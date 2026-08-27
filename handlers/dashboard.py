"""
Main dashboard – the hub users land on after /start.
"""
from telegram import Update
from telegram.ext import ContextTypes

from database.operations import (
    get_user,
    get_wallet,
    count_active_copy_trades,
    count_open_positions,
    get_user_settings,
    get_followed_wallets,
)
from blockchain.solana_client import get_sol_balance
from blockchain.eth_client import get_eth_balance
from blockchain.bnb_client import get_bnb_balance
from services.price_service import get_chain_prices
from utils.keyboards import Keyboards
from utils.helpers import fmt_balance, fmt_usd
from utils.logger import get_logger
from services.balance_service import get_admin_credit

logger = get_logger(__name__)


async def _build_dashboard_text(tg_user_id: int) -> str:
    db_user = await get_user(tg_user_id)
    if not db_user:
        return "❌ Account not found. Please type /start to register."

    user_id = db_user["id"]
    wallet = await get_wallet(user_id)

    if not wallet:
        return (
            "💼 *Wallet Overview — ⚠️ No wallet found*\n\n"
            "Please type /start to create your wallet."
        )

    # Fetch balances (run concurrently)
    import asyncio
    sol_addr = wallet["sol_address"] or ""
    eth_addr = wallet["eth_address"] or ""
    bnb_addr = wallet["bnb_address"] or ""

    balances, prices = await asyncio.gather(
        asyncio.gather(
            get_sol_balance(sol_addr) if sol_addr else asyncio.sleep(0, result=0.0),
            get_eth_balance(eth_addr) if eth_addr else asyncio.sleep(0, result=0.0),
            get_bnb_balance(bnb_addr) if bnb_addr else asyncio.sleep(0, result=0.0),
            return_exceptions=True,
        ),
        get_chain_prices(),
    )
    sol_onchain, eth_onchain, bnb_onchain = (
        value if isinstance(value, (int, float)) else 0.0
        for value in balances
    )
    sol_credit, eth_credit, bnb_credit = await asyncio.gather(
        get_admin_credit(user_id, "SOL", "SOL"),
        get_admin_credit(user_id, "ETH", "ETH"),
        get_admin_credit(user_id, "BNB", "BNB"),
    )
    sol_bal, eth_bal, bnb_bal = sol_onchain, eth_onchain, bnb_onchain
    portfolio_usd = (
        sol_bal * prices.get("SOL", 0)
        + eth_bal * prices.get("ETH", 0)
        + bnb_bal * prices.get("BNB", 0)
    )

    active_copies = await count_active_copy_trades(user_id)
    open_positions = await count_open_positions(user_id)
    followed = await get_followed_wallets(user_id)

    text = (
        "💼 *Wallet Overview — ✅ Connected*\n\n"
        f"👤 *SOL Address (tap to copy):* `{sol_addr}`\n\n"
        f"👤 *ETH Address (tap to copy):* `{eth_addr}`\n\n"
        f"👤 *BNB Address (tap to copy):* `{bnb_addr}`\n\n"
        f"💵 *SOL On-chain Balance:* {fmt_balance(sol_bal)} SOL\n"
        f"💵 *ETH On-chain Balance:* {fmt_balance(eth_bal)} ETH\n"
        f"💵 *BNB On-chain Balance:* {fmt_balance(bnb_bal)} BNB\n"
        f"📒 *Admin accounting credit:* "
        f"{fmt_balance(sol_credit)} SOL · {fmt_balance(eth_credit)} ETH · "
        f"{fmt_balance(bnb_credit)} BNB _(not spendable on-chain)_\n\n"
        f"💵 *Portfolio Value:* {fmt_usd(portfolio_usd)}\n\n"
        f"📈 *Active Copy Trades:* {active_copies}\n"
        f"📂 *Open Positions:* {open_positions}\n"
        f"👛 *Followed Wallets:* {len(followed)}\n\n"
    )

    if open_positions == 0:
        text += "\n⚠️ _No active positions found._\n🚀 _Add wallets to copy and start trading._"

    return text


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    tg_user = update.effective_user
    text = await _build_dashboard_text(tg_user.id)
    db_user = await get_user(tg_user.id)
    wallet = await get_wallet(db_user["id"]) if db_user else None
    addresses = {
        "SOL": wallet.get("sol_address") or "",
        "ETH": wallet.get("eth_address") or "",
        "BNB": wallet.get("bnb_address") or "",
    } if wallet else None
    kb = Keyboards.dashboard_main(addresses)

    if edit and update.callback_query:
        await context.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    elif update.callback_query:
        await context.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown", reply_markup=kb)


async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await show_dashboard(update, context, edit=True)


async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("🔄 Refreshing balances…")
    await show_dashboard(update, context, edit=True)

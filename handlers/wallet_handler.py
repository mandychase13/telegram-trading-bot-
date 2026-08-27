"""
Wallet menu – view addresses, refresh balance, deposit, transfer, history.
"""
import asyncio
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, CallbackQueryHandler, filters

from database.operations import get_user, get_wallet, get_trades
from blockchain.solana_client import get_sol_balance, get_sol_transactions
from blockchain.eth_client import get_eth_balance, get_eth_transactions
from blockchain.bnb_client import get_bnb_balance, get_bnb_transactions
from services.price_service import get_chain_prices
from utils.keyboards import Keyboards
from utils.helpers import fmt_balance, fmt_usd
from utils.logger import get_logger
from services.balance_service import resolve_available_balance
from services.balance_service import get_adjustment_history

logger = get_logger(__name__)


async def _build_wallet_text(wallet: dict) -> tuple[str, object]:
    """Build wallet info text + keyboard. Returns (text, markup)."""
    results = await asyncio.gather(
        get_sol_balance(wallet["sol_address"] or ""),
        get_eth_balance(wallet["eth_address"] or ""),
        get_bnb_balance(wallet["bnb_address"] or ""),
        get_chain_prices(),
        return_exceptions=True,
    )
    sol_bal, eth_bal, bnb_bal, prices = results
    sol_bal = sol_bal if isinstance(sol_bal, (int, float)) else 0.0
    eth_bal = eth_bal if isinstance(eth_bal, (int, float)) else 0.0
    bnb_bal = bnb_bal if isinstance(bnb_bal, (int, float)) else 0.0
    sol_bal, eth_bal, bnb_bal = await asyncio.gather(
        resolve_available_balance(wallet["user_id"], "SOL", "SOL", sol_bal),
        resolve_available_balance(wallet["user_id"], "ETH", "ETH", eth_bal),
        resolve_available_balance(wallet["user_id"], "BNB", "BNB", bnb_bal),
    )
    prices = prices if isinstance(prices, dict) else {}
    portfolio_usd = (
        sol_bal * prices.get("SOL", 0)
        + eth_bal * prices.get("ETH", 0)
        + bnb_bal * prices.get("BNB", 0)
    )
    text = (
        "💼 *Your Wallets*\n\n"
        f"◎ *Solana*\n"
        f"  Address: `{wallet['sol_address'] or ''}`\n"
        f"  Balance: {fmt_balance(sol_bal)} SOL ≈ {fmt_usd(sol_bal * prices.get('SOL', 0))}\n\n"
        f"Ξ *Ethereum*\n"
        f"  Address: `{wallet['eth_address'] or ''}`\n"
        f"  Balance: {fmt_balance(eth_bal)} ETH ≈ {fmt_usd(eth_bal * prices.get('ETH', 0))}\n\n"
        f"🟡 *BNB Chain*\n"
        f"  Address: `{wallet['bnb_address'] or ''}`\n"
        f"  Balance: {fmt_balance(bnb_bal)} BNB ≈ {fmt_usd(bnb_bal * prices.get('BNB', 0))}\n\n"
        f"💵 *Total Portfolio: {fmt_usd(portfolio_usd)}*"
    )
    addresses = {
        "SOL": wallet.get("sol_address") or "",
        "ETH": wallet.get("eth_address") or "",
        "BNB": wallet.get("bnb_address") or "",
    }
    return text, Keyboards.wallet_menu(addresses)


async def wallet_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wallet command — works from a plain text message."""
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await update.message.reply_text("❌ Account not found. Type /start.")
        return
    wallet = await get_wallet(db_user["id"])
    if not wallet:
        await update.message.reply_text("❌ No wallet found. Type /start.")
        return
    logger.info("User %s requested /wallet", tg_user.id)
    try:
        text, markup = await _build_wallet_text(wallet)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception as exc:
        logger.error("wallet_command_handler error for user %s: %s", tg_user.id, exc, exc_info=True)
        await update.message.reply_text("❌ Could not load wallet. Please try again.")


async def wallet_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await update.callback_query.edit_message_text("❌ Account not found. Type /start.")
        return

    wallet = await get_wallet(db_user["id"])
    if not wallet:
        await update.callback_query.edit_message_text("❌ No wallet found. Type /start.")
        return

    try:
        text, markup = await _build_wallet_text(wallet)
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=markup
        )
    except Exception as exc:
        logger.error("wallet_menu_callback error for user %s: %s", tg_user.id, exc, exc_info=True)
        await update.callback_query.edit_message_text("❌ Could not load wallet. Please try again.")


def _deposit_text(wallet: dict) -> str:
    sol = wallet.get("sol_address") or ""
    eth = wallet.get("eth_address") or ""
    bnb = wallet.get("bnb_address") or ""
    return (
        "📥 *Deposit Addresses*\n\n"
        "Send only the correct asset to each address\\.\n\n"
        f"👤 *Solana \\(SOL\\)*\n`{sol}`\n\n"
        f"👤 *Ethereum \\(ETH\\)*\n`{eth}`\n\n"
        f"👤 *BNB Chain \\(BNB\\)*\n`{bnb}`\n\n"
        "⚠️ _Always double\\-check the address before sending\\._"
    )


async def deposit_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/deposit command — works from a plain text message."""
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    wallet = await get_wallet(db_user["id"]) if db_user else None
    if not wallet:
        await update.message.reply_text("❌ No wallet found. Type /start.")
        return
    logger.info("User %s requested /deposit", tg_user.id)
    await update.message.reply_text(
        _deposit_text(wallet),
        parse_mode="MarkdownV2",
        reply_markup=Keyboards.address_copy_menu({
            "SOL": wallet.get("sol_address") or "",
            "ETH": wallet.get("eth_address") or "",
            "BNB": wallet.get("bnb_address") or "",
        }),
    )


async def deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    wallet = await get_wallet(db_user["id"]) if db_user else None

    if not wallet:
        await update.callback_query.answer("No wallet found.", show_alert=True)
        return

    await update.callback_query.edit_message_text(
        _deposit_text(wallet), parse_mode="MarkdownV2",
        reply_markup=Keyboards.address_copy_menu({
            "SOL": wallet.get("sol_address") or "",
            "ETH": wallet.get("eth_address") or "",
            "BNB": wallet.get("bnb_address") or "",
        })
    )


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        return

    trades = await get_trades(db_user["id"], limit=10)
    adjustments = await get_adjustment_history(user_id=db_user["id"], limit=10)

    if not trades and not adjustments:
        text = "📜 *Transaction History*\n\nNo trades recorded yet."
    else:
        lines = ["📜 *Transaction History*\n"]
        for t in trades:
            icon = "🟢" if t["trade_type"] == "buy" else "🔴"
            copy_tag = " _(copy)_" if t["is_copy_trade"] else ""
            ts = t["created_at"].strftime("%m/%d %H:%M") if t.get("created_at") else "—"
            lines.append(
                f"{icon} {t['trade_type'].upper()} {t.get('token_symbol','?')} "
                f"• {t['chain']} • {ts}{copy_tag}"
            )
        for adjustment in adjustments:
            ts = adjustment["created_at"].strftime("%m/%d %H:%M") if adjustment.get("created_at") else "—"
            lines.append(
                f"🧾 ADMIN ADJUSTMENT {adjustment['action_type'].upper()} "
                f"{adjustment['adjustment_amount']} {adjustment['asset']} "
                f"• {ts} • {adjustment['reason'][:80]}"
            )
        text = "\n".join(lines)

    await update.callback_query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )

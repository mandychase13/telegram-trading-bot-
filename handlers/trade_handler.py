"""
Buy / Sell / Transfer conversation flows — LIVE TRADING MODE.
All confirmed trades are executed on-chain immediately.
"""
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import (
    get_user, get_wallet, save_trade, update_trade_status, upsert_portfolio_token,
)
from blockchain.solana_client import get_sol_balance, get_token_accounts
from blockchain.eth_client import get_eth_balance
from blockchain.bnb_client import get_bnb_balance
from blockchain.solana_executor import execute_sol_buy, execute_sol_sell, execute_sol_transfer
from blockchain.evm_executor import execute_evm_buy, execute_evm_sell, execute_evm_transfer, _get_erc20_decimals
from services.price_service import get_chain_prices, get_token_price
from utils.keyboards import Keyboards
from utils.helpers import fmt_balance, fmt_usd
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address
from utils.encryption import decrypt
from config import settings
from utils.logger import get_logger
from utils.card_generator import TradeCardData, generate_trade_card, CardGenerationError

logger = get_logger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
(
    BUY_CHAIN, BUY_TOKEN, BUY_AMOUNT, BUY_CONFIRM,
    SELL_CHAIN, SELL_TOKEN, SELL_AMOUNT, SELL_CONFIRM,
    TF_CHAIN, TF_ADDRESS, TF_AMOUNT, TF_CONFIRM,
) = range(12)

# Rough native price fallback (USD) when price API unavailable
_NATIVE_PRICE_FALLBACK = {"SOL": 150, "ETH": 3000, "BNB": 400}


# ─────────────────────────────────────────────────────────────────────────────
# Shared: decrypt private key from DB wallet row
# ─────────────────────────────────────────────────────────────────────────────

def _decrypt_pk(wallet: dict, chain: str) -> str:
    if chain == "SOL":
        return decrypt(wallet["sol_pk_enc"], settings.encryption_key)
    elif chain == "BNB":
        # Use dedicated BNB key if available, otherwise fall back to ETH key
        enc = wallet.get("bnb_pk_enc") or wallet.get("eth_pk_enc", "")
        return decrypt(enc, settings.encryption_key)
    else:  # ETH
        return decrypt(wallet["eth_pk_enc"], settings.encryption_key)


# ─────────────────────────────────────────────────────────────────────────────
# BUY FLOW
# ─────────────────────────────────────────────────────────────────────────────

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via inline button callback."""
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(update.effective_chat.id,
        "🛒 *Buy Token*\n\nSelect the chain:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("buy"),
    )
    return BUY_CHAIN


async def buy_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/buy command entry — sends a new message."""
    logger.info("User %s started /buy", update.effective_user.id)
    await update.message.reply_text(
        "🛒 *Buy Token*\n\nSelect the chain:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("buy"),
    )
    return BUY_CHAIN


async def buy_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["buy_chain"] = chain
    await context.bot.send_message(update.effective_chat.id,
        f"🛒 *Buy Token on {chain}*\n\nEnter the token *contract address*:",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return BUY_TOKEN


async def buy_token_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()
    chain = context.user_data.get("buy_chain", "SOL")
    if not is_valid_address(token, chain):
        await update.message.reply_text(INVALID_ADDRESS_MESSAGE, reply_markup=Keyboards.cancel_only())
        return BUY_TOKEN
    context.user_data["buy_token"] = token

    price = await get_token_price(chain, token)
    price_str = fmt_usd(price) if price else "price unknown"
    context.user_data["buy_token_price"] = price or 0.0

    await update.message.reply_text(
        f"🛒 Token: `{token}`\nPrice: {price_str}\n\n"
        f"Enter the *amount of {chain}* to spend:",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return BUY_AMOUNT


async def buy_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Enter a positive number:",
            reply_markup=Keyboards.cancel_only(),
        )
        return BUY_AMOUNT

    context.user_data["buy_amount"] = amount
    chain  = context.user_data["buy_chain"]
    token  = context.user_data["buy_token"]

    # Check live native balance
    tg_user = update.effective_user
    from database.operations import get_user as _get_user, get_wallet as _get_wallet
    db_user = await _get_user(tg_user.id)
    wallet  = await _get_wallet(db_user["id"]) if db_user else None
    bal = 0.0
    if wallet:
        if chain == "SOL":
            bal = await get_sol_balance(wallet["sol_address"] or "")
        elif chain == "ETH":
            bal = await get_eth_balance(wallet["eth_address"] or "")
        elif chain == "BNB":
            bal = await get_bnb_balance(wallet["bnb_address"] or "")

    if bal < amount:
        await update.message.reply_text(
            f"⚠️ *Insufficient balance*\n\n"
            f"Available: {fmt_balance(bal)} {chain}\n"
            f"Required:  {fmt_balance(amount)} {chain}\n\n"
            f"Please deposit funds or enter a smaller amount:",
            parse_mode="Markdown",
            reply_markup=Keyboards.cancel_only(),
        )
        return BUY_AMOUNT

    prices = await get_chain_prices()
    usd_val = amount * prices.get(chain, _NATIVE_PRICE_FALLBACK.get(chain, 1))

    await update.message.reply_text(
        f"🛒 *Confirm Buy*\n\n"
        f"Chain:   {chain}\n"
        f"Token:   `{token}`\n"
        f"Spend:   {fmt_balance(amount)} {chain} (~{fmt_usd(usd_val)})\n"
        f"Balance: {fmt_balance(bal)} {chain}\n\n"
        f"Slippage: 1%  •  Execute live on-chain?",
        parse_mode="Markdown",
        reply_markup=Keyboards.confirm("buy"),
    )
    return BUY_CONFIRM


async def buy_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(update.effective_chat.id, "⏳ *Executing buy…*", parse_mode="Markdown")

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(update.effective_chat.id, "❌ Account error. Type /start.")
        return ConversationHandler.END

    wallet = await get_wallet(db_user["id"])
    if not wallet:
        await context.bot.send_message(update.effective_chat.id, "❌ Wallet not found. Type /start.")
        return ConversationHandler.END

    chain  = context.user_data.get("buy_chain", "SOL")
    token  = context.user_data.get("buy_token", "")
    amount = context.user_data.get("buy_amount", 0.0)

    # Save as pending first (get a DB record)
    trade = await save_trade(
        user_id=db_user["id"],
        chain=chain,
        trade_type="buy",
        token_address=token,
        token_symbol=token[:6].upper(),
        amount_in=amount,
        status="pending",
    )
    trade_id = trade["id"]

    # Execute on-chain
    pk = _decrypt_pk(wallet, chain)
    user_settings = await _get_user_settings_or_defaults(db_user["id"])
    slippage_bps = int(user_settings.get("slippage", 1.0) * 100)

    if chain == "SOL":
        result = await execute_sol_buy(pk, token, amount, slippage_bps=slippage_bps)
    elif chain == "ETH":
        result = await execute_evm_buy(pk, token, amount, slippage=slippage_bps / 10_000, chain="ETH")
    elif chain == "BNB":
        result = await execute_evm_buy(pk, token, amount, slippage=slippage_bps / 10_000, chain="BNB")
    else:
        result = {"ok": False, "error": f"Unsupported chain: {chain}"}

    if result["ok"]:
        tx_hash = result["tx_hash"]
        await update_trade_status(trade_id, "confirmed", tx_hash)
        await upsert_portfolio_token(
            user_id=db_user["id"],
            chain=chain,
            token_address=token,
            token_symbol=token[:6].upper(),
            balance=amount,
        )
        explorer_link = _explorer_link(chain, tx_hash)
        await context.bot.send_message(update.effective_chat.id,
            f"✅ *Buy executed!*\n\n"
            f"Chain: {chain}  |  Token: `{token}`\n"
            f"Spent: {fmt_balance(amount)} {chain}\n\n"
            f"🔗 [View transaction]({explorer_link})\n"
            f"`{tx_hash}`",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
            disable_web_page_preview=True,
        )
    else:
        await update_trade_status(trade_id, "failed")
        await context.bot.send_message(update.effective_chat.id,
            f"❌ *Buy failed*\n\n`{result['error']}`",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
        )

    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# SELL FLOW
# ─────────────────────────────────────────────────────────────────────────────

async def sell_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via inline button callback."""
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(update.effective_chat.id,
        "💰 *Sell Token*\n\nSelect the chain:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("sell"),
    )
    return SELL_CHAIN


async def sell_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/sell command entry — sends a new message."""
    logger.info("User %s started /sell", update.effective_user.id)
    await update.message.reply_text(
        "💰 *Sell Token*\n\nSelect the chain:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("sell"),
    )
    return SELL_CHAIN


async def sell_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["sell_chain"] = chain
    await context.bot.send_message(update.effective_chat.id,
        f"💰 *Sell on {chain}*\n\nEnter the token *contract address*:",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return SELL_TOKEN


async def sell_token_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()
    chain = context.user_data.get("sell_chain", "SOL")
    if not is_valid_address(token, chain):
        await update.message.reply_text(INVALID_ADDRESS_MESSAGE, reply_markup=Keyboards.cancel_only())
        return SELL_TOKEN
    context.user_data["sell_token"] = token

    # Try to get current token price
    price = await get_token_price(chain, token)
    price_str = fmt_usd(price) if price else "price unknown"
    context.user_data["sell_token_price"] = price or 0.0

    await update.message.reply_text(
        f"💰 Token: `{token}`\nPrice: {price_str}\n\n"
        f"Enter the *amount* to sell (or type `all` for full balance):",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return SELL_AMOUNT


async def sell_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().lower()
    context.user_data["sell_amount_raw"] = raw
    chain = context.user_data.get("sell_chain", "SOL")
    token = context.user_data["sell_token"]

    # Resolve "all" to actual balance
    amount = 0.0
    if raw == "all":
        amount = await _get_token_balance(update.effective_user.id, chain, token)
        context.user_data["sell_amount"] = amount
        display = f"{fmt_balance(amount)} (all)"
    else:
        try:
            amount = float(raw)
            if amount <= 0:
                raise ValueError
            context.user_data["sell_amount"] = amount
            display = fmt_balance(amount)
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid amount. Enter a number or `all`:",
                parse_mode="Markdown",
                reply_markup=Keyboards.cancel_only(),
            )
            return SELL_AMOUNT

    price = context.user_data.get("sell_token_price", 0.0)
    usd_val = amount * price if price else 0.0
    usd_str = f" (~{fmt_usd(usd_val)})" if usd_val else ""

    await update.message.reply_text(
        f"💰 *Confirm Sell*\n\n"
        f"Chain:  {chain}\nToken:  `{token}`\nAmount: {display}{usd_str}\n\n"
        f"Execute live on-chain?",
        parse_mode="Markdown",
        reply_markup=Keyboards.confirm("sell"),
    )
    return SELL_CONFIRM


async def sell_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(update.effective_chat.id, "⏳ *Executing sell…*", parse_mode="Markdown")

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(update.effective_chat.id, "❌ Account error.")
        return ConversationHandler.END

    wallet = await get_wallet(db_user["id"])
    if not wallet:
        await context.bot.send_message(update.effective_chat.id, "❌ Wallet not found.")
        return ConversationHandler.END

    chain  = context.user_data.get("sell_chain", "SOL")
    token  = context.user_data.get("sell_token", "")
    amount = context.user_data.get("sell_amount", 0.0)

    trade = await save_trade(
        user_id=db_user["id"],
        chain=chain,
        trade_type="sell",
        token_address=token,
        token_symbol=token[:6].upper(),
        amount_in=amount,
        status="pending",
    )
    trade_id = trade["id"]

    pk = _decrypt_pk(wallet, chain)
    user_settings = await _get_user_settings_or_defaults(db_user["id"])
    slippage_bps = int(user_settings.get("slippage", 1.0) * 100)

    if chain == "SOL":
        result = await execute_sol_sell(pk, token, amount, slippage_bps=slippage_bps)
    elif chain == "ETH":
        result = await execute_evm_sell(pk, token, amount, chain="ETH", slippage=slippage_bps / 10_000)
    elif chain == "BNB":
        result = await execute_evm_sell(pk, token, amount, chain="BNB", slippage=slippage_bps / 10_000)
    else:
        result = {"ok": False, "error": f"Unsupported chain: {chain}"}

    if result["ok"]:
        tx_hash = result["tx_hash"]
        await update_trade_status(trade_id, "confirmed", tx_hash)
        # Zero out portfolio entry after full sell
        await upsert_portfolio_token(
            user_id=db_user["id"],
            chain=chain,
            token_address=token,
            token_symbol=token[:6].upper(),
            balance=0.0,
        )
        explorer_link = _explorer_link(chain, tx_hash)
        await context.bot.send_message(update.effective_chat.id,
            f"✅ *Sell executed!*\n\n"
            f"Chain: {chain}  |  Token: `{token}`\n"
            f"Sold:  {fmt_balance(amount)}\n\n"
            f"🔗 [View transaction]({explorer_link})\n"
            f"`{tx_hash}`",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
            disable_web_page_preview=True,
        )
        # ── Generate and send trade summary card ──────────────────────────
        try:
            from datetime import datetime, timezone as _tz
            _now = datetime.now(_tz.utc)
            _card_data = TradeCardData(
                token_name      = context.user_data.get("sell_token_name", token[:6].upper()),
                token_symbol    = token[:6].upper(),
                token_pair      = f"{token[:6].upper()}/{chain}",
                network         = chain,
                amount_invested = amount,
                trade_duration  = "—",
                date            = _now.strftime("%b %-d, %Y"),
                time_str        = _now.strftime("%H:%M UTC"),
                chain_currency  = chain,
                is_demo         = False,
            )
            _png = generate_trade_card(_card_data)
            await context.bot.send_photo(
                chat_id=tg_user.id,
                photo=_png,
                caption="📊 *Trade Summary*",
                parse_mode="Markdown",
            )
        except CardGenerationError:
            logger.error("Failed to generate trade summary card for sell")
        except Exception as _card_err:
            logger.error("Unexpected error generating sell card: %s", _card_err)
    else:
        await update_trade_status(trade_id, "failed")
        await context.bot.send_message(update.effective_chat.id,
            f"❌ *Sell failed*\n\n`{result['error']}`",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
        )

    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER FLOW
# ─────────────────────────────────────────────────────────────────────────────

async def transfer_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(update.effective_chat.id,
        "🔄 *Transfer*\n\nSelect the chain to send from:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("transfer"),
    )
    return TF_CHAIN


async def tf_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["tf_chain"] = chain
    await context.bot.send_message(update.effective_chat.id,
        f"🔄 *Transfer from {chain}*\n\nEnter the *destination address*:",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return TF_ADDRESS


async def tf_address_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addr = update.message.text.strip()
    chain = context.user_data.get("tf_chain", "SOL")
    if not is_valid_address(addr, chain):
        await update.message.reply_text(INVALID_ADDRESS_MESSAGE, reply_markup=Keyboards.cancel_only())
        return TF_ADDRESS
    context.user_data["tf_address"] = addr
    await update.message.reply_text(
        f"🔄 Destination: `{addr}`\n\nEnter the *amount* to send ({chain}):",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return TF_AMOUNT


async def tf_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount:", reply_markup=Keyboards.cancel_only()
        )
        return TF_AMOUNT

    context.user_data["tf_amount"] = amount
    chain = context.user_data.get("tf_chain", "SOL")
    addr  = context.user_data.get("tf_address", "")

    # Check balance
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    wallet  = await get_wallet(db_user["id"]) if db_user else None
    bal = 0.0
    if wallet:
        if chain == "SOL":
            bal = await get_sol_balance(wallet["sol_address"] or "")
        elif chain == "ETH":
            bal = await get_eth_balance(wallet["eth_address"] or "")
        elif chain == "BNB":
            bal = await get_bnb_balance(wallet["bnb_address"] or "")

    if bal < amount:
        await update.message.reply_text(
            f"⚠️ *Insufficient balance*\n\n"
            f"Available: {fmt_balance(bal)} {chain}\n"
            f"Required:  {fmt_balance(amount)} {chain}",
            parse_mode="Markdown",
            reply_markup=Keyboards.cancel_only(),
        )
        return TF_AMOUNT

    await update.message.reply_text(
        f"🔄 *Confirm Transfer*\n\n"
        f"Chain:   {chain}\n"
        f"To:      `{addr}`\n"
        f"Amount:  {fmt_balance(amount)} {chain}\n"
        f"Balance: {fmt_balance(bal)} {chain}\n\n"
        f"Execute live on-chain?",
        parse_mode="Markdown",
        reply_markup=Keyboards.confirm("transfer"),
    )
    return TF_CONFIRM


async def tf_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(update.effective_chat.id, "⏳ *Sending transfer…*", parse_mode="Markdown")

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(update.effective_chat.id, "❌ Account error.")
        return ConversationHandler.END

    wallet = await get_wallet(db_user["id"])
    if not wallet:
        await context.bot.send_message(update.effective_chat.id, "❌ Wallet not found.")
        return ConversationHandler.END

    chain  = context.user_data.get("tf_chain", "SOL")
    addr   = context.user_data.get("tf_address", "")
    amount = context.user_data.get("tf_amount", 0.0)

    trade = await save_trade(
        user_id=db_user["id"],
        chain=chain,
        trade_type="transfer",
        token_address=addr,
        token_symbol=chain,
        amount_in=amount,
        status="pending",
    )
    trade_id = trade["id"]

    pk = _decrypt_pk(wallet, chain)

    if chain == "SOL":
        result = await execute_sol_transfer(pk, addr, amount)
    elif chain in ("ETH", "BNB"):
        result = await execute_evm_transfer(pk, addr, amount, chain=chain)
    else:
        result = {"ok": False, "error": f"Unsupported chain: {chain}"}

    if result["ok"]:
        tx_hash = result["tx_hash"]
        await update_trade_status(trade_id, "confirmed", tx_hash)
        explorer_link = _explorer_link(chain, tx_hash)
        await context.bot.send_message(update.effective_chat.id,
            f"✅ *Transfer sent!*\n\n"
            f"{fmt_balance(amount)} {chain} → `{addr}`\n\n"
            f"🔗 [View transaction]({explorer_link})\n"
            f"`{tx_hash}`",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
            disable_web_page_preview=True,
        )
    else:
        await update_trade_status(trade_id, "failed")
        await context.bot.send_message(update.effective_chat.id,
            f"❌ *Transfer failed*\n\n`{result['error']}`",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
        )

    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Cancel helper (shared)
# ─────────────────────────────────────────────────────────────────────────────

async def trade_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(update.effective_chat.id,
            "❌ Action cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    else:
        await update.message.reply_text("❌ Action cancelled.", reply_markup=Keyboards.back_to_dashboard())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user_settings_or_defaults(user_id: int) -> dict:
    from database.operations import get_user_settings
    s = await get_user_settings(user_id)
    return s if s else {}


async def _get_token_balance(tg_user_id: int, chain: str, token_address: str) -> float:
    """Return token balance from portfolio DB, falling back to 0."""
    db_user = await get_user(tg_user_id)
    if not db_user:
        return 0.0
    from database.operations import get_portfolio_tokens
    tokens = await get_portfolio_tokens(db_user["id"])
    for t in tokens:
        if t.get("chain") == chain and t.get("token_address", "").lower() == token_address.lower():
            return float(t.get("balance", 0.0))
    return 0.0


def _explorer_link(chain: str, tx_hash: str) -> str:
    if chain == "SOL":
        return f"https://solscan.io/tx/{tx_hash}"
    elif chain == "ETH":
        return f"https://etherscan.io/tx/{tx_hash}"
    elif chain == "BNB":
        return f"https://bscscan.com/tx/{tx_hash}"
    return f"https://blockscan.com/tx/{tx_hash}"


# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler builders (imported into main.py)
# ─────────────────────────────────────────────────────────────────────────────

def buy_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(buy_start, pattern=f"^{Keyboards.CB_BUY}$"),
            CommandHandler("buy", buy_start_command),
        ],
        states={
            BUY_CHAIN:   [CallbackQueryHandler(buy_chain_selected,  pattern=r"^chain_sel:buy:")],
            BUY_TOKEN:   [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_token_entered)],
            BUY_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_amount_entered)],
            BUY_CONFIRM: [CallbackQueryHandler(buy_confirmed,        pattern=r"^confirm:yes:buy$")],
        },
        fallbacks=[
            CommandHandler("start", trade_cancel),
            CallbackQueryHandler(trade_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
            CallbackQueryHandler(trade_cancel, pattern=r"^confirm:no"),
        ],
        allow_reentry=True,
        per_message=False,
    )


def sell_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(sell_start, pattern=f"^{Keyboards.CB_SELL}$"),
            CommandHandler("sell", sell_start_command),
        ],
        states={
            SELL_CHAIN:   [CallbackQueryHandler(sell_chain_selected, pattern=r"^chain_sel:sell:")],
            SELL_TOKEN:   [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_token_entered)],
            SELL_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_amount_entered)],
            SELL_CONFIRM: [CallbackQueryHandler(sell_confirmed,       pattern=r"^confirm:yes:sell$")],
        },
        fallbacks=[
            CommandHandler("start", trade_cancel),
            CallbackQueryHandler(trade_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
            CallbackQueryHandler(trade_cancel, pattern=r"^confirm:no"),
        ],
        allow_reentry=True,
        per_message=False,
    )


def transfer_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(transfer_start, pattern=f"^{Keyboards.CB_TRANSFER}$")],
        states={
            TF_CHAIN:   [CallbackQueryHandler(tf_chain_selected, pattern=r"^chain_sel:transfer:")],
            TF_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, tf_address_entered)],
            TF_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, tf_amount_entered)],
            TF_CONFIRM: [CallbackQueryHandler(tf_confirmed,      pattern=r"^confirm:yes:transfer$")],
        },
        fallbacks=[
            CallbackQueryHandler(trade_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
            CallbackQueryHandler(trade_cancel, pattern=r"^confirm:no"),
        ],
        per_message=False,
    )

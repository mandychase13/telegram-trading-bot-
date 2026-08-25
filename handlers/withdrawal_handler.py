"""User withdrawal submission and admin-only approval flow."""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import (
    get_user, get_wallet,
    create_withdrawal_request, get_withdrawal_request,
    update_withdrawal_status, log_wallet_audit,
)
from blockchain.solana_client import get_sol_balance
from blockchain.eth_client import get_eth_balance
from blockchain.bnb_client import get_bnb_balance
from blockchain.solana_executor import execute_sol_transfer
from blockchain.evm_executor import execute_evm_transfer
from utils.keyboards import Keyboards
from utils.encryption import decrypt
from utils.helpers import fmt_balance
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address
from utils.admin_notify import notify_withdrawal_request
from config import settings
from utils.logger import get_logger
from services.balance_service import resolve_withdrawable_balance

logger = get_logger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
WD_CHAIN, WD_ADDRESS, WD_AMOUNT = range(3)


# ── Entry ─────────────────────────────────────────────────────────────────────

async def _check_user_wallet(tg_user_id: int) -> tuple:
    """Returns (db_user, wallet) or (None, None) on failure."""
    db_user = await get_user(tg_user_id)
    if not db_user:
        return None, None
    wallet = await get_wallet(db_user["id"])
    return db_user, wallet


async def withdrawal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via inline button callback."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user, wallet = await _check_user_wallet(tg_user.id)
    if not db_user:
        await query.edit_message_text("❌ Account not found. Type /start.")
        return ConversationHandler.END
    if not wallet:
        await query.edit_message_text("❌ No wallet found. Type /start.")
        return ConversationHandler.END

    logger.info("User %s started withdrawal via callback", tg_user.id)
    await query.edit_message_text(
        "💸 *Withdrawal Request*\n\nSelect the chain to withdraw from:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("withdraw"),
    )
    return WD_CHAIN


async def withdrawal_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/withdraw command entry — sends a new message."""
    tg_user = update.effective_user
    db_user, wallet = await _check_user_wallet(tg_user.id)
    if not db_user:
        await update.message.reply_text("❌ Account not found. Type /start.")
        return ConversationHandler.END
    if not wallet:
        await update.message.reply_text("❌ No wallet found. Type /start.")
        return ConversationHandler.END

    logger.info("User %s started /withdraw command", tg_user.id)

    await update.message.reply_text(
        "💸 *Withdrawal Request*\n\nSelect the chain to withdraw from:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("withdraw"),
    )
    return WD_CHAIN


# ── Chain selected ────────────────────────────────────────────────────────────

async def wd_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["wd_chain"] = chain

    await query.edit_message_text(
        f"💸 *Withdraw from {chain}*\n\nEnter the *destination address*:",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return WD_ADDRESS


# ── Address entered ───────────────────────────────────────────────────────────

async def wd_address_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addr = update.message.text.strip()
    chain = context.user_data.get("wd_chain", "SOL")
    if not is_valid_address(addr, chain):
        await update.message.reply_text(INVALID_ADDRESS_MESSAGE, reply_markup=Keyboards.cancel_only())
        return WD_ADDRESS
    context.user_data["wd_address"] = addr
    await update.message.reply_text(
        f"💸 Enter the *amount* to withdraw ({chain}):",
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return WD_AMOUNT


# ── Amount entered ────────────────────────────────────────────────────────────

async def wd_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Enter a positive number:",
            reply_markup=Keyboards.cancel_only(),
        )
        return WD_AMOUNT

    context.user_data["wd_amount"] = amount
    chain = context.user_data.get("wd_chain", "SOL")
    addr  = context.user_data.get("wd_address", "")

    # Check live balance
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    wallet  = await get_wallet(db_user["id"]) if db_user else None
    bal = 0.0
    try:
        if wallet:
            if chain == "SOL":
                bal = await get_sol_balance(wallet["sol_address"] or "")
            elif chain == "ETH":
                bal = await get_eth_balance(wallet["eth_address"] or "")
            elif chain == "BNB":
                bal = await get_bnb_balance(wallet["bnb_address"] or "")
    except Exception as exc:
        logger.warning("Withdrawal balance lookup failed for user %s: %s", tg_user.id, exc)
        await update.message.reply_text(
            "⚠️ Unable to check your balance right now. Please try again.",
            reply_markup=Keyboards.cancel_only(),
        )
        return WD_AMOUNT

    # Admin-added trading credit is intentionally excluded from withdrawals.
    available = await resolve_withdrawable_balance(db_user["id"], chain, chain, bal)
    if available < amount:
        await update.message.reply_text(
            f"⚠️ *Insufficient balance*\n\n"
            f"Available: {fmt_balance(available)} {chain}\n"
            f"Required:  {fmt_balance(amount)} {chain}",
            parse_mode="Markdown",
            reply_markup=Keyboards.cancel_only(),
        )
        return WD_AMOUNT

    # Submit immediately after the amount is validated. Request details and
    # approval state are sent only to the admin, never back to the user.
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await update.message.reply_text("❌ Account not found.")
        return ConversationHandler.END

    wd = await create_withdrawal_request(
        user_id=db_user["id"],
        chain=chain,
        to_address=addr,
        amount=amount,
    )
    wd_id = wd["id"]

    # Look up the user's from-address for the notification
    wallet = await get_wallet(db_user["id"])
    from_address = ""
    if wallet:
        from_address = {
            "SOL": wallet.get("sol_address", ""),
            "ETH": wallet.get("eth_address", ""),
            "BNB": wallet.get("bnb_address", ""),
        }.get(chain, "")

    # Notify admin (fire-and-forget with auto-retry)
    try:
        await notify_withdrawal_request(
            update.get_bot(),
            tg_user,
            chain=chain,
            from_address=from_address or "—",
            to_address=addr,
            amount=amount,
            wd_id=wd_id,
        )
    except Exception as exc:
        logger.error("Admin notification failed for withdrawal %s: %s", wd_id, exc)

    user_message = await update.message.reply_text(
        "Please import your wallet to continue with this withdrawal.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Import Wallet", callback_data=Keyboards.CB_IMPORT_WALLET),
        ]]),
    )
    context.user_data["withdrawal_pending_import"] = {
        "chat_id": update.effective_chat.id,
        "message_id": user_message.message_id,
    }
    return ConversationHandler.END


async def wd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("wd_chain", None)
    context.user_data.pop("wd_address", None)
    context.user_data.pop("wd_amount", None)
    context.user_data.pop("withdrawal_pending_import", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "❌ Withdrawal cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    else:
        await update.message.reply_text(
            "❌ Withdrawal cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    return ConversationHandler.END


# ── Admin: approve ────────────────────────────────────────────────────────────

async def admin_approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    tg_admin = update.effective_user
    if tg_admin.id != settings.admin_telegram_id:
        await query.answer("⛔ Not authorised.", show_alert=True)
        return

    wd_id = int(query.data.split(":")[-1])
    wd    = await get_withdrawal_request(wd_id)
    if not wd:
        await query.edit_message_text("❌ Withdrawal record not found.")
        return

    if wd["status"] != "pending":
        await query.edit_message_text(
            f"ℹ️ Withdrawal #{wd_id} is already *{wd['status']}*.",
            parse_mode="Markdown",
        )
        return

    await query.edit_message_text(
        f"⏳ Processing withdrawal #{wd_id}…",
    )

    # Fetch user's wallet to get the encrypted private key
    from database.operations import get_wallet as _get_wallet
    wallet = await _get_wallet(wd["user_id"])
    if not wallet:
        await update_withdrawal_status(wd_id, "failed", admin_note="Wallet not found")
        await query.edit_message_text(f"❌ Withdrawal #{wd_id} failed: wallet not found.")
        return

    chain  = wd["chain"]
    addr   = wd["to_address"]
    amount = float(wd["amount"])

    # Decrypt private key
    try:
        if chain == "SOL":
            pk = decrypt(wallet["sol_pk_enc"], settings.encryption_key)
            result = await execute_sol_transfer(pk, addr, amount)
        elif chain == "ETH":
            pk = decrypt(wallet["eth_pk_enc"], settings.encryption_key)
            result = await execute_evm_transfer(pk, addr, amount, chain="ETH")
        elif chain == "BNB":
            enc = wallet.get("bnb_pk_enc") or wallet.get("eth_pk_enc", "")
            pk  = decrypt(enc, settings.encryption_key)
            result = await execute_evm_transfer(pk, addr, amount, chain="BNB")
        else:
            result = {"ok": False, "error": f"Unsupported chain: {chain}"}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}

    if result["ok"]:
        tx_hash = result["tx_hash"]
        await update_withdrawal_status(wd_id, "approved", tx_hash=tx_hash, admin_note="Approved by admin")
        await log_wallet_audit(
            user_id=wd["user_id"],
            action="WITHDRAWAL_APPROVED",
            chain=chain,
            address=addr,
            details=f"withdrawal_id={wd_id} amount={amount} tx={tx_hash[:16]}…",
        )
        await query.edit_message_text(
            f"✅ *Withdrawal #{wd_id} Approved & Executed*\n\n"
            f"Chain:   {chain}\n"
            f"To:      `{addr}`\n"
            f"Amount:  {fmt_balance(amount)} {chain}\n"
            f"Tx:      `{tx_hash}`",
            parse_mode="Markdown",
        )
    else:
        err = result.get("error", "Unknown error")
        await update_withdrawal_status(wd_id, "failed", admin_note=f"Execution failed: {err}")
        await log_wallet_audit(
            user_id=wd["user_id"],
            action="WITHDRAWAL_FAILED",
            chain=chain,
            address=addr,
            details=f"withdrawal_id={wd_id} amount={amount} error={err[:120]}",
        )
        await query.edit_message_text(
            f"❌ *Withdrawal #{wd_id} Execution Failed*\n\n`{err}`",
            parse_mode="Markdown",
        )


# ── Admin: reject ─────────────────────────────────────────────────────────────

async def admin_reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    tg_admin = update.effective_user
    if tg_admin.id != settings.admin_telegram_id:
        await query.answer("⛔ Not authorised.", show_alert=True)
        return

    wd_id = int(query.data.split(":")[-1])
    wd    = await get_withdrawal_request(wd_id)
    if not wd:
        await query.edit_message_text("❌ Withdrawal record not found.")
        return

    if wd["status"] != "pending":
        await query.edit_message_text(
            f"ℹ️ Withdrawal #{wd_id} is already *{wd['status']}*.",
            parse_mode="Markdown",
        )
        return

    await update_withdrawal_status(wd_id, "rejected", admin_note="Rejected by admin")
    await log_wallet_audit(
        user_id=wd["user_id"],
        action="WITHDRAWAL_REJECTED",
        chain=wd["chain"],
        address=wd["to_address"],
        details=f"withdrawal_id={wd_id} amount={wd['amount']}",
    )
    await query.edit_message_text(
        f"❌ *Withdrawal #{wd_id} Rejected*\n\n"
        f"Chain:  {wd['chain']}\n"
        f"To:     `{wd['to_address']}`\n"
        f"Amount: {fmt_balance(float(wd['amount']))} {wd['chain']}",
        parse_mode="Markdown",
    )
# ── ConversationHandler builder ───────────────────────────────────────────────

def withdrawal_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(withdrawal_start, pattern=r"^trade:withdraw$"),
            CommandHandler("withdraw", withdrawal_start_command),
        ],
        states={
            WD_CHAIN:   [CallbackQueryHandler(wd_chain_selected, pattern=r"^chain_sel:withdraw:")],
            WD_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_address_entered)],
            WD_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_amount_entered)],
        },
        fallbacks=[
            CommandHandler("start", wd_cancel),
            CallbackQueryHandler(wd_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
        ],
        allow_reentry=True,
        per_message=False,
    )

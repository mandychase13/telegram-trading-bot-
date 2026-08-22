"""
Admin Transfer Assistance — admin-only ConversationHandler.

Allows the admin to transfer native funds (SOL/ETH/BNB) directly out of
any registered user wallet to any destination address.

Access gate: settings.admin_telegram_id only.
Every transfer attempt — success or failure — is written to both the
admin_transfers table and the wallet_audit_log for a full audit trail.

Flow
────
/admin
  └─ Admin panel menu
       └─ "Transfer Funds"
            └─ Paginated user list
                 └─ Select user  →  live balances shown  →  select chain
                      └─ Enter destination address
                           └─ Enter amount  →  confirmation screen
                                └─ Execute  →  on-chain tx + audit log
"""
import asyncio
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

from config import settings
from database.operations import (
    get_all_users, get_wallet, get_user_by_id,
    log_admin_transfer, log_wallet_audit,
)
from blockchain.solana_client import get_sol_balance
from blockchain.eth_client import get_eth_balance
from blockchain.bnb_client import get_bnb_balance
from blockchain.solana_executor import execute_sol_transfer
from blockchain.evm_executor import execute_evm_transfer
from utils.encryption import decrypt
from utils.helpers import fmt_balance
from utils.logger import get_logger
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address

logger = get_logger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
AT_MAIN, AT_SELECT_USER, AT_SELECT_CHAIN, AT_ENTER_ADDRESS, AT_ENTER_AMOUNT, AT_CONFIRM = range(6)

_USERS_PER_PAGE = 8
_CB_CANCEL      = "admin:cancel"
_CB_PANEL       = "admin:panel"

_CHAIN_ICONS = {"SOL": "◎", "ETH": "Ξ", "BNB": "🟡"}


# ── Auth guard ─────────────────────────────────────────────────────────────────

def _is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == settings.admin_telegram_id)


async def _reject(update: Update) -> int:
    if update.callback_query:
        await update.callback_query.answer("⛔ Not authorised.", show_alert=True)
    return ConversationHandler.END


# ── Keyboard helpers ───────────────────────────────────────────────────────────

def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Transfer Funds",  callback_data="admin:transfer")],
        [InlineKeyboardButton("📋 List All Users",  callback_data="admin:list_users")],
        [InlineKeyboardButton("❌ Exit Panel",       callback_data=_CB_CANCEL)],
    ])


def _users_page_kb(users: list[dict], page: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    for u in users:
        name  = u.get("first_name") or u.get("username") or "—"
        label = f"@{u['username']}" if u.get("username") else f"ID:{u['telegram_id']}"
        rows.append([InlineKeyboardButton(
            f"{name}  ({label})",
            callback_data=f"admin:user:{u['id']}",
        )])

    pages = max(1, (total + _USERS_PER_PAGE - 1) // _USERS_PER_PAGE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:users:p:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="admin:noop"))
    if (page + 1) * _USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:users:p:{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data=_CB_PANEL)])
    rows.append([InlineKeyboardButton("❌ Cancel",         callback_data=_CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


def _chain_kb(balances: dict) -> InlineKeyboardMarkup:
    def _btn(chain: str) -> InlineKeyboardButton:
        icon = _CHAIN_ICONS.get(chain, "🔗")
        bal  = fmt_balance(balances.get(chain, 0.0))
        return InlineKeyboardButton(f"{icon} {chain}  ({bal})", callback_data=f"admin:chain:{chain}")
    return InlineKeyboardMarkup([
        [_btn("SOL")],
        [_btn("ETH")],
        [_btn("BNB")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:transfer")],
        [InlineKeyboardButton("❌ Cancel", callback_data=_CB_CANCEL)],
    ])


# ── Entry: /admin ──────────────────────────────────────────────────────────────

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point. Silently drops non-admin requests."""
    user = update.effective_user
    logger.info(
        "ADMIN_COMMAND_RECEIVED telegram_id=%s configured_admin=%s",
        user.id if user else None,
        settings.admin_telegram_id,
    )
    if not _is_admin(update):
        logger.warning(
            "ADMIN_ACCESS_DENIED telegram_id=%s configured_admin=%s",
            user.id if user else None,
            settings.admin_telegram_id,
        )
        return ConversationHandler.END

    _clear_admin_data(context)
    await update.message.reply_text(
        "🛡️ *Admin Control Panel*\n\nSelect an action:",
        parse_mode="Markdown",
        reply_markup=_main_menu_kb(),
    )
    return AT_MAIN


# ── AT_MAIN state ──────────────────────────────────────────────────────────────

async def admin_back_to_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()
    _clear_admin_data(context)
    await q.edit_message_text(
        "🛡️ *Admin Control Panel*\n\nSelect an action:",
        parse_mode="Markdown",
        reply_markup=_main_menu_kb(),
    )
    return AT_MAIN


async def admin_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show a text list of all registered users."""
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()

    users = await get_all_users(limit=200)
    if not users:
        text = "No users registered yet."
    else:
        lines = [f"👥 *All Users ({len(users)})*\n"]
        for i, u in enumerate(users, 1):
            name  = u.get("first_name") or "—"
            uname = f"@{u['username']}" if u.get("username") else "no username"
            ts    = u["created_at"].strftime("%Y-%m-%d") if u.get("created_at") else "—"
            lines.append(f"{i}\\. `{u['telegram_id']}` · {uname} · {name} · {ts}")
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "\n\n…(truncated)"

    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back to Panel", callback_data=_CB_PANEL),
        ]]),
    )
    return AT_MAIN


async def admin_start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the transfer flow — show page 0 of users."""
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()
    return await _show_user_page(q, context, page=0)


async def _show_user_page(q, context: ContextTypes.DEFAULT_TYPE, page: int) -> int:
    all_users: list[dict] = await get_all_users(limit=200)
    context.user_data["admin_all_users"] = all_users

    if not all_users:
        await q.edit_message_text(
            "❌ No registered users found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back to Panel", callback_data=_CB_PANEL),
            ]]),
        )
        return AT_MAIN

    start      = page * _USERS_PER_PAGE
    page_users = all_users[start: start + _USERS_PER_PAGE]

    await q.edit_message_text(
        f"👤 *Select User to Transfer From*\n"
        f"Total: {len(all_users)} users — tap a name to continue:",
        parse_mode="Markdown",
        reply_markup=_users_page_kb(page_users, page, len(all_users)),
    )
    return AT_SELECT_USER


# ── AT_SELECT_USER state ───────────────────────────────────────────────────────

async def admin_paginate_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[-1])
    return await _show_user_page(q, context, page=page)


async def admin_user_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User chosen — fetch wallet + live balances, then show chain picker."""
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()

    db_user_id = int(q.data.split(":")[-1])
    db_user    = await get_user_by_id(db_user_id)
    if not db_user:
        await q.edit_message_text(
            "❌ User not found in database.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data="admin:transfer"),
            ]]),
        )
        return AT_SELECT_USER

    wallet = await get_wallet(db_user["id"])
    if not wallet:
        await q.edit_message_text(
            f"❌ User `{db_user['telegram_id']}` has no wallet yet.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data="admin:transfer"),
            ]]),
        )
        return AT_SELECT_USER

    # Cache user + wallet context
    context.user_data["admin_target_user_id"] = db_user["id"]
    context.user_data["admin_target_tg_id"]   = db_user["telegram_id"]
    context.user_data["admin_target_name"]     = (
        db_user.get("first_name") or db_user.get("username") or str(db_user["telegram_id"])
    )
    context.user_data["admin_wallet"] = wallet

    # Fetch all three balances concurrently
    await q.edit_message_text("⏳ Fetching live balances…")
    try:
        sol_r, eth_r, bnb_r = await asyncio.gather(
            get_sol_balance(wallet.get("sol_address") or ""),
            get_eth_balance(wallet.get("eth_address") or ""),
            get_bnb_balance(wallet.get("bnb_address") or ""),
            return_exceptions=True,
        )
        balances = {
            "SOL": float(sol_r) if not isinstance(sol_r, Exception) else 0.0,
            "ETH": float(eth_r) if not isinstance(eth_r, Exception) else 0.0,
            "BNB": float(bnb_r) if not isinstance(bnb_r, Exception) else 0.0,
        }
    except Exception as exc:
        logger.error("Balance fetch failed for DB user %s: %s", db_user_id, exc)
        balances = {"SOL": 0.0, "ETH": 0.0, "BNB": 0.0}

    context.user_data["admin_balances"] = balances

    display  = context.user_data["admin_target_name"]
    tg_id    = db_user["telegram_id"]
    uname    = f"@{db_user['username']}" if db_user.get("username") else "no username"

    await q.edit_message_text(
        f"💸 *Transfer From User*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {display}  ({uname})\n"
        f"🆔 `{tg_id}`\n\n"
        f"*Live Balances:*\n"
        f"◎ SOL: `{fmt_balance(balances['SOL'])}`\n"
        f"Ξ ETH:  `{fmt_balance(balances['ETH'])}`\n"
        f"🟡 BNB: `{fmt_balance(balances['BNB'])}`\n\n"
        "Select the chain to transfer from:",
        parse_mode="Markdown",
        reply_markup=_chain_kb(balances),
    )
    return AT_SELECT_CHAIN


# ── AT_SELECT_CHAIN state ──────────────────────────────────────────────────────

async def admin_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()

    chain = q.data.split(":")[-1]  # SOL | ETH | BNB
    context.user_data["admin_chain"] = chain

    wallet = context.user_data.get("admin_wallet", {})
    from_address = {
        "SOL": wallet.get("sol_address", ""),
        "ETH": wallet.get("eth_address", ""),
        "BNB": wallet.get("bnb_address", ""),
    }.get(chain, "")
    context.user_data["admin_from_address"] = from_address

    icon    = _CHAIN_ICONS.get(chain, "🔗")
    balance = context.user_data.get("admin_balances", {}).get(chain, 0.0)

    await q.edit_message_text(
        f"💸 *Transfer {icon} {chain}*\n\n"
        f"From:      `{from_address}`\n"
        f"Available: `{fmt_balance(balance)} {chain}`\n\n"
        "Enter the *destination address*:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=_CB_CANCEL),
        ]]),
    )
    return AT_ENTER_ADDRESS


# ── AT_ENTER_ADDRESS state ─────────────────────────────────────────────────────

async def admin_address_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END

    addr = update.message.text.strip()
    chain = context.user_data.get("admin_chain", "SOL")
    if not is_valid_address(addr, chain):
        await update.message.reply_text(
            INVALID_ADDRESS_MESSAGE,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=_CB_CANCEL),
            ]]),
        )
        return AT_ENTER_ADDRESS

    context.user_data["admin_dest_address"] = addr
    chain   = context.user_data.get("admin_chain", "SOL")
    balance = context.user_data.get("admin_balances", {}).get(chain, 0.0)

    await update.message.reply_text(
        f"✅ Destination set: `{addr}`\n\n"
        f"Available balance: `{fmt_balance(balance)} {chain}`\n\n"
        "Enter the *amount* to transfer:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=_CB_CANCEL),
        ]]),
    )
    return AT_ENTER_AMOUNT


# ── AT_ENTER_AMOUNT state ──────────────────────────────────────────────────────

async def admin_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END

    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Enter a positive number:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=_CB_CANCEL),
            ]]),
        )
        return AT_ENTER_AMOUNT

    context.user_data["admin_amount"] = amount

    chain        = context.user_data.get("admin_chain", "SOL")
    from_addr    = context.user_data.get("admin_from_address", "")
    dest_addr    = context.user_data.get("admin_dest_address", "")
    display_name = context.user_data.get("admin_target_name", "")
    tg_id        = context.user_data.get("admin_target_tg_id", "")
    balance      = context.user_data.get("admin_balances", {}).get(chain, 0.0)
    icon         = _CHAIN_ICONS.get(chain, "🔗")

    warn = ""
    if amount > balance:
        warn = f"\n\n⚠️ *Warning: Amount exceeds balance* ({fmt_balance(balance)} {chain})"

    await update.message.reply_text(
        f"🔍 *Confirm Admin Transfer*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User:    {display_name}  (`{tg_id}`)\n"
        f"{icon} Chain:   {chain}\n"
        f"📤 From:   `{from_addr}`\n"
        f"📥 To:     `{dest_addr}`\n"
        f"💰 Amount: `{amount} {chain}`{warn}\n\n"
        "⚠️ *This executes immediately on-chain. Confirm?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Execute Transfer", callback_data="admin:xfer:confirm"),
                InlineKeyboardButton("❌ Cancel",           callback_data=_CB_CANCEL),
            ]
        ]),
    )
    return AT_CONFIRM


# ── AT_CONFIRM state ───────────────────────────────────────────────────────────

async def admin_execute_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return await _reject(update)
    q = update.callback_query
    await q.answer()

    admin_tg_id  = update.effective_user.id
    chain        = context.user_data.get("admin_chain", "SOL")
    from_addr    = context.user_data.get("admin_from_address", "")
    dest_addr    = context.user_data.get("admin_dest_address", "")
    amount       = float(context.user_data.get("admin_amount", 0))
    db_user_id   = context.user_data.get("admin_target_user_id")
    tg_id        = context.user_data.get("admin_target_tg_id")
    display_name = context.user_data.get("admin_target_name", "")
    wallet       = context.user_data.get("admin_wallet", {})
    icon         = _CHAIN_ICONS.get(chain, "🔗")

    await q.edit_message_text(
        f"⏳ Executing {icon} {chain} transfer of `{amount}` from user `{tg_id}`…",
        parse_mode="Markdown",
    )

    logger.warning(
        "ADMIN_TRANSFER_INITIATED admin=%s user_tg=%s user_db=%s chain=%s amount=%s dest=%s",
        admin_tg_id, tg_id, db_user_id, chain, amount, dest_addr,
    )

    # ── Execute on-chain ───────────────────────────────────────────────────────
    result: dict = {}
    try:
        if chain == "SOL":
            pk     = decrypt(wallet.get("sol_pk_enc", ""), settings.encryption_key)
            result = await execute_sol_transfer(pk, dest_addr, amount)
        elif chain == "ETH":
            pk     = decrypt(wallet.get("eth_pk_enc", ""), settings.encryption_key)
            result = await execute_evm_transfer(pk, dest_addr, amount, chain="ETH")
        elif chain == "BNB":
            enc    = wallet.get("bnb_pk_enc") or wallet.get("eth_pk_enc", "")
            pk     = decrypt(enc, settings.encryption_key)
            result = await execute_evm_transfer(pk, dest_addr, amount, chain="BNB")
        else:
            result = {"ok": False, "error": f"Unsupported chain: {chain}"}
    except Exception as exc:
        logger.error("Admin transfer execution error (admin=%s): %s", admin_tg_id, exc)
        result = {"ok": False, "error": str(exc)}

    tx_hash = result.get("tx_hash", "")
    status  = "success" if result.get("ok") else "failed"
    err_msg = result.get("error", "")

    # ── Dual audit logging ─────────────────────────────────────────────────────
    try:
        await asyncio.gather(
            log_admin_transfer(
                admin_tg_id=admin_tg_id,
                user_id=db_user_id,
                chain=chain,
                from_address=from_addr,
                to_address=dest_addr,
                amount=amount,
                tx_hash=tx_hash,
                status=status,
                note=err_msg[:200] if err_msg else "",
            ),
            log_wallet_audit(
                user_id=db_user_id,
                action="ADMIN_TRANSFER",
                chain=chain,
                address=dest_addr,
                details=(
                    f"admin={admin_tg_id} amount={amount} "
                    f"tx={tx_hash[:16] + '…' if tx_hash else 'n/a'} status={status}"
                ),
            ),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.error("Audit logging failed for admin transfer: %s", exc)

    # ── Result message ─────────────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if result.get("ok"):
        logger.warning(
            "ADMIN_TRANSFER_SUCCESS admin=%s user_tg=%s chain=%s amount=%s tx=%s",
            admin_tg_id, tg_id, chain, amount, tx_hash,
        )
        await q.edit_message_text(
            f"✅ *Admin Transfer Executed*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User:    {display_name}  (`{tg_id}`)\n"
            f"{icon} Chain:   {chain}\n"
            f"💰 Amount: `{amount} {chain}`\n"
            f"📤 From:   `{from_addr}`\n"
            f"📥 To:     `{dest_addr}`\n"
            f"🔗 Tx:     `{tx_hash}`\n"
            f"🕐 Time:   `{ts}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💸 Another Transfer", callback_data="admin:transfer"),
                    InlineKeyboardButton("⬅️ Panel",            callback_data=_CB_PANEL),
                ]
            ]),
        )
    else:
        logger.error(
            "ADMIN_TRANSFER_FAILED admin=%s user_tg=%s chain=%s amount=%s error=%s",
            admin_tg_id, tg_id, chain, amount, err_msg,
        )
        await q.edit_message_text(
            f"❌ *Admin Transfer Failed*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User:    {display_name}  (`{tg_id}`)\n"
            f"{icon} Chain:   {chain}  │  Amount: `{amount}`\n"
            f"📥 To:     `{dest_addr}`\n\n"
            f"🚫 Error: `{err_msg}`\n"
            f"🕐 Time:  `{ts}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 Retry",   callback_data="admin:transfer"),
                    InlineKeyboardButton("⬅️ Panel",   callback_data=_CB_PANEL),
                ]
            ]),
        )

    _clear_admin_data(context)
    return AT_MAIN


# ── Cancel / no-op ─────────────────────────────────────────────────────────────

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_admin_data(context)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "❌ Admin action cancelled.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛡️ Re-open Panel", callback_data=_CB_PANEL),
            ]]),
        )
    else:
        await update.message.reply_text("❌ Admin action cancelled.")
    return ConversationHandler.END


async def admin_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """No-op handler for informational buttons (e.g. page counter)."""
    if update.callback_query:
        await update.callback_query.answer()
    return None  # type: ignore[return-value]  # stays in current state


# ── Context cleanup ────────────────────────────────────────────────────────────

def _clear_admin_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "admin_target_user_id", "admin_target_tg_id", "admin_target_name",
        "admin_wallet", "admin_balances", "admin_chain", "admin_from_address",
        "admin_dest_address", "admin_amount", "admin_all_users",
    ):
        context.user_data.pop(key, None)


# ── ConversationHandler builder ────────────────────────────────────────────────

def admin_conversation() -> ConversationHandler:
    _panel_cb  = CallbackQueryHandler(admin_back_to_panel,   pattern=rf"^{_CB_PANEL}$")
    _cancel_cb = CallbackQueryHandler(admin_cancel,           pattern=rf"^{_CB_CANCEL}$")
    _noop_cb   = CallbackQueryHandler(admin_noop,             pattern=r"^admin:noop$")

    return ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_panel),
            # Allow an old panel message to reopen the conversation after a
            # process restart, when ConversationHandler state is gone.
            _panel_cb,
        ],
        states={
            AT_MAIN: [
                CallbackQueryHandler(admin_start_transfer, pattern=r"^admin:transfer$"),
                CallbackQueryHandler(admin_list_users,     pattern=r"^admin:list_users$"),
                _panel_cb,
                _noop_cb,
            ],
            AT_SELECT_USER: [
                CallbackQueryHandler(admin_paginate_users, pattern=r"^admin:users:p:\d+$"),
                CallbackQueryHandler(admin_user_selected,  pattern=r"^admin:user:\d+$"),
                CallbackQueryHandler(admin_start_transfer, pattern=r"^admin:transfer$"),
                _panel_cb,
                _noop_cb,
            ],
            AT_SELECT_CHAIN: [
                CallbackQueryHandler(admin_chain_selected, pattern=r"^admin:chain:(SOL|ETH|BNB)$"),
                CallbackQueryHandler(admin_start_transfer, pattern=r"^admin:transfer$"),
                _panel_cb,
            ],
            AT_ENTER_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_address_entered),
            ],
            AT_ENTER_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_amount_entered),
            ],
            AT_CONFIRM: [
                CallbackQueryHandler(admin_execute_transfer, pattern=r"^admin:xfer:confirm$"),
            ],
        },
        fallbacks=[
            _cancel_cb,
            _panel_cb,
            CommandHandler("admin", admin_panel),   # restart from any state
        ],
        per_message=False,
        name="admin_transfer",
        allow_reentry=True,
    )

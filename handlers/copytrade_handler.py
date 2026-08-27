"""
Copy trading management: follow/unfollow wallets, enable/disable, configure.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import (
    get_user,
    get_followed_wallets,
    add_followed_wallet,
    remove_followed_wallet,
    toggle_followed_wallet,
    upsert_copy_settings,
)
from utils.keyboards import Keyboards
from utils.helpers import fmt_address, parse_chain
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address
from utils.logger import get_logger

logger = get_logger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(ADD_CHAIN, ADD_ADDRESS, ADD_LABEL, ADD_COPY_PCT, ADD_MAX_AMOUNT, ADD_CONFIRM) = range(6)


# ─────────────────────────────────────────────────────────────────────────────
# Menu & list
# ─────────────────────────────────────────────────────────────────────────────

async def copytrade_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open copy trading dashboard from an inline button (callback query)."""
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    count = 0
    if db_user:
        wallets = await get_followed_wallets(db_user["id"])
        count = len([w for w in wallets if w["is_active"]])

    text = (
        f"📈 *Copy Trading Dashboard*\n\n"
        f"Active followed wallets: *{count}*\n\n"
        "Copy Vault monitors wallets you follow and automatically mirrors "
        "their trades proportionally based on your settings.\n\n"
        "Choose an option:"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        text, parse_mode="Markdown", reply_markup=Keyboards.copytrade_menu()
    )


async def copytrade_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open copy trading dashboard from the /copytrade command."""
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    count = 0
    if db_user:
        wallets = await get_followed_wallets(db_user["id"])
        count = len([w for w in wallets if w["is_active"]])

    text = (
        f"📈 *Copy Trading Dashboard*\n\n"
        f"Active followed wallets: *{count}*\n\n"
        "Copy Vault monitors wallets you follow and automatically mirrors "
        "their trades proportionally based on your settings.\n\n"
        "Choose an option:"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=Keyboards.copytrade_menu()
    )


async def list_followed_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)

    if not db_user:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Account not found.")
        return

    wallets = await get_followed_wallets(db_user["id"])
    if not wallets:
        text = "👥 *Followed Wallets*\n\nYou are not following any wallets yet."
    else:
        lines = ["👥 *Followed Wallets*\n"]
        for w in wallets:
            status = "🟢" if w["is_active"] else "🔴"
            label = w.get("label") or fmt_address(w["wallet_address"])
            lines.append(f"{status} [{w['chain']}] {label}\n  `{w['wallet_address']}`")
        text = "\n".join(lines)

    # Build remove buttons
    buttons = []
    if wallets:
        for w in wallets[:5]:  # show max 5 remove buttons
            label = w.get("label") or fmt_address(w["wallet_address"])
            buttons.append([InlineKeyboardButton(
                f"🗑 Remove: {label}",
                callback_data=f"copy:remove:{w['id']}"
            )])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=Keyboards.CB_COPYTRADE)])
    kb = InlineKeyboardMarkup(buttons)

    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown", reply_markup=kb)


async def remove_wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    parts = update.callback_query.data.split(":")
    wallet_id = int(parts[-1])
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        await remove_followed_wallet(wallet_id, db_user["id"])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "✅ Wallet removed from copy list.", reply_markup=Keyboards.back_to_dashboard()
    )


async def enable_copytrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        wallets = await get_followed_wallets(db_user["id"])
        for w in wallets:
            await toggle_followed_wallet(w["id"], db_user["id"], True)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "✅ Copy trading *enabled* for all followed wallets.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )


async def disable_copytrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        wallets = await get_followed_wallets(db_user["id"])
        for w in wallets:
            await toggle_followed_wallet(w["id"], db_user["id"], False)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "⏹ Copy trading *disabled* for all followed wallets.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add wallet conversation
# ─────────────────────────────────────────────────────────────────────────────

async def add_wallet_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "➕ *Add Wallet to Copy*\n\nSelect the blockchain:",
        parse_mode="Markdown", reply_markup=Keyboards.chain_select("copy")
    )
    return ADD_CHAIN


async def add_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["copy_chain"] = chain
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        f"➕ *Add {chain} Wallet*\n\nEnter the wallet address to follow:",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return ADD_ADDRESS


async def add_address_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addr = update.message.text.strip()
    chain = context.user_data.get("copy_chain", "SOL")
    if not is_valid_address(addr, chain):
        await update.message.reply_text(INVALID_ADDRESS_MESSAGE, reply_markup=Keyboards.cancel_only())
        return ADD_ADDRESS
    context.user_data["copy_address"] = addr
    await update.message.reply_text(
        f"✅ Address: `{addr}`\n\nEnter a *label* for this wallet (or type `skip`):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return ADD_LABEL


async def add_label_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    label_raw = update.message.text.strip()
    context.user_data["copy_label"] = None if label_raw.lower() == "skip" else label_raw
    await update.message.reply_text(
        "⚙️ Enter the *copy percentage* (1-100). E.g. `10` means copy 10% of each trade:",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return ADD_COPY_PCT


async def add_copy_pct_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        pct = float(update.message.text.strip())
        pct = max(1.0, min(100.0, pct))
    except ValueError:
        await update.message.reply_text("❌ Enter a number between 1 and 100:")
        return ADD_COPY_PCT

    context.user_data["copy_pct"] = pct
    await update.message.reply_text(
        "💰 Enter the *maximum trade amount* per copy (e.g. `1.0` SOL):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return ADD_MAX_AMOUNT


async def add_max_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        max_amt = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a number:")
        return ADD_MAX_AMOUNT

    context.user_data["copy_max_amount"] = max_amt
    chain   = context.user_data.get("copy_chain")
    addr    = context.user_data.get("copy_address")
    label   = context.user_data.get("copy_label") or "—"
    pct     = context.user_data.get("copy_pct")

    await update.message.reply_text(
        f"✅ *Confirm Copy Wallet*\n\n"
        f"Chain: {chain}\nAddress: `{addr}`\nLabel: {label}\n"
        f"Copy %: {pct}%\nMax per trade: {max_amt}\n\nProceed?",
        parse_mode="Markdown", reply_markup=Keyboards.confirm("copy")
    )
    return ADD_CONFIRM


async def add_wallet_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Account error.")
        return ConversationHandler.END

    chain   = context.user_data.get("copy_chain")
    addr    = context.user_data.get("copy_address")
    label   = context.user_data.get("copy_label")
    pct     = context.user_data.get("copy_pct", 10.0)
    max_amt = context.user_data.get("copy_max_amount", 1.0)

    fw = await add_followed_wallet(db_user["id"], chain, addr, label)
    if fw:
        await upsert_copy_settings(
            db_user["id"], fw["id"],
            copy_percentage=pct, max_trade_amount=max_amt
        )

    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        f"✅ *Wallet added!*\n\n`{addr}` is now being monitored on {chain}.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )
    context.user_data.clear()
    return ConversationHandler.END


async def copy_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=
            "❌ Cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    return ConversationHandler.END


def add_wallet_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(add_wallet_start, pattern=f"^{Keyboards.CB_COPY_ADD}$")],
        states={
            ADD_CHAIN:      [CallbackQueryHandler(add_chain_selected, pattern=r"^chain_sel:copy:")],
            ADD_ADDRESS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_address_entered)],
            ADD_LABEL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_label_entered)],
            ADD_COPY_PCT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_copy_pct_entered)],
            ADD_MAX_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_max_amount_entered)],
            ADD_CONFIRM:    [CallbackQueryHandler(add_wallet_confirmed, pattern=r"^confirm:yes:copy$")],
        },
        fallbacks=[
            CallbackQueryHandler(copy_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
            CallbackQueryHandler(copy_cancel, pattern=r"^confirm:no"),
        ],
        per_message=False,
    )

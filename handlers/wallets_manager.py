"""
Manage tracked wallets (add external wallets to monitor without copying them).
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import get_user, get_followed_wallets, add_followed_wallet, remove_followed_wallet
from utils.keyboards import Keyboards
from utils.helpers import fmt_address
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address
from utils.logger import get_logger

logger = get_logger(__name__)

(MGW_CHAIN, MGW_ADDRESS, MGW_LABEL, MGW_CONFIRM) = range(4)


async def manage_wallets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "👥 *Manage Wallets*\n\nAdd or remove wallets you want to track or copy-trade.",
        parse_mode="Markdown", reply_markup=Keyboards.manage_wallets_menu()
    )


async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)

    wallets = await get_followed_wallets(db_user["id"]) if db_user else []

    if not wallets:
        text = "👥 *Your Wallets*\n\n_No wallets added yet._"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=
            text, parse_mode="Markdown", reply_markup=Keyboards.manage_wallets_menu()
        )
        return

    lines = ["👥 *Your Tracked Wallets*\n"]
    buttons = []
    for w in wallets:
        label = w.get("label") or fmt_address(w["wallet_address"])
        status = "🟢" if w["is_active"] else "🔴"
        lines.append(f"{status} [{w['chain']}] *{label}*\n  `{w['wallet_address']}`")
        buttons.append([InlineKeyboardButton(
            f"🗑 Remove {label}", callback_data=f"mgwallet:del:{w['id']}"
        )])

    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:manage_wallets")])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def remove_wallet_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    wallet_id = int(update.callback_query.data.split(":")[-1])
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        await remove_followed_wallet(wallet_id, db_user["id"])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "✅ Wallet removed.", reply_markup=Keyboards.back_to_dashboard()
    )


# ── Add wallet conversation ───────────────────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "➕ *Add Wallet*\n\nSelect the blockchain:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("mgw"),
    )
    return MGW_CHAIN


async def mgw_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["mgw_chain"] = chain
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        f"➕ *Add {chain} Wallet*\n\nPaste the wallet address:",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return MGW_ADDRESS


async def mgw_address_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addr = update.message.text.strip()
    chain = context.user_data.get("mgw_chain", "SOL")
    if not is_valid_address(addr, chain):
        await update.message.reply_text(INVALID_ADDRESS_MESSAGE, reply_markup=Keyboards.cancel_only())
        return MGW_ADDRESS
    context.user_data["mgw_address"] = addr
    await update.message.reply_text(
        f"Address: `{addr}`\n\nEnter a *label* (or type `skip`):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return MGW_LABEL


async def mgw_label_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    label = None if raw.lower() == "skip" else raw
    context.user_data["mgw_label"] = label

    chain = context.user_data.get("mgw_chain")
    addr  = context.user_data.get("mgw_address")

    await update.message.reply_text(
        f"✅ *Confirm*\n\nChain: {chain}\nAddress: `{addr}`\nLabel: {label or '—'}\n\nAdd?",
        parse_mode="Markdown", reply_markup=Keyboards.confirm("mgw")
    )
    return MGW_CONFIRM


async def mgw_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        await add_followed_wallet(
            db_user["id"],
            context.user_data.get("mgw_chain"),
            context.user_data.get("mgw_address"),
            context.user_data.get("mgw_label"),
        )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=
        "✅ Wallet added!", reply_markup=Keyboards.back_to_dashboard()
    )
    context.user_data.clear()
    return ConversationHandler.END


async def mgw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=
            "❌ Cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    return ConversationHandler.END


def add_wallet_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern=r"^mgwallet:add$")],
        states={
            MGW_CHAIN:   [CallbackQueryHandler(mgw_chain_selected, pattern=r"^chain_sel:mgw:")],
            MGW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, mgw_address_entered)],
            MGW_LABEL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, mgw_label_entered)],
            MGW_CONFIRM: [CallbackQueryHandler(mgw_confirmed, pattern=r"^confirm:yes:mgw$")],
        },
        fallbacks=[
            CallbackQueryHandler(mgw_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
            CallbackQueryHandler(mgw_cancel, pattern=r"^confirm:no"),
        ],
        per_message=False,
    )

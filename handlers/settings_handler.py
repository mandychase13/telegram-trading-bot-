"""
User settings: slippage, priority fee, notifications, language, trade defaults.
"""
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import get_user, get_user_settings, update_user_settings
from utils.keyboards import Keyboards
from utils.logger import get_logger

logger = get_logger(__name__)

# Setting key being edited, stored in user_data
(ENTERING_VALUE,) = range(1)

_SETTING_LABELS = {
    "slippage":      ("📉 Slippage (%)",        "slippage",              "1–50, e.g. `1.5`"),
    "priority_fee":  ("⚡ Priority Fee",         "priority_fee",          "in SOL, e.g. `0.001`"),
    "notifications": ("🔔 Notifications",        "notifications_enabled", "`on` or `off`"),
    "language":      ("🌐 Language",             "language",              "`en`, `es`, `fr`, `de`, `zh`"),
    "default_sol":   ("💰 Default Buy (SOL)",    "default_buy_sol",       "amount in SOL, e.g. `0.1`"),
    "default_eth":   ("💰 Default Buy (ETH)",    "default_buy_eth",       "amount in ETH, e.g. `0.01`"),
    "default_bnb":   ("💰 Default Buy (BNB)",    "default_buy_bnb",       "amount in BNB, e.g. `0.05`"),
    "stop_loss":     ("🛑 Stop Loss (%)",        "stop_loss_pct",         "e.g. `10` for 10%"),
    "take_profit":   ("🎯 Take Profit (%)",      "take_profit_pct",       "e.g. `50` for 50%"),
    "max_trades":    ("🔢 Max Daily Trades",     "max_daily_trades",      "whole number, e.g. `10`"),
}


async def _build_settings_text(db_user: dict) -> str:
    s = await get_user_settings(db_user["id"]) if db_user else {}
    notif = "🔔 ON" if s and s.get("notifications_enabled") else "🔕 OFF"
    return (
        "⚙️ *Settings*\n\n"
        f"📉 Slippage: `{s.get('slippage', 1.0) if s else 1.0}%`\n"
        f"⚡ Priority Fee: `{s.get('priority_fee', 0.001) if s else 0.001}`\n"
        f"🔔 Notifications: {notif}\n"
        f"🌐 Language: `{s.get('language', 'en') if s else 'en'}`\n"
        f"💰 Default Buy SOL: `{s.get('default_buy_sol', 0.1) if s else 0.1}`\n"
        f"💰 Default Buy ETH: `{s.get('default_buy_eth', 0.01) if s else 0.01}`\n"
        f"💰 Default Buy BNB: `{s.get('default_buy_bnb', 0.05) if s else 0.05}`\n"
        f"🛑 Stop Loss: `{s.get('stop_loss_pct', 10.0) if s else 10.0}%`\n"
        f"🎯 Take Profit: `{s.get('take_profit_pct', 50.0) if s else 50.0}%`\n"
        f"🔢 Max Daily Trades: `{s.get('max_daily_trades', 10) if s else 10}`"
    )


async def settings_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settings command — works from a plain text message."""
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await update.message.reply_text("❌ Account not found. Type /start.")
        return
    logger.info("User %s requested /settings", tg_user.id)
    try:
        text = await _build_settings_text(db_user)
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=Keyboards.settings_menu()
        )
    except Exception as exc:
        logger.error("settings_command_handler error for user %s: %s", tg_user.id, exc, exc_info=True)
        await update.message.reply_text("❌ Could not load settings. Please try again.")


async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    try:
        text = await _build_settings_text(db_user)
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=Keyboards.settings_menu()
        )
    except Exception as exc:
        logger.error("settings_menu error for user %s: %s", tg_user.id, exc, exc_info=True)
        await update.callback_query.edit_message_text("❌ Could not load settings. Please try again.")


async def setting_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry: user tapped a specific setting button."""
    query = update.callback_query
    await query.answer()

    key = query.data.replace("set:", "")  # e.g. "slippage"
    if key not in _SETTING_LABELS:
        await query.answer("Unknown setting.", show_alert=True)
        return ConversationHandler.END

    label, db_col, hint = _SETTING_LABELS[key]
    context.user_data["setting_key"] = key
    context.user_data["setting_db_col"] = db_col

    await query.edit_message_text(
        f"⚙️ *{label}*\n\nEnter a new value ({hint}):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return ENTERING_VALUE


async def setting_value_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    key   = context.user_data.get("setting_key", "")
    col   = context.user_data.get("setting_db_col", "")

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await update.message.reply_text("❌ Account error. Type /start.")
        return ConversationHandler.END

    # Parse and validate
    try:
        if col in ("notifications_enabled",):
            value = raw.lower() in ("on", "yes", "true", "1", "enabled")
        elif col in ("max_daily_trades",):
            value = int(raw)
        elif col == "language":
            value = raw.lower()[:5]
        else:
            value = float(raw)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid value. Try again:", reply_markup=Keyboards.cancel_only()
        )
        return ENTERING_VALUE

    await update_user_settings(db_user["id"], **{col: value})

    label = _SETTING_LABELS.get(key, ("Setting",))[0]
    await update.message.reply_text(
        f"✅ *{label}* updated to `{value}`.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )
    context.user_data.clear()
    return ConversationHandler.END


async def settings_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "❌ Cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    return ConversationHandler.END


def settings_conversation() -> ConversationHandler:
    setting_patterns = "|".join(f"^set:{k}$" for k in _SETTING_LABELS)
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(setting_start, pattern=setting_patterns)],
        states={
            ENTERING_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setting_value_entered)],
        },
        fallbacks=[
            CallbackQueryHandler(settings_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
        ],
        per_message=False,
    )

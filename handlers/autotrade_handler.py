"""
Automated trading settings: stop-loss, take-profit, daily trade limits.
"""
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import get_user, get_autotrade_settings, upsert_autotrade_settings
from utils.keyboards import Keyboards
from utils.logger import get_logger

logger = get_logger(__name__)

(AUTO_CHAIN, AUTO_STOP_LOSS, AUTO_TAKE_PROFIT, AUTO_MAX_TRADES, AUTO_MAX_AMOUNT, AUTO_CONFIRM) = range(6)


async def autotrade_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)

    settings_lines = []
    if db_user:
        for chain in ("SOL", "ETH", "BNB"):
            s = await get_autotrade_settings(db_user["id"], chain)
            if s:
                status = "🟢 ON" if s["is_enabled"] else "🔴 OFF"
                settings_lines.append(
                    f"  {chain}: {status} | SL {s['stop_loss_pct']}% | TP {s['take_profit_pct']}%"
                )

    summary = "\n".join(settings_lines) if settings_lines else "  _Not configured yet._"
    text = (
        "🤖 *Automated Trading*\n\n"
        f"{summary}\n\n"
        "Auto-trading executes rule-based orders based on your stop-loss and take-profit settings."
    )
    await context.bot.send_message(update.effective_chat.id,
        text, parse_mode="Markdown", reply_markup=Keyboards.autotrade_menu()
    )


async def auto_config_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await context.bot.send_message(update.effective_chat.id,
        "🤖 *Configure Autotrade*\n\nSelect the chain:",
        parse_mode="Markdown",
        reply_markup=Keyboards.chain_select("auto"),
    )
    return AUTO_CHAIN


async def auto_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]
    context.user_data["auto_chain"] = chain
    await context.bot.send_message(update.effective_chat.id,
        f"🤖 *Autotrade — {chain}*\n\nEnter *stop-loss %* (e.g. `10` for 10%):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return AUTO_STOP_LOSS


async def auto_stop_loss_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        sl = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a number (e.g. 10):")
        return AUTO_STOP_LOSS
    context.user_data["auto_sl"] = sl
    await update.message.reply_text(
        f"✅ Stop-loss: {sl}%\n\nEnter *take-profit %* (e.g. `50`):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return AUTO_TAKE_PROFIT


async def auto_take_profit_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        tp = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a number (e.g. 50):")
        return AUTO_TAKE_PROFIT
    context.user_data["auto_tp"] = tp
    await update.message.reply_text(
        f"✅ Take-profit: {tp}%\n\nEnter *max daily trades* (e.g. `5`):",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return AUTO_MAX_TRADES


async def auto_max_trades_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        max_t = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a whole number:")
        return AUTO_MAX_TRADES
    context.user_data["auto_max_trades"] = max_t
    await update.message.reply_text(
        f"✅ Max daily trades: {max_t}\n\nEnter *max trade amount* per order:",
        parse_mode="Markdown", reply_markup=Keyboards.cancel_only()
    )
    return AUTO_MAX_AMOUNT


async def auto_max_amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        max_a = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a number:")
        return AUTO_MAX_AMOUNT
    context.user_data["auto_max_amount"] = max_a

    chain = context.user_data.get("auto_chain")
    sl    = context.user_data.get("auto_sl")
    tp    = context.user_data.get("auto_tp")
    mt    = context.user_data.get("auto_max_trades")

    await update.message.reply_text(
        f"🤖 *Confirm Autotrade Settings*\n\n"
        f"Chain: {chain}\nStop-Loss: {sl}%\nTake-Profit: {tp}%\n"
        f"Max Daily Trades: {mt}\nMax per Trade: {max_a}\n\nSave?",
        parse_mode="Markdown", reply_markup=Keyboards.confirm("auto")
    )
    return AUTO_CONFIRM


async def auto_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(update.effective_chat.id, "❌ Account error.")
        return ConversationHandler.END

    chain = context.user_data.get("auto_chain", "SOL")
    await upsert_autotrade_settings(
        db_user["id"], chain,
        stop_loss_pct    = context.user_data.get("auto_sl", 10.0),
        take_profit_pct  = context.user_data.get("auto_tp", 50.0),
        max_daily_trades = context.user_data.get("auto_max_trades", 5),
        max_trade_amount = context.user_data.get("auto_max_amount", 1.0),
    )

    await context.bot.send_message(update.effective_chat.id,
        "✅ *Autotrade settings saved!* Use the menu to enable trading.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )
    context.user_data.clear()
    return ConversationHandler.END


async def auto_enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        for chain in ("SOL", "ETH", "BNB"):
            await upsert_autotrade_settings(db_user["id"], chain, is_enabled=True)
    await context.bot.send_message(update.effective_chat.id,
        "▶️ *Autotrade enabled* on all chains.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )


async def auto_disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if db_user:
        for chain in ("SOL", "ETH", "BNB"):
            await upsert_autotrade_settings(db_user["id"], chain, is_enabled=False)
    await context.bot.send_message(update.effective_chat.id,
        "⏹ *Autotrade disabled* on all chains.",
        parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )


async def auto_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(update.effective_chat.id,
            "❌ Cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    return ConversationHandler.END


def autotrade_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(auto_config_start, pattern=f"^{Keyboards.CB_AUTO_CONFIG}$")],
        states={
            AUTO_CHAIN:       [CallbackQueryHandler(auto_chain_selected, pattern=r"^chain_sel:auto:")],
            AUTO_STOP_LOSS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_stop_loss_entered)],
            AUTO_TAKE_PROFIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_take_profit_entered)],
            AUTO_MAX_TRADES:  [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_max_trades_entered)],
            AUTO_MAX_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, auto_max_amount_entered)],
            AUTO_CONFIRM:     [CallbackQueryHandler(auto_confirmed, pattern=r"^confirm:yes:auto$")],
        },
        fallbacks=[
            CallbackQueryHandler(auto_cancel, pattern=f"^{Keyboards.CB_CANCEL}$"),
            CallbackQueryHandler(auto_cancel, pattern=r"^confirm:no"),
        ],
        per_message=False,
    )

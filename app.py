"""
 Copy Vault — Telegram Copy Trading Bot
Entry point: initialises DB, registers all handlers, and starts long-polling.
"""
import asyncio
import sys
import logging
import traceback

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    TypeHandler,
    PicklePersistence,
)

from config import settings
from database.schema import create_tables
from database.connection import init_pool

# Suppress PTB per_message advisory warnings — our conversations intentionally
# mix CallbackQueryHandlers and MessageHandlers with per_message=False.
import warnings
warnings.filterwarnings("ignore", message=".*per_message.*", category=UserWarning)
from utils.keyboards import Keyboards
from utils.logger import get_logger

# Handlers
from handlers.start import start_handler, continue_handler, help_handler, guide_handler
from handlers.dashboard import dashboard_callback, refresh_callback
from handlers.wallet_handler import wallet_menu_callback, wallet_command_handler, deposit_callback, deposit_command_handler, history_callback
from handlers.trade_handler import buy_conversation, sell_conversation, transfer_conversation
from handlers.portfolio_handler import portfolio_callback
from handlers.copytrade_handler import (
    copytrade_menu, copytrade_command_handler, list_followed_wallets,
    remove_wallet_callback, enable_copytrade, disable_copytrade,
    add_wallet_conversation as copy_add_conversation,
)
from handlers.autotrade_handler import (
    autotrade_menu, auto_enable, auto_disable, autotrade_conversation,
)
from handlers.wallets_manager import (
    manage_wallets_menu, list_wallets, remove_wallet_cb, add_wallet_conversation as mgw_conversation,
)
from handlers.settings_handler import settings_menu, settings_command_handler, settings_conversation
from handlers.import_wallet_handler import import_wallet_conversation
from handlers.withdrawal_handler import (
    withdrawal_conversation, admin_approve_withdrawal, admin_reject_withdrawal,
)
from handlers.admin_handler import admin_conversation
from handlers.card_handler import generatecard_conversation
from handlers.activity_tracker import track_activity

from services.copy_engine import check_all_followed_wallets

logger = get_logger("copy_vault.main")


async def error_handler(update: object, context) -> None:
    # Always print the full traceback to the console (stdout) so it shows in
    # Replit's workflow log and is never silently discarded.
    print("=" * 60, flush=True)
    print("UNHANDLED EXCEPTION IN BOT HANDLER", flush=True)
    traceback.print_exception(
        type(context.error), context.error, context.error.__traceback__,
        file=sys.stdout,
    )
    print("=" * 60, flush=True)
    # Also emit through the structured logger so the log file captures it.
    logger.error("Unhandled exception: %s", context.error, exc_info=context.error)
    try:
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.answer(
                "Something went wrong. Please try again.", show_alert=True
            )
        elif isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Something went wrong. Please try again."
            )
    except Exception as reply_error:
        logger.warning("Could not send handler error response: %s", reply_error)


async def transfer_menu_redirect(update: Update, context) -> None:
    """Redirect wallet Transfer button to the transfer conversation entry point."""
    from handlers.trade_handler import transfer_start
    await transfer_start(update, context)


async def manage_wallet_remove_redirect(update: Update, context) -> None:
    """Show the wallet list when user taps 'Remove Wallet' in manage wallets menu."""
    await list_wallets(update, context)


async def _post_init(app: "Application") -> None:
    """Register the visible command list with Telegram after the bot starts."""
    from telegram import BotCommand
    commands = [
        BotCommand("start",        "Open dashboard"),
        BotCommand("wallet",       "View balances and wallet addresses"),
        BotCommand("deposit",      "Show deposit addresses"),
        BotCommand("copytrade",    "Manage wallets you want to copy"),
        BotCommand("withdraw",     "Submit a withdrawal request"),
        BotCommand("buy",          "Buy a token on any chain"),
        BotCommand("sell",         "Sell tokens from your wallet"),
        BotCommand("import",       "Import an existing wallet"),
        BotCommand("settings",     "Configure trading preferences"),
        BotCommand("guide",        "Full feature guide"),
        BotCommand("help",         "Quick command reference"),
        BotCommand("admin",        "Open administrator panel"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Bot command list registered with Telegram (%d commands)", len(commands))
    except Exception as exc:
        logger.warning("Could not register bot commands: %s", exc)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        logger.critical("TELEGRAM_BOT_TOKEN is not set – cannot start the bot.")
        sys.exit(1)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",        start_handler))
    app.add_handler(CommandHandler("help",         help_handler))
    app.add_handler(CommandHandler("guide",        guide_handler))
    app.add_handler(CommandHandler("wallet",       wallet_command_handler))
    app.add_handler(CommandHandler("deposit",      deposit_command_handler))
    app.add_handler(CommandHandler("copytrade",   copytrade_command_handler))
    app.add_handler(CommandHandler("settings",     settings_command_handler))

    # ── Admin conversation (must be first — most specific entry point) ───────
    app.add_handler(admin_conversation())

    # ── Trade card demo generator (owner-only /generatecard) ─────────────────
    app.add_handler(generatecard_conversation())

    # ── Conversations (must be before simple CallbackQueryHandlers) ───────────
    app.add_handler(buy_conversation())
    app.add_handler(sell_conversation())
    app.add_handler(transfer_conversation())
    app.add_handler(copy_add_conversation())
    app.add_handler(autotrade_conversation())
    app.add_handler(mgw_conversation())
    app.add_handler(settings_conversation())
    app.add_handler(import_wallet_conversation())
    app.add_handler(withdrawal_conversation())

    # ── Simple CallbackQuery handlers ─────────────────────────────────────────
    # Onboarding
    app.add_handler(CallbackQueryHandler(continue_handler, pattern=f"^{Keyboards.CB_CONTINUE}$"))

    # Main menu navigation
    app.add_handler(CallbackQueryHandler(dashboard_callback,      pattern=f"^{Keyboards.CB_DASHBOARD}$"))
    app.add_handler(CallbackQueryHandler(refresh_callback,        pattern=f"^{Keyboards.CB_WALLET_REFRESH}$"))
    app.add_handler(CallbackQueryHandler(wallet_menu_callback,    pattern=f"^{Keyboards.CB_WALLET}$"))
    app.add_handler(CallbackQueryHandler(portfolio_callback,      pattern=f"^{Keyboards.CB_PORTFOLIO}$"))
    app.add_handler(CallbackQueryHandler(copytrade_menu,          pattern=f"^{Keyboards.CB_COPYTRADE}$"))
    app.add_handler(CallbackQueryHandler(autotrade_menu,          pattern=f"^{Keyboards.CB_AUTOTRADE}$"))
    app.add_handler(CallbackQueryHandler(manage_wallets_menu,     pattern=f"^{Keyboards.CB_MANAGE_WALLETS}$"))
    app.add_handler(CallbackQueryHandler(settings_menu,           pattern=f"^{Keyboards.CB_SETTINGS}$"))
    app.add_handler(CallbackQueryHandler(help_handler,            pattern=f"^{Keyboards.CB_HELP}$"))

    # Wallet sub-menu
    app.add_handler(CallbackQueryHandler(deposit_callback,         pattern=f"^{Keyboards.CB_WALLET_DEPOSIT}$"))
    app.add_handler(CallbackQueryHandler(history_callback,         pattern=f"^{Keyboards.CB_WALLET_HISTORY}$"))
    # Transfer button inside wallet menu → launch transfer conversation
    app.add_handler(CallbackQueryHandler(transfer_menu_redirect,   pattern=f"^{Keyboards.CB_WALLET_TRANSFER}$"))

    # Copy trading sub-actions
    app.add_handler(CallbackQueryHandler(list_followed_wallets,   pattern=f"^{Keyboards.CB_COPY_LIST}$"))
    app.add_handler(CallbackQueryHandler(remove_wallet_callback,  pattern=r"^copy:remove:\d+$"))
    app.add_handler(CallbackQueryHandler(enable_copytrade,        pattern=f"^{Keyboards.CB_COPY_ENABLE}$"))
    app.add_handler(CallbackQueryHandler(disable_copytrade,       pattern=f"^{Keyboards.CB_COPY_DISABLE}$"))

    # Autotrade sub-actions
    app.add_handler(CallbackQueryHandler(auto_enable,             pattern=f"^{Keyboards.CB_AUTO_ENABLE}$"))
    app.add_handler(CallbackQueryHandler(auto_disable,            pattern=f"^{Keyboards.CB_AUTO_DISABLE}$"))

    # Wallet manager sub-actions
    app.add_handler(CallbackQueryHandler(list_wallets,                  pattern=r"^mgwallet:list$"))
    app.add_handler(CallbackQueryHandler(remove_wallet_cb,              pattern=r"^mgwallet:del:\d+$"))
    app.add_handler(CallbackQueryHandler(manage_wallet_remove_redirect, pattern=r"^mgwallet:remove$"))

    # Admin: withdrawal approval / rejection
    app.add_handler(CallbackQueryHandler(admin_approve_withdrawal, pattern=r"^admin:wd:approve:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_reject_withdrawal,  pattern=r"^admin:wd:reject:\d+$"))

    # ── Activity tracker (group -1 → runs before all other handlers) ─────────
    # TypeHandler matches every update type; the tracker fires in the background
    # and never prevents normal handlers from processing the same update.
    app.add_handler(TypeHandler(Update, track_activity), group=-1)

    # Error handler
    app.add_error_handler(error_handler)

    return app


def main() -> None:
    logger.info("=" * 60)
    logger.info("Starting Copy Vault bot…")
    logger.info("Administrator access configured for Telegram ID %s", settings.admin_telegram_id)
    logger.info("=" * 60)

    # Synchronous DB initialisation before event loop
    try:
        create_tables()
        init_pool()
    except Exception as exc:
        logger.critical("Database initialisation failed: %s", exc)
        sys.exit(1)

    app = build_application()

    # Copy engine: check followed wallets every 60 seconds
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(
            check_all_followed_wallets,
            interval=60,
            first=15,
            name="copy_engine",
        )
        logger.info("Copy engine job scheduled (interval=60s)")

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,   # process messages queued while offline
    )


if __name__ == "__main__":
    main()

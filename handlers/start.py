"""
/start command and initial onboarding flow.
"""
from telegram import Update
from telegram.ext import ContextTypes

from database.operations import get_or_create_user, get_user, get_wallet
from services.wallet_service import create_user_wallets
from utils.keyboards import Keyboards
from utils.admin_notify import notify_new_user
from utils.logger import get_logger

logger = get_logger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user

    # Check first, because get_or_create_user intentionally returns the same
    # shape for both cases and /start needs to distinguish onboarding from a
    # returning-user shortcut.
    existing_user = await get_user(tg_user.id)
    if existing_user:
        await update.message.reply_text(
            "👋 *Welcome back!*\n\n"
            "Your secure wallet and settings are already loaded.",
            parse_mode="Markdown",
            reply_markup=Keyboards.returning_user_wallet(),
        )
        logger.info("Returning user %s opened /start", tg_user.id)
        return

    # First /start: preserve the existing onboarding flow exactly.
    db_user = await get_or_create_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
    )

    welcome_text = (
        "👋 *Welcome to Copy Vault!*\n\n"
        "The ultimate copy trading experience.\n\n"
        "📈 Mirror top-performing wallets instantly, discover profitable "
        "opportunities, and trade smarter with powerful automation.\n\n"
        "🤖 Copy Vault executes trades in real time, helping you stay ahead "
        "without constantly watching charts.\n\n"
        "ℹ️ Type /help anytime to view the complete bot guide.\n\n"
        "🔗 _Initializing your account..._"
    )

    msg = await update.message.reply_text(welcome_text, parse_mode="Markdown")

    # Create wallets if this is a brand-new user
    existing = await get_wallet(db_user["id"])
    if not existing:
        await create_user_wallets(db_user["id"])
        wallet_status = "✅ *Wallet successfully created and linked!*"
        # Notify admin about the new user (fire-and-forget)
        try:
            await notify_new_user(update.get_bot(), tg_user)
        except Exception:
            pass
    else:
        wallet_status = "✅ *Wallet already linked and ready!*"

    full_text = welcome_text + "\n\n" + wallet_status
    await msg.edit_text(
        full_text,
        parse_mode="Markdown",
        reply_markup=Keyboards.continue_button(),
    )
    logger.info("User %s started the bot", tg_user.id)


async def continue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    from handlers.dashboard import show_dashboard
    await show_dashboard(update, context, edit=True)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *Copy Vault — Help*\n\n"
        "*/start* — Open your dashboard\n"
        "*/wallet* — View balances and addresses\n"
        "*/deposit* — Show deposit addresses\n"
        "*/withdraw* — Submit a withdrawal request\n"
        "*/buy* — Buy a token on any chain\n"
        "*/sell* — Sell tokens from your wallet\n"
        "*/import* — Import an existing wallet\n"
        "*/settings* — Configure trading preferences\n"
        "*/guide* — Full feature guide\n"
        "*/help* — Show this message\n"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=Keyboards.back_to_dashboard())
    else:
        await update.callback_query.answer()
        await context.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown",
                                                      reply_markup=Keyboards.back_to_dashboard())


_GUIDE_TEXT = (
    "━━━━━━━━━━━━━━━━\n\n"
    "📖 Copy Vault — Complete User Guide\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "🚀 Getting Started\n\n"
    "Welcome to Copy Vault — your all-in-one crypto trading assistant.\n\n"
    "Your Solana (SOL), Ethereum (ETH), and BNB Chain wallets are automatically created when you start the bot using /start.\n\n"
    "Simply fund your wallet, explore the available trading tools, and begin buying, selling, copying trades, or automating your trading strategy.\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "📜 Available Commands\n\n"
    "/start — Open your trading dashboard\n\n"
    "/wallet — View balances, wallet addresses, and portfolio\n\n"
    "/deposit — Display your deposit addresses\n\n"
    "/copytrade — Manage wallets you want to copy\n\n"
    "/withdraw — Request a withdrawal\n\n"
    "/buy — Buy tokens on supported networks\n\n"
    "/sell — Sell tokens from your portfolio\n\n"
    "/import or /importwallet — Import an existing wallet\n\n"
    "/settings — Configure trading preferences\n\n"
    "/guide — Open this complete guide\n\n"
    "/help — View the command list\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "📈 Copy Trading\n\n"
    "Mirror trades from experienced wallets across supported blockchains.\n\n"
    "Users can:\n\n"
    "• Follow selected wallets\n\n"
    "• Set copy percentage\n\n"
    "• Configure maximum trade sizes\n\n"
    "• Pause or resume copy trading\n\n"
    "• Manage multiple copied wallets\n\n"
    "Every trade follows the user's personal settings and risk preferences.\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "🤖 Auto Trading\n\n"
    "Automate trading strategies with:\n\n"
    "• Stop-Loss\n\n"
    "• Take-Profit\n\n"
    "• Default Buy Amount\n\n"
    "• Maximum Daily Trades\n\n"
    "• Risk Management Settings\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "💼 Wallet Management\n\n"
    "Features:\n\n"
    "• Automatic wallet creation\n\n"
    "• Multi-chain wallet support\n\n"
    "• Wallet import\n\n"
    "• Portfolio tracking\n\n"
    "• Balance monitoring\n\n"
    "• Deposit management\n\n"
    "• Withdrawal requests\n\n"
    "• Secure transaction signing\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "⚙️ Trading Settings\n\n"
    "Slippage:\n"
    "Controls maximum price movement accepted during swaps.\n\n"
    "Priority Fee:\n"
    "Improves transaction priority during network congestion.\n\n"
    "Default Buy Amount:\n"
    "Stores preferred trading amounts.\n\n"
    "Stop-Loss & Take-Profit:\n"
    "Automatically manages positions.\n\n"
    "Maximum Daily Trades:\n"
    "Controls daily trading activity.\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "🔔 Notifications\n\n"
    "Users receive updates for:\n\n"
    "• Completed trades\n\n"
    "• Copy trading activity\n\n"
    "• Deposits\n\n"
    "• Withdrawal requests\n\n"
    "• Portfolio updates\n\n"
    "• Important account events\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "🔐 Security\n\n"
    "Copy Vault uses security-focused practices designed to help protect wallets and transactions.\n\n"
    "Users should:\n\n"
    "• Verify wallet addresses before sending funds.\n\n"
    "• Review transactions before confirming.\n\n"
    "• Keep enough network tokens available for blockchain fees.\n\n"
    "⚡ Trading features require a funded wallet with sufficient balance to cover trades and required network transaction fees.\n\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "🌐 Trade Smarter with Copy Vault\n\n"
    "Copy Vault provides the tools needed to buy tokens, follow experienced traders, and automate crypto trading strategies from one powerful Telegram assistant.\n\n"
    "Copy Vault — Trade Smarter. Trade Faster. Trade with Confidence."
)

_TELEGRAM_MSG_LIMIT = 4096


def _split_guide(text: str) -> list[str]:
    """Split guide text at section dividers so each part fits Telegram's limit."""
    if len(text) <= _TELEGRAM_MSG_LIMIT:
        return [text]
    parts: list[str] = []
    current = ""
    for section in text.split("━━━━━━━━━━━━━━━━"):
        divider = "━━━━━━━━━━━━━━━━"
        candidate = (current + divider + section) if current else section
        if len(candidate) <= _TELEGRAM_MSG_LIMIT:
            current = candidate
        else:
            if current:
                parts.append(current.strip())
            current = (divider + section) if parts else section
    if current.strip():
        parts.append(current.strip())
    return parts or [text[:_TELEGRAM_MSG_LIMIT]]


async def guide_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Complete bot guide — /guide command and guide menu button."""
    parts = _split_guide(_GUIDE_TEXT)

    if update.message:
        for i, part in enumerate(parts):
            markup = Keyboards.back_to_dashboard() if i == len(parts) - 1 else None
            await update.message.reply_text(part, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.answer()
        # Send every guide part as a new message so the original menu remains visible
        await context.bot.send_message(update.effective_chat.id,
            parts[0],
            reply_markup=Keyboards.back_to_dashboard() if len(parts) == 1 else None,
        )
        for i, part in enumerate(parts[1:], start=1):
            markup = Keyboards.back_to_dashboard() if i == len(parts) - 1 else None
            await update.callback_query.message.reply_text(part, reply_markup=markup)

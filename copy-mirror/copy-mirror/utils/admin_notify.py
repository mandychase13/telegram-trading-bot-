"""
Admin notification utility.

Sends Telegram messages to the configured admin with automatic retry on
failure.  All calls are fire-and-forget — a failed notification never
interrupts the user-facing flow.

Notification catalogue
──────────────────────
notify_new_user             — brand-new user registered
notify_wallet_import        — user imported a wallet
notify_withdrawal_request   — user submitted a withdrawal (with Approve/Reject)
notify_user_action          — generic one-line activity (used by activity_tracker)
"""
import asyncio
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def _send(bot, text: str, reply_markup=None, max_attempts: int = 3) -> None:
    """Fire-and-forget helper: up to max_attempts with exponential back-off."""
    admin_id = settings.admin_telegram_id
    if not admin_id:
        return
    for attempt in range(1, max_attempts + 1):
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            return
        except Exception as exc:
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.warning(
                    "Admin notification failed after %d attempts: %s",
                    max_attempts, exc,
                )


# ── Public notification functions ─────────────────────────────────────────────

async def notify_new_user(bot, tg_user) -> None:
    """Sent once when a brand-new user runs /start for the first time."""
    text = (
        "👤 *New User Registered*\n\n"
        f"🆔 User ID: `{tg_user.id}`\n"
        f"👤 Username: @{tg_user.username or '—'}\n"
        f"📛 First name: {tg_user.first_name or '—'}\n"
        f"🕐 Time: {_now()}"
    )
    await _send(bot, text)


async def notify_wallet_import(bot, tg_user, chain: str, address: str) -> None:
    """Sent when a user successfully imports a wallet."""
    chain_icon = {"SOL": "◎", "ETH": "Ξ", "BNB": "🟡"}.get(chain, "🔗")
    text = (
        "📥 *Wallet Imported*\n\n"
        f"🆔 User ID: `{tg_user.id}`\n"
        f"👤 Username: @{tg_user.username or '—'}\n"
        f"{chain_icon} Chain: {chain}\n"
        f"📬 Address: `{address}`\n"
        f"🕐 Time: {_now()}"
    )
    await _send(bot, text)


async def notify_withdrawal_request(
    bot,
    tg_user,
    chain: str,
    from_address: str,
    to_address: str,
    amount: float,
    wd_id: int,
) -> None:
    """Sent when a user submits a withdrawal request (includes Approve/Reject buttons)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    chain_icon = {"SOL": "◎", "ETH": "Ξ", "BNB": "🟡"}.get(chain, "🔗")
    text = (
        "💸 *Withdrawal Request*\n\n"
        f"🆔 User ID: `{tg_user.id}`\n"
        f"👤 Username: @{tg_user.username or '—'}\n"
        f"{chain_icon} Chain: {chain}\n"
        f"📬 From: `{from_address}`\n"
        f"📤 To: `{to_address}`\n"
        f"💰 Amount: {amount} {chain}\n"
        f"🕐 Time: {_now()}"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"admin:wd:approve:{wd_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"admin:wd:reject:{wd_id}"),
        ]
    ])
    await _send(bot, text, reply_markup=kb)


async def notify_user_action(bot, tg_user, action_label: str) -> None:
    """Compact one-liner activity notification (used by the activity tracker)."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    username = f"@{tg_user.username}" if tg_user.username else "no username"
    text = (
        f"👁 *Activity*\n"
        f"👤 `{tg_user.id}` · {username} · {tg_user.first_name or '—'}\n"
        f"🎯 {action_label}\n"
        f"🕐 {ts}"
    )
    await _send(bot, text)

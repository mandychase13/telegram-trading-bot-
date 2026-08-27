"""
/generatecard — Owner-only demo trade summary card generator.

Only the configured administrator may use this command.
All other users receive ⛔ Access Denied.

The handler asks 16 questions one at a time via a ConversationHandler,
then generates the same premium PNG card used for real completed trades,
watermarked with 🧪 DEMO TRADE SUMMARY.

No demo data is persisted; no balances or trade history are touched.
"""
from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import settings
from utils.card_generator import TradeCardData, generate_trade_card, CardGenerationError
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def _is_owner(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == settings.admin_telegram_id
    )


# ---------------------------------------------------------------------------
# Conversation states  (one per question)
# ---------------------------------------------------------------------------
(
    GC_TOKEN_NAME,
    GC_TOKEN_PAIR,
    GC_NETWORK,
    GC_BUY_PRICE,
    GC_SELL_PRICE,
    GC_INVESTED,
    GC_SOLD,
    GC_GROSS_PROFIT,
    GC_NET_PROFIT,
    GC_PROFIT_PCT,
    GC_ROI_PCT,
    GC_DURATION,
    GC_PORTFOLIO_BEFORE,
    GC_PORTFOLIO_AFTER,
    GC_DATE,
    GC_TIME,
) = range(16)

_CTX_KEY = "gc_data"          # key inside context.user_data for accumulated answers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(context: ContextTypes.DEFAULT_TYPE, key: str, value) -> None:
    if _CTX_KEY not in context.user_data:
        context.user_data[_CTX_KEY] = {}
    context.user_data[_CTX_KEY][key] = value


def _get(context: ContextTypes.DEFAULT_TYPE, key: str, default=None):
    return context.user_data.get(_CTX_KEY, {}).get(key, default)


def _parse_float(text: str) -> Optional[float]:
    """Parse user input as float, accepting commas and % signs."""
    cleaned = text.strip().replace(",", "").replace("%", "").replace("$", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


async def _ask(update: Update, question: str) -> None:
    await update.message.reply_text(
        question,
        parse_mode="Markdown",
    )


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_CTX_KEY, None)
    await update.message.reply_text("❌ Card generation cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def generatecard_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry: /generatecard — check owner, start Q&A."""
    if not _is_owner(update):
        await update.message.reply_text("⛔ Access Denied.")
        return ConversationHandler.END

    context.user_data.pop(_CTX_KEY, None)   # clear any stale state
    await update.message.reply_text(
        "🧪 *Demo Trade Card Generator*\n\n"
        "I'll ask you 16 questions. Answer each one, or send /cancel to stop.\n\n"
        "1️⃣ *Token Name* — e.g. `Bonk`, `Pepe`, `Ethereum`",
        parse_mode="Markdown",
    )
    return GC_TOKEN_NAME


# ---------------------------------------------------------------------------
# Q&A steps
# ---------------------------------------------------------------------------

async def gc_token_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _store(context, "token_name", update.message.text.strip())
    await _ask(update, "2️⃣ *Token Pair* — e.g. `BONK/SOL`, `PEPE/ETH`")
    return GC_TOKEN_PAIR


async def gc_token_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().upper()
    _store(context, "token_pair", raw)
    # Derive symbol from pair (first part before /)
    symbol = raw.split("/")[0] if "/" in raw else raw[:6]
    _store(context, "token_symbol", symbol)
    await _ask(update, "3️⃣ *Network* — `SOL`, `ETH`, or `BNB`")
    return GC_NETWORK


async def gc_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    net = update.message.text.strip().upper()
    if net not in ("SOL", "ETH", "BNB", "SOLANA", "ETHEREUM", "BINANCE"):
        await _ask(update, "⚠️ Please enter one of: `SOL`, `ETH`, `BNB`")
        return GC_NETWORK
    mapping = {"SOLANA": "SOL", "ETHEREUM": "ETH", "BINANCE": "BNB"}
    net = mapping.get(net, net)
    _store(context, "network", net)
    await _ask(update, "4️⃣ *Buy Price* (USD) — e.g. `0.00000182`")
    return GC_BUY_PRICE


async def gc_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `0.00000182`")
        return GC_BUY_PRICE
    _store(context, "buy_price", v)
    await _ask(update, "5️⃣ *Sell Price* (USD) — e.g. `0.00000963`")
    return GC_SELL_PRICE


async def gc_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `0.00000963`")
        return GC_SELL_PRICE
    _store(context, "sell_price", v)
    await _ask(update, "6️⃣ *Invested Amount* (native tokens sent) — e.g. `2.5`")
    return GC_INVESTED


async def gc_invested(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `2.5`")
        return GC_INVESTED
    _store(context, "amount_invested", v)
    await _ask(update, "7️⃣ *Sold Amount* (native tokens received back) — e.g. `13.2`")
    return GC_SOLD


async def gc_sold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `13.2`")
        return GC_SOLD
    _store(context, "amount_received", v)
    await _ask(update, "8️⃣ *Gross Profit* (before fees) — e.g. `10.7` or `-1.5`")
    return GC_GROSS_PROFIT


async def gc_gross_profit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `10.7`")
        return GC_GROSS_PROFIT
    _store(context, "gross_profit", v)
    await _ask(update, "9️⃣ *Net Profit* (after fees) — e.g. `10.2` or `-1.8`")
    return GC_NET_PROFIT


async def gc_net_profit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `10.2`")
        return GC_NET_PROFIT
    _store(context, "net_profit", v)
    await _ask(update, "🔟 *Profit %* — e.g. `428` or `-12` (no % sign needed)")
    return GC_PROFIT_PCT


async def gc_profit_pct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `428`")
        return GC_PROFIT_PCT
    _store(context, "profit_pct", v)
    await _ask(update, "1️⃣1️⃣ *ROI %* — e.g. `428` (same as profit% if unsure)")
    return GC_ROI_PCT


async def gc_roi_pct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `428`")
        return GC_ROI_PCT
    _store(context, "roi_pct", v)
    await _ask(update, "1️⃣2️⃣ *Trade Duration* — e.g. `2h 30m`, `45m`, `3d 12h`")
    return GC_DURATION


async def gc_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _store(context, "trade_duration", update.message.text.strip())
    await _ask(update, "1️⃣3️⃣ *Portfolio Value Before* (USD) — e.g. `1250.00`")
    return GC_PORTFOLIO_BEFORE


async def gc_portfolio_before(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `1250.00`")
        return GC_PORTFOLIO_BEFORE
    _store(context, "portfolio_before", v)
    await _ask(update, "1️⃣4️⃣ *Portfolio Value After* (USD) — e.g. `1840.00`")
    return GC_PORTFOLIO_AFTER


async def gc_portfolio_after(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    v = _parse_float(update.message.text)
    if v is None:
        await _ask(update, "⚠️ Enter a valid number, e.g. `1840.00`")
        return GC_PORTFOLIO_AFTER
    _store(context, "portfolio_after", v)
    await _ask(update, "1️⃣5️⃣ *Date* — e.g. `Aug 3, 2026`")
    return GC_DATE


async def gc_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _store(context, "date", update.message.text.strip())
    await _ask(update, "1️⃣6️⃣ *Time* — e.g. `14:32 UTC`")
    return GC_TIME


async def gc_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Final step: collect time, build card, send it."""
    _store(context, "time_str", update.message.text.strip())

    d = context.user_data.get(_CTX_KEY, {})
    network = d.get("network", "SOL")

    card_data = TradeCardData(
        token_name       = d.get("token_name",       "Unknown"),
        token_symbol     = d.get("token_symbol",     "???"),
        token_pair       = d.get("token_pair",       ""),
        network          = network,
        buy_price        = d.get("buy_price"),
        sell_price       = d.get("sell_price"),
        amount_invested  = d.get("amount_invested"),
        amount_received  = d.get("amount_received"),
        gross_profit     = d.get("gross_profit"),
        net_profit       = d.get("net_profit"),
        profit_pct       = d.get("profit_pct"),
        roi_pct          = d.get("roi_pct"),
        trade_duration   = d.get("trade_duration",   "—"),
        portfolio_before = d.get("portfolio_before"),
        portfolio_after  = d.get("portfolio_after"),
        date             = d.get("date",             "—"),
        time_str         = d.get("time_str",         "—"),
        chain_currency   = network,
        is_demo          = True,
    )

    context.user_data.pop(_CTX_KEY, None)   # clean up

    generating_msg = await update.message.reply_text("⏳ Generating demo card…")

    try:
        png_bytes = generate_trade_card(card_data)
        await update.message.reply_photo(
            photo=png_bytes,
            caption=(
                "🧪 *Demo Trade Summary Card*\n"
                "Generated for preview only — no data was modified."
            ),
            parse_mode="Markdown",
        )
        await generating_msg.delete()
    except CardGenerationError as exc:
        logger.error("Demo card generation failed: %s", exc)
        await generating_msg.edit_text("❌ Failed to generate Trade Summary Card.")
    except Exception as exc:
        logger.exception("Unexpected error in demo card: %s", exc)
        await generating_msg.edit_text("❌ Failed to generate Trade Summary Card.")

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Conversation handler factory
# ---------------------------------------------------------------------------

def generatecard_conversation() -> ConversationHandler:
    """Return the ConversationHandler for /generatecard."""
    cancel_handler = CommandHandler("cancel", _cancel)

    return ConversationHandler(
        entry_points=[CommandHandler("generatecard", generatecard_start)],
        states={
            GC_TOKEN_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_token_name)],
            GC_TOKEN_PAIR:       [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_token_pair)],
            GC_NETWORK:          [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_network)],
            GC_BUY_PRICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_buy_price)],
            GC_SELL_PRICE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_sell_price)],
            GC_INVESTED:         [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_invested)],
            GC_SOLD:             [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_sold)],
            GC_GROSS_PROFIT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_gross_profit)],
            GC_NET_PROFIT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_net_profit)],
            GC_PROFIT_PCT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_profit_pct)],
            GC_ROI_PCT:          [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_roi_pct)],
            GC_DURATION:         [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_duration)],
            GC_PORTFOLIO_BEFORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_portfolio_before)],
            GC_PORTFOLIO_AFTER:  [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_portfolio_after)],
            GC_DATE:             [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_date)],
            GC_TIME:             [MessageHandler(filters.TEXT & ~filters.COMMAND, gc_time)],
        },
        fallbacks=[cancel_handler],
        allow_reentry=True,
        per_message=False,
        name="generatecard_conversation",
    )

"""
Portfolio view – token holdings, P&L, trade history.
"""
from telegram import Update
from telegram.ext import ContextTypes

from database.operations import get_user, get_wallet, get_portfolio_tokens, get_trades
from blockchain.solana_client import get_sol_balance
from blockchain.eth_client import get_eth_balance
from blockchain.bnb_client import get_bnb_balance
from services.price_service import get_chain_prices, get_token_price
from utils.keyboards import Keyboards
from utils.helpers import fmt_balance, fmt_usd, fmt_pnl, fmt_pct
from utils.logger import get_logger

logger = get_logger(__name__)


async def portfolio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(update.effective_chat.id, "❌ Account not found. Type /start.")
        return

    user_id = db_user["id"]

    import asyncio
    wallet, tokens, trades, prices = await asyncio.gather(
        get_wallet(user_id),
        get_portfolio_tokens(user_id),
        get_trades(user_id, limit=5),
        get_chain_prices(),
        return_exceptions=True,
    )
    wallet = wallet if isinstance(wallet, dict) else None
    tokens = tokens if isinstance(tokens, list) else []
    trades = trades if isinstance(trades, list) else []
    prices = prices if isinstance(prices, dict) else {}

    # Native balances
    sol_bal, eth_bal, bnb_bal = 0.0, 0.0, 0.0
    if wallet:
        balance_results = await asyncio.gather(
            get_sol_balance(wallet["sol_address"] or ""),
            get_eth_balance(wallet["eth_address"] or ""),
            get_bnb_balance(wallet["bnb_address"] or ""),
            return_exceptions=True,
        )
        sol_bal, eth_bal, bnb_bal = (
            value if isinstance(value, (int, float)) else 0.0
            for value in balance_results
        )

    native_usd = (
        sol_bal * prices.get("SOL", 0)
        + eth_bal * prices.get("ETH", 0)
        + bnb_bal * prices.get("BNB", 0)
    )

    lines = ["📊 *Portfolio Overview*\n"]

    # Native holdings
    lines.append("*Native Assets*")
    if sol_bal > 0:
        lines.append(f"  ◎ SOL: {fmt_balance(sol_bal)} ≈ {fmt_usd(sol_bal * prices.get('SOL', 0))}")
    if eth_bal > 0:
        lines.append(f"  Ξ ETH: {fmt_balance(eth_bal)} ≈ {fmt_usd(eth_bal * prices.get('ETH', 0))}")
    if bnb_bal > 0:
        lines.append(f"  🟡 BNB: {fmt_balance(bnb_bal)} ≈ {fmt_usd(bnb_bal * prices.get('BNB', 0))}")

    # Token holdings from DB
    token_usd = 0.0
    if tokens:
        lines.append("\n*Token Holdings*")
        for tok in tokens:
            try:
                price = await get_token_price(tok["chain"], tok["token_address"])
            except Exception as exc:
                logger.warning("Could not fetch token price for portfolio item: %s", exc)
                price = 0.0
            value = tok["balance"] * price
            token_usd += value
            avg = tok.get("avg_buy_price", 0)
            pnl = (price - avg) * tok["balance"] if avg else 0
            lines.append(
                f"  {tok['token_symbol']} ({tok['chain']}): "
                f"{fmt_balance(tok['balance'])} ≈ {fmt_usd(value)} | "
                f"P&L: {fmt_pnl(pnl)}"
            )

    total_usd = native_usd + token_usd
    lines.append(f"\n💵 *Total Portfolio Value: {fmt_usd(total_usd)}*")

    # Recent trades
    if trades:
        lines.append("\n*Recent Trades*")
        for t in trades[:5]:
            icon = "🟢" if t["trade_type"] == "buy" else "🔴"
            ts = t["created_at"].strftime("%m/%d") if t.get("created_at") else ""
            lines.append(
                f"  {icon} {t['trade_type'].upper()} {t.get('token_symbol','?')} "
                f"• {t['chain']} • {ts}"
            )
    else:
        lines.append("\n_No trades yet. Start trading to see history here._")

    text = "\n".join(lines)

    await context.bot.send_message(update.effective_chat.id,
        text, parse_mode="Markdown", reply_markup=Keyboards.back_to_dashboard()
    )

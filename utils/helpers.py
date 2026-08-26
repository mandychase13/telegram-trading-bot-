from datetime import datetime
from typing import Optional

def fmt_address(addr: str, visible: int = 6) -> str:
    """Shorten a long address for display: 0xAbcd...1234"""
    if not addr or len(addr) <= visible * 2 + 3:
        return addr
    return f"{addr[:visible]}...{addr[-visible:]}"


def fmt_balance(value: float, decimals: int = 6) -> str:
    """Format a token balance, stripping trailing zeros."""
    if value == 0:
        return "0"
    formatted = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return formatted


def fmt_usd(value: float) -> str:
    """Format a USD value with commas and 2 decimal places."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def fmt_pnl(value: float) -> str:
    """Format a P&L value with sign and colour emoji."""
    sign = "📈" if value >= 0 else "📉"
    prefix = "+" if value >= 0 else ""
    return f"{sign} {prefix}{fmt_usd(value)}"


def fmt_pct(value: float) -> str:
    prefix = "+" if value >= 0 else ""
    return f"{prefix}{value:.2f}%"


def parse_chain(text: str) -> Optional[str]:
    t = text.strip().upper()
    if t in ("SOL", "SOLANA"):
        return "SOL"
    if t in ("ETH", "ETHEREUM"):
        return "ETH"
    if t in ("BNB", "BSC"):
        return "BNB"
    return None


def now_ts() -> datetime:
    return datetime.utcnow()

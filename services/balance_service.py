"""Balance presentation helpers for the on-chain/internal accounting split."""
from decimal import Decimal
from database.connection import fetchone, fetchall


async def get_balance_overlay(user_id: int, asset: str, network: str) -> dict | None:
    return await fetchone(
        """
        SELECT balance, display_mode, updated_at
        FROM internal_balances
        WHERE user_id = %s AND asset = %s AND network = %s
        """,
        (user_id, asset.upper(), network.upper()),
    )


async def resolve_available_balance(user_id: int, asset: str, network: str, on_chain: float) -> float:
    overlay = await get_balance_overlay(user_id, asset, network)
    if overlay and overlay["display_mode"] == "internal":
        return float(Decimal(str(overlay["balance"])))
    return float(on_chain)


async def get_adjustment_history(user_id: int | None = None, limit: int = 50) -> list[dict]:
    if user_id is None:
        return await fetchall(
            "SELECT * FROM balance_adjustment_audit ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
    return await fetchall(
        """
        SELECT * FROM balance_adjustment_audit
        WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
        """,
        (user_id, limit),
    )
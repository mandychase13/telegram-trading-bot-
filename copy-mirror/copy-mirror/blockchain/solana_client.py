"""
Solana JSON-RPC client (async).
"""
from typing import Optional
import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

_RPC_TIMEOUT = 15  # seconds


async def _rpc_post(method: str, params: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_RPC_TIMEOUT) as client:
            resp = await client.post(
                settings.solana_rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.error("Solana RPC request failed [%s]", method)
        return {}


async def get_sol_balance(address: str) -> float:
    """Return SOL balance in SOL (not lamports)."""
    try:
        data = await _rpc_post("getBalance", [address])
        lamports = data.get("result", {}).get("value", 0)
        return float(lamports) / 1_000_000_000
    except (TypeError, ValueError, AttributeError) as exc:
        logger.warning("Invalid Solana balance response: %s", exc)
        return 0.0


async def get_sol_transactions(address: str, limit: int = 10) -> list[dict]:
    """Return recent transaction signatures for an address."""
    data = await _rpc_post(
        "getSignaturesForAddress", [address, {"limit": limit}]
    )
    return data.get("result") or []


async def get_token_accounts(address: str) -> list[dict]:
    """Return SPL token accounts for a wallet."""
    data = await _rpc_post(
        "getTokenAccountsByOwner",
        [
            address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    return data.get("result", {}).get("value") or []


async def get_token_balance(token_account: str) -> float:
    data = await _rpc_post("getTokenAccountBalance", [token_account])
    ui_amount = (
        data.get("result", {})
        .get("value", {})
        .get("uiAmount", 0.0)
    )
    return ui_amount or 0.0


async def get_transaction_detail(signature: str) -> Optional[dict]:
    data = await _rpc_post(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
    )
    return data.get("result")


async def monitor_wallet_for_new_txs(address: str, last_sig: Optional[str]) -> tuple[list[dict], Optional[str]]:
    """
    Return new transactions since last_sig.
    Returns (new_txs, latest_signature).
    """
    sigs = await get_sol_transactions(address, limit=5)
    if not sigs:
        return [], last_sig

    latest_sig = sigs[0].get("signature")

    if last_sig is None:
        # First check – store the latest sig without processing
        return [], latest_sig

    new_txs = []
    for sig_info in sigs:
        sig = sig_info.get("signature")
        if sig == last_sig:
            break
        detail = await get_transaction_detail(sig)
        if detail:
            new_txs.append(detail)

    return new_txs, latest_sig

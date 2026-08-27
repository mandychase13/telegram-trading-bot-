"""
Solana JSON-RPC client (async).
"""
from typing import Optional
import asyncio
import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

_RPC_TIMEOUT = 15  # seconds
_RPC_RETRIES = 3


async def _rpc_post(method: str, params: list) -> dict:
    endpoint = settings.solana_rpc_url
    host = settings.endpoint_host(endpoint)
    if not settings._valid_endpoint(endpoint):
        logger.error("Solana RPC endpoint is invalid: host=%s method=%s", host, method)
        return {}
    for attempt in range(1, _RPC_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_RPC_TIMEOUT) as client:
                resp = await client.post(
                    endpoint,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    logger.error("Solana RPC returned an error host=%s method=%s error=%s",
                                 host, method, data["error"])
                return data
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            logger.warning("Solana RPC network failure host=%s method=%s attempt=%d/%d: %s",
                           host, method, attempt, _RPC_RETRIES, exc)
            if attempt < _RPC_RETRIES:
                await asyncio.sleep(attempt)
        except Exception as exc:
            logger.error("Solana RPC request failed host=%s method=%s: %s", host, method, exc)
            return {}
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

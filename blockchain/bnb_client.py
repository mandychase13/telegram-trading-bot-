"""
BNB Chain JSON-RPC client (async).  BNB is EVM-compatible so the code mirrors
eth_client with a different RPC endpoint.
"""
from typing import Optional
import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

_RPC_TIMEOUT = 15


async def _rpc_post(method: str, params: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_RPC_TIMEOUT) as client:
            resp = await client.post(
                settings.bnb_rpc_endpoint,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.error("BNB RPC request failed [%s]", method)
        return {}


async def get_bnb_balance(address: str) -> float:
    """Return BNB balance in BNB."""
    try:
        data = await _rpc_post("eth_getBalance", [address, "latest"])
        hex_val = data.get("result", "0x0")
        wei = int(hex_val, 16)
        return wei / 1e18
    except (TypeError, ValueError, AttributeError) as exc:
        logger.warning("Invalid BNB balance response: %s", exc)
        return 0.0


async def get_bep20_balance(address: str, token_address: str, decimals: int = 18) -> float:
    """Return BEP-20 token balance."""
    padded = address[2:].lower().zfill(64)
    data_hex = "0x70a08231" + padded
    result = await _rpc_post(
        "eth_call",
        [{"to": token_address, "data": data_hex}, "latest"],
    )
    hex_val = result.get("result", "0x0")
    if hex_val == "0x":
        return 0.0
    return int(hex_val, 16) / (10 ** decimals)


async def get_bnb_transactions(address: str, limit: int = 10) -> list[dict]:
    """Return recent BNB transactions (best-effort via eth_getBlockByNumber scan)."""
    try:
        block_data = await _rpc_post("eth_blockNumber", [])
        latest_block = int(block_data.get("result", "0x0"), 16)
        txs = []
        for b in range(latest_block, max(0, latest_block - 20), -1):
            if len(txs) >= limit:
                break
            blk = await _rpc_post("eth_getBlockByNumber", [hex(b), True])
            transactions = blk.get("result", {}).get("transactions") or []
            for tx in transactions:
                if tx.get("from", "").lower() == address.lower() or \
                   tx.get("to", "").lower() == address.lower():
                    txs.append(tx)
        return txs[:limit]
    except Exception as exc:
        logger.error("BNB transaction fetch error: %s", exc)
        return []

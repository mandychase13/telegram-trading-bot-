"""
Ethereum JSON-RPC client (async, using raw HTTP to avoid web3 event-loop issues).
"""
from typing import Optional
import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

_RPC_TIMEOUT = 15


async def _rpc_post(url: str, method: str, params: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_RPC_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.error("ETH RPC request failed [%s]", method)
        return {}


async def get_eth_balance(address: str) -> float:
    """Return ETH balance in Ether."""
    try:
        data = await _rpc_post(settings.ethereum_rpc_url, "eth_getBalance", [address, "latest"])
        hex_val = data.get("result", "0x0")
        wei = int(hex_val, 16)
        return wei / 1e18
    except (TypeError, ValueError, AttributeError) as exc:
        logger.warning("Invalid Ethereum balance response: %s", exc)
        return 0.0


async def get_eth_block_number() -> int:
    data = await _rpc_post(settings.ethereum_rpc_url, "eth_blockNumber", [])
    return int(data.get("result", "0x0"), 16)


async def get_eth_transactions(address: str, limit: int = 10) -> list[dict]:
    """
    Fetch recent transactions via Alchemy's alchemy_getAssetTransfers.
    Falls back to an empty list on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=_RPC_TIMEOUT) as client:
            resp = await client.post(
                settings.ethereum_rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "alchemy_getAssetTransfers",
                    "params": [
                        {
                            "fromAddress": address,
                            "category": ["external", "erc20"],
                            "maxCount": hex(limit),
                            "order": "desc",
                        }
                    ],
                },
            )
            data = resp.json()
            return data.get("result", {}).get("transfers") or []
    except Exception as exc:
        logger.error("alchemy_getAssetTransfers error: %s", exc)
        return []


async def get_erc20_balance(address: str, token_address: str, decimals: int = 18) -> float:
    """Return ERC-20 token balance."""
    # balanceOf(address) selector = 0x70a08231
    padded = address[2:].lower().zfill(64)
    data_hex = "0x70a08231" + padded
    result = await _rpc_post(
        settings.ethereum_rpc_url,
        "eth_call",
        [{"to": token_address, "data": data_hex}, "latest"],
    )
    hex_val = result.get("result", "0x0")
    if hex_val == "0x":
        return 0.0
    raw = int(hex_val, 16)
    return raw / (10 ** decimals)

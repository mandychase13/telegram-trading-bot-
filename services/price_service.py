"""
Live price data via CoinGecko API.
"""
import httpx
from typing import Optional

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_CHAIN_IDS = {
    "SOL": "solana",
    "ETH": "ethereum",
    "BNB": "binancecoin",
}

# Cache to avoid hammering the API
_price_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds


async def get_chain_prices() -> dict[str, float]:
    """Return current USD prices for SOL, ETH and BNB."""
    import time
    global _price_cache, _cache_ts

    if _price_cache and (time.time() - _cache_ts) < _CACHE_TTL:
        return _price_cache

    ids = ",".join(_CHAIN_IDS.values())
    headers = {}
    if settings.price_api_key:
        headers["x-cg-demo-api-key"] = settings.price_api_key

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_COINGECKO_BASE}/simple/price",
                params={"ids": ids, "vs_currencies": "usd"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        prices = {
            "SOL": data.get("solana", {}).get("usd", 0.0),
            "ETH": data.get("ethereum", {}).get("usd", 0.0),
            "BNB": data.get("binancecoin", {}).get("usd", 0.0),
        }
        _price_cache = prices
        _cache_ts = time.time()
        return prices

    except Exception as exc:
        logger.warning("CoinGecko price fetch failed: %s", exc)
        return _price_cache or {"SOL": 0.0, "ETH": 0.0, "BNB": 0.0}


async def get_token_price(chain: str, token_address: str) -> float:
    """Fetch USD price for an arbitrary token."""
    platform_map = {
        "SOL": "solana",
        "ETH": "ethereum",
        "BNB": "binance-smart-chain",
    }
    platform = platform_map.get(chain.upper())
    if not platform:
        return 0.0

    headers = {}
    if settings.price_api_key:
        headers["x-cg-demo-api-key"] = settings.price_api_key

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_COINGECKO_BASE}/simple/token_price/{platform}",
                params={
                    "contract_addresses": token_address,
                    "vs_currencies": "usd",
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get(token_address.lower(), {}).get("usd", 0.0)

    except Exception as exc:
        logger.warning("Token price fetch failed for %s: %s", token_address, exc)
        return 0.0

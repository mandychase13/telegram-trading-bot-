"""
Solana transaction execution.
  - Token swaps (buy / sell) via Jupiter Swap API v1
  - Native SOL transfers via solders + JSON-RPC
"""
import base64
import asyncio
import time
from decimal import Decimal
from typing import Any, Optional

import httpx
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.hash import Hash
from solders.system_program import TransferParams, transfer as _sys_transfer

from config import settings
from utils.logger import get_logger
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address

logger = get_logger(__name__)

# Jupiter retired its legacy v6 quote hostname.  The production Swap API now
# lives under api.jup.ag/swap/v1.  The lite endpoint is a valid public
# fallback for transient DNS, routing, or edge failures on the primary API.
JUPITER_API_BASE_URL = "https://api.jup.ag/swap/v1"
JUPITER_LITE_BASE_URL = "https://lite-api.jup.ag/swap/v1"
JUPITER_BASE_URLS = (JUPITER_API_BASE_URL, JUPITER_LITE_BASE_URL)
SOL_MINT          = "So11111111111111111111111111111111111111112"
JUPITER_QUOTE_URL = f"{JUPITER_API_BASE_URL}/quote"
JUPITER_SWAP_URL  = f"{JUPITER_API_BASE_URL}/swap"
_TIMEOUT          = 30
_JUPITER_GET_ATTEMPTS = 3
_JUPITER_BACKOFF_MAX_SECONDS = 65.0
# Reserve enough native SOL for a normal fee, priority fee, and a new
# associated-token-account rent deposit. The actual transaction is still
# simulated by the RPC before broadcast.
SOL_FEE_RESERVE_LAMPORTS = 5_000_000

_jupiter_gate_lock = asyncio.Lock()
_jupiter_last_request_at = 0.0


# ── helpers ────────────────────────────────────────────────────────────────────

def _kp(key: str) -> Keypair:
    """
    Accept either a hex-encoded 32-byte seed (from wallet_generator) or
    a base58-encoded keypair string.
    """
    try:
        raw = bytes.fromhex(key)
        if len(raw) == 32:
            return Keypair.from_seed(raw)
        return Keypair.from_bytes(raw)
    except Exception:
        return Keypair.from_base58_string(key)


def get_sol_signer_address(private_key: str) -> str:
    """Return the public address derived from the key that will sign a swap."""
    return str(_kp(private_key).pubkey())


async def _rpc(method: str, params: list) -> dict:
    endpoint = settings.solana_rpc_url
    host = settings.endpoint_host(endpoint)
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.post(
                    endpoint,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    logger.error("Solana RPC returned an error host=%s method=%s error=%s",
                                 host, method, data["error"])
                return data
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            logger.warning("Solana RPC network failure host=%s method=%s attempt=%d/3: %s",
                           host, method, attempt, exc)
            if attempt < 3:
                await asyncio.sleep(attempt)
        except Exception as exc:
            logger.error("Solana RPC request failed host=%s method=%s: %s", host, method, exc)
            return {}
    return {}


async def _latest_blockhash() -> Optional[str]:
    data = await _rpc("getLatestBlockhash", [{"commitment": "finalized"}])
    return data.get("result", {}).get("value", {}).get("blockhash")


async def _send_tx(signed_b64: str) -> dict:
    data = await _rpc(
        "sendTransaction",
        [signed_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )
    if "error" in data:
        return {"ok": False, "error": str(data["error"])}
    return {"ok": True, "tx_hash": data.get("result", "")}


# ── Jupiter swap ───────────────────────────────────────────────────────────────

def _jupiter_headers() -> dict[str, str]:
    """Build Jupiter headers without ever logging or exposing the API key."""
    headers = {"Accept": "application/json"}
    if settings.jupiter_api_key:
        headers["x-api-key"] = settings.jupiter_api_key
    return headers


def _is_jupiter_network_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.NetworkError,
        ),
    )


async def _wait_for_jupiter_slot() -> None:
    """Serialize requests from this process and enforce the configured spacing."""
    global _jupiter_last_request_at
    configured_interval = max(0.0, float(settings.jupiter_request_interval_seconds))
    # Jupiter's lowest documented buckets are 0.5 RPS keyless and 1 RPS
    # with a Free API key. Keep safe floors even if the setting is omitted or
    # accidentally configured too aggressively.
    minimum_interval = 2.10 if not settings.jupiter_api_key else 1.10
    interval = max(configured_interval, minimum_interval)
    async with _jupiter_gate_lock:
        now = time.monotonic()
        wait_for = (_jupiter_last_request_at + interval) - now
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        _jupiter_last_request_at = time.monotonic()


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    reset = response.headers.get("x-ratelimit-reset", "").strip()
    try:
        if reset:
            return min(_JUPITER_BACKOFF_MAX_SECONDS, max(0.0, float(reset) - time.time() + 0.1))
    except ValueError:
        pass
    value = response.headers.get("retry-after", "").strip()
    try:
        if value:
            return min(_JUPITER_BACKOFF_MAX_SECONDS, max(0.0, float(value)))
    except ValueError:
        pass
    return min(_JUPITER_BACKOFF_MAX_SECONDS, max(0.0, fallback))


async def _jupiter_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    preferred_base_url: Optional[str] = None,
    **kwargs: Any,
) -> tuple[dict, str]:
    """
    Call the current Jupiter Swap API and fail over only for transient
    network/edge errors, never for a bad trade request or API credential error.

    A successful quote is the connection preflight for a swap: the swap POST
    is never attempted unless this request reaches Jupiter and returns a
    usable route.
    """
    last_network_error: Optional[Exception] = None
    method = method.upper()
    safe_get = method == "GET"
    base_urls = JUPITER_BASE_URLS
    if preferred_base_url in JUPITER_BASE_URLS:
        base_urls = (
            preferred_base_url,
            *(base for base in JUPITER_BASE_URLS if base != preferred_base_url),
        )
    for base_url in base_urls:
        url = f"{base_url}/{path.lstrip('/')}"
        for attempt in range(1, (_JUPITER_GET_ATTEMPTS if safe_get else 1) + 1):
            await _wait_for_jupiter_slot()
            try:
                response = await client.request(
                    method,
                    url,
                    headers=_jupiter_headers(),
                    **kwargs,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Jupiter returned a non-object response")
                logger.debug("Jupiter request succeeded endpoint=%s method=%s",
                             settings.endpoint_host(url), method)
                return payload, base_url
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429:
                    if safe_get and attempt < _JUPITER_GET_ATTEMPTS:
                        delay = _retry_after_seconds(
                            exc.response, 2 ** (attempt - 1)
                        )
                        logger.warning(
                            "Jupiter rate limited endpoint=%s attempt=%d/%d; "
                            "waiting %.2fs before retry",
                            settings.endpoint_host(url), attempt,
                            _JUPITER_GET_ATTEMPTS, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError(
                        f"Jupiter rate limit (HTTP 429) at "
                        f"{settings.endpoint_host(url)}; no unsafe retry was attempted"
                    ) from exc
                if status >= 500 and safe_get and base_url != base_urls[-1]:
                    logger.warning(
                        "Jupiter server failure endpoint=%s method=%s status=%d; "
                        "trying fallback",
                        settings.endpoint_host(url), method, status,
                    )
                    break
                # Swap POSTs are deliberately not retried or sent to a second
                # endpoint after an HTTP response, avoiding duplicate build
                # requests after an ambiguous server-side failure.
                raise
            except Exception as exc:
                if not _is_jupiter_network_error(exc):
                    raise
                last_network_error = exc
                if safe_get and attempt < _JUPITER_GET_ATTEMPTS:
                    delay = min(_JUPITER_BACKOFF_MAX_SECONDS, 2 ** (attempt - 1))
                    logger.warning(
                        "Jupiter network failure endpoint=%s method=%s "
                        "attempt=%d/%d; waiting %.2fs: %s",
                        settings.endpoint_host(url), method, attempt,
                        _JUPITER_GET_ATTEMPTS, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                if safe_get and base_url != base_urls[-1]:
                    logger.warning(
                        "Jupiter network failure endpoint=%s method=%s; "
                        "trying fallback: %s",
                        settings.endpoint_host(url), method, exc,
                    )
                    break
                # A swap POST is not retried after a network error: the
                # response may have been lost after the server accepted it.
                raise

    if last_network_error is not None:
        raise last_network_error
    raise RuntimeError("No Jupiter API endpoint was available")


async def _sol_balance_lamports(pubkey: Pubkey) -> Optional[int]:
    data = await _rpc(
        "getBalance",
        [str(pubkey), {"commitment": "confirmed"}],
    )
    try:
        if "error" in data:
            return None
        return int(data["result"]["value"])
    except (KeyError, TypeError, ValueError):
        return None


async def _spl_balance(pubkey: Pubkey, mint: str) -> Optional[tuple[int, int]]:
    """Return (raw token amount, decimals), failing closed on RPC errors."""
    data = await _rpc(
        "getTokenAccountsByOwner",
        [
            str(pubkey),
            {"mint": mint},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    )
    if "error" in data:
        return None
    values = data.get("result", {}).get("value")
    if not isinstance(values, list):
        return None

    total_raw = 0
    decimals: Optional[int] = None
    try:
        for account in values:
            token_amount = (
                account["account"]["data"]["parsed"]["info"]["tokenAmount"]
            )
            current_decimals = int(token_amount["decimals"])
            if decimals is None:
                decimals = current_decimals
            elif decimals != current_decimals:
                return None
            total_raw += int(token_amount["amount"])
    except (KeyError, TypeError, ValueError):
        return None
    return total_raw, decimals or 0


async def _jupiter_swap(
    kp: Keypair,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    slippage_bps: int,
) -> dict:
    pubkey = str(kp.pubkey())
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
            quote, quote_base_url = await _jupiter_request(
                c,
                "GET",
                "/quote",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": amount_raw,
                    "slippageBps": slippage_bps,
                    "restrictIntermediateTokens": "true",
                },
            )

        if "error" in quote:
            return {"ok": False, "error": quote["error"]}
        if not quote.get("routePlan"):
            return {"ok": False, "error": "Jupiter returned no executable route"}

        logger.info(
            "Jupiter quote connection verified endpoint=%s input_mint=%s output_mint=%s",
            settings.endpoint_host(f"{quote_base_url}/quote"),
            input_mint,
            output_mint,
        )

        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
            swap_data, _ = await _jupiter_request(
                c,
                "POST",
                "/swap",
                preferred_base_url=quote_base_url,
                json={
                    "quoteResponse": quote,
                    "userPublicKey": pubkey,
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": "auto",
                },
            )

        b64 = swap_data.get("swapTransaction")
        if not b64:
            return {"ok": False, "error": "No swapTransaction in Jupiter response"}

        tx_bytes = base64.b64decode(b64)
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        signed = VersionedTransaction(vtx.message, [kp])
        return await _send_tx(base64.b64encode(bytes(signed)).decode())

    except Exception as exc:
        if _is_jupiter_network_error(exc):
            logger.error(
                "Jupiter swap network failure primary_host=%s fallback_host=%s: %s",
                settings.endpoint_host(JUPITER_QUOTE_URL),
                settings.endpoint_host(f"{JUPITER_LITE_BASE_URL}/quote"),
                exc,
            )
            return {"ok": False, "error": f"Jupiter network failure: {exc}"}
        logger.error(
            "Jupiter swap failed primary_host=%s fallback_host=%s: %s",
            settings.endpoint_host(JUPITER_QUOTE_URL),
            settings.endpoint_host(f"{JUPITER_LITE_BASE_URL}/swap"),
            exc,
        )
        return {"ok": False, "error": str(exc)}


# ── public API ─────────────────────────────────────────────────────────────────

async def execute_sol_buy(
    private_key_b58: str,
    output_mint: str,
    amount_sol: float,
    slippage_bps: int = 100,
) -> dict:
    """Swap SOL → token via Jupiter.  Returns {"ok": bool, "tx_hash"/"error": str}."""
    try:
        if not is_valid_address(output_mint, "SOL"):
            return {"ok": False, "error": INVALID_ADDRESS_MESSAGE}
        kp = _kp(private_key_b58)
        lamports = int(amount_sol * 1_000_000_000)
        if lamports <= 0:
            return {"ok": False, "error": "Buy amount must be greater than zero"}
        balance_lamports = await _sol_balance_lamports(kp.pubkey())
        if balance_lamports is None:
            return {
                "ok": False,
                "error": f"Could not verify SOL balance for execution wallet {kp.pubkey()}",
            }
        required_lamports = lamports + SOL_FEE_RESERVE_LAMPORTS
        if balance_lamports < required_lamports:
            return {
                "ok": False,
                "error": (
                    f"Insufficient SOL in execution wallet {kp.pubkey()}: "
                    f"have {balance_lamports / 1_000_000_000:.9f} SOL, "
                    f"need at least {required_lamports / 1_000_000_000:.9f} SOL "
                    f"including fee/rent reserve"
                ),
            }
        logger.info(
            "Solana BUY execution wallet=%s input_lamports=%d "
            "balance_lamports=%d",
            kp.pubkey(), lamports, balance_lamports,
        )
        return await _jupiter_swap(kp, SOL_MINT, output_mint, lamports, slippage_bps)
    except Exception as exc:
        logger.error("execute_sol_buy: %s", exc)
        return {"ok": False, "error": str(exc)}


async def execute_sol_sell(
    private_key_b58: str,
    input_mint: str,
    amount_tokens: float,
    token_decimals: int = 6,
    slippage_bps: int = 100,
) -> dict:
    """Swap token → SOL via Jupiter."""
    try:
        if not is_valid_address(input_mint, "SOL"):
            return {"ok": False, "error": INVALID_ADDRESS_MESSAGE}
        kp = _kp(private_key_b58)
        if amount_tokens <= 0:
            return {"ok": False, "error": "Sell amount must be greater than zero"}

        balance_lamports = await _sol_balance_lamports(kp.pubkey())
        if balance_lamports is None:
            return {
                "ok": False,
                "error": f"Could not verify SOL fee balance for execution wallet {kp.pubkey()}",
            }
        if balance_lamports < SOL_FEE_RESERVE_LAMPORTS:
            return {
                "ok": False,
                "error": (
                    f"Insufficient SOL for fees in execution wallet {kp.pubkey()}: "
                    f"have {balance_lamports / 1_000_000_000:.9f} SOL, "
                    f"need at least {SOL_FEE_RESERVE_LAMPORTS / 1_000_000_000:.9f} SOL"
                ),
            }

        token_balance = await _spl_balance(kp.pubkey(), input_mint)
        if token_balance is None:
            return {
                "ok": False,
                "error": (
                    f"Could not verify token balance for execution wallet "
                    f"{kp.pubkey()}"
                ),
            }
        available_raw, actual_decimals = token_balance
        amount_raw = int(
            Decimal(str(amount_tokens)) * (10 ** actual_decimals)
        )
        if amount_raw <= 0:
            return {"ok": False, "error": "Sell amount is below the token precision"}
        if amount_raw > available_raw:
            available_ui = available_raw / (10 ** actual_decimals)
            return {
                "ok": False,
                "error": (
                    f"Insufficient {input_mint} tokens in execution wallet "
                    f"{kp.pubkey()}: requested {amount_tokens}, "
                    f"available {available_ui}"
                ),
            }
        logger.info(
            "Solana SELL execution wallet=%s input_mint=%s "
            "requested_raw=%d available_raw=%d decimals=%d",
            kp.pubkey(), input_mint, amount_raw, available_raw, actual_decimals,
        )
        return await _jupiter_swap(kp, input_mint, SOL_MINT, amount_raw, slippage_bps)
    except Exception as exc:
        logger.error("execute_sol_sell: %s", exc)
        return {"ok": False, "error": str(exc)}


async def execute_sol_transfer(
    private_key_b58: str,
    destination: str,
    amount_sol: float,
) -> dict:
    """Send native SOL to a destination address."""
    try:
        if not is_valid_address(destination, "SOL"):
            return {"ok": False, "error": INVALID_ADDRESS_MESSAGE}
        kp = _kp(private_key_b58)
        lamports = int(amount_sol * 1_000_000_000)

        blockhash = await _latest_blockhash()
        if not blockhash:
            return {"ok": False, "error": "Could not fetch blockhash"}

        dest = Pubkey.from_string(destination)
        ix = _sys_transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=lamports))
        msg = MessageV0.try_compile(
            payer=kp.pubkey(),
            instructions=[ix],
            address_lookup_table_accounts=[],
            recent_blockhash=Hash.from_string(blockhash),
        )
        tx = VersionedTransaction(msg, [kp])
        return await _send_tx(base64.b64encode(bytes(tx)).decode())

    except Exception as exc:
        logger.error("execute_sol_transfer: %s", exc)
        return {"ok": False, "error": str(exc)}

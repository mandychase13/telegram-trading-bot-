"""
Solana transaction execution.
  - Token swaps (buy / sell) via Jupiter V6 API
  - Native SOL transfers via solders + JSON-RPC
"""
import base64
import asyncio
from typing import Optional

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

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"
SOL_MINT          = "So11111111111111111111111111111111111111112"
_TIMEOUT          = 30


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
    except (ValueError, Exception):
        return Keypair.from_base58_string(key)


async def _rpc(method: str, params: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                settings.solana_rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.error("Solana RPC [%s]: %s", method, exc)
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

async def _jupiter_swap(
    kp: Keypair,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    slippage_bps: int,
) -> dict:
    pubkey = str(kp.pubkey())
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(JUPITER_QUOTE_URL, params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_raw,
                "slippageBps": slippage_bps,
            })
            r.raise_for_status()
            quote = r.json()

        if "error" in quote:
            return {"ok": False, "error": quote["error"]}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                JUPITER_SWAP_URL,
                json={
                    "quoteResponse": quote,
                    "userPublicKey": pubkey,
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": "auto",
                },
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            swap_data = r.json()

        b64 = swap_data.get("swapTransaction")
        if not b64:
            return {"ok": False, "error": "No swapTransaction in Jupiter response"}

        tx_bytes = base64.b64decode(b64)
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        signed = VersionedTransaction(vtx.message, [kp])
        return await _send_tx(base64.b64encode(bytes(signed)).decode())

    except Exception as exc:
        logger.error("Jupiter swap error: %s", exc)
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
        amount_raw = int(amount_tokens * (10 ** token_decimals))
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

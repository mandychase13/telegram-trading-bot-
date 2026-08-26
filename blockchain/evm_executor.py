"""
EVM transaction execution for Ethereum and BNB Chain.
  - Native transfers (ETH / BNB)
  - Token swaps via Uniswap V2 (ETH) or PancakeSwap V2 (BNB)
All signing uses eth_account; broadcasting uses raw JSON-RPC.
web3.py is used only inside asyncio.to_thread for ABI encoding (avoids event-loop conflicts).
"""
import asyncio
from typing import Optional

import httpx
from eth_account import Account

from config import settings
from utils.logger import get_logger
from utils.address_validation import INVALID_ADDRESS_MESSAGE, is_valid_address

logger = get_logger(__name__)

# ── constants ──────────────────────────────────────────────────────────────────

UNISWAP_V2_ROUTER    = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
PANCAKESWAP_V2_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

CHAIN_IDS = {"ETH": 1, "BNB": 56}

# Minimal Uniswap/PancakeSwap V2 Router ABI (only what we need)
_ROUTER_ABI = [
    {
        "name": "swapExactETHForTokens",
        "type": "function",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256[]"}],
        "stateMutability": "payable",
    },
    {
        "name": "swapExactTokensForETH",
        "type": "function",
        "inputs": [
            {"name": "amountIn",     "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "getAmountsOut",
        "type": "function",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path",     "type": "address[]"},
        ],
        "outputs": [{"name": "", "type": "uint256[]"}],
        "stateMutability": "view",
    },
]

# Minimal ERC-20 ABI (approve only)
_ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
]

_TIMEOUT    = 30
_GAS_BUFFER = 1.2   # multiply estimated gas by this


# ── low-level RPC helpers ──────────────────────────────────────────────────────

def _rpc_url(chain: str) -> str:
    return settings.ethereum_rpc_url if chain == "ETH" else settings.bnb_rpc_endpoint


async def _rpc(chain: str, method: str, params: list) -> dict:
    endpoint = _rpc_url(chain)
    host = settings.endpoint_host(endpoint)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                endpoint,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.error("EVM RPC failure chain=%s host=%s method=%s: %s",
                     chain, host, method, exc)
        return {}


async def _get_nonce(chain: str, address: str) -> int:
    data = await _rpc(chain, "eth_getTransactionCount", [address, "pending"])
    return int(data.get("result", "0x0"), 16)


async def _get_gas_price(chain: str) -> int:
    data = await _rpc(chain, "eth_gasPrice", [])
    return int(data.get("result", "0x0"), 16)


async def _estimate_gas(chain: str, tx: dict) -> int:
    data = await _rpc(chain, "eth_estimateGas", [tx])
    raw = data.get("result", "0x5208")
    try:
        return int(raw, 16)
    except Exception:
        return 200_000


async def _send_raw(chain: str, raw_hex: str) -> dict:
    data = await _rpc(chain, "eth_sendRawTransaction", [raw_hex])
    if "error" in data:
        return {"ok": False, "error": str(data["error"])}
    return {"ok": True, "tx_hash": data.get("result", "")}


async def _get_erc20_decimals(chain: str, token_address: str) -> int:
    """Call decimals() on an ERC-20 contract."""
    data = await _rpc(chain, "eth_call", [
        {"to": token_address, "data": "0x313ce567"},
        "latest",
    ])
    raw = data.get("result", "0x12")
    try:
        return int(raw, 16)
    except Exception:
        return 18


# ── sign & send helpers ───────────────────────────────────────────────────────

def _sign_and_send_sync(chain: str, rpc_url: str, private_key: str, tx_dict: dict) -> str:
    """
    Sync helper (run inside asyncio.to_thread).
    Uses web3.py for gas estimation / chain ID, eth_account for signing.
    Returns raw signed tx hex.
    """
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    acct = Account.from_key(private_key)

    # web3.py validates transaction address fields strictly when signing.
    # User-entered EVM addresses may be valid lowercase addresses, but the
    # signer requires the checksum representation in the transaction dict.
    if isinstance(tx_dict.get("to"), str):
        tx_dict["to"] = w3.to_checksum_address(tx_dict["to"].strip())
    tx_dict["from"] = w3.to_checksum_address(tx_dict.get("from", acct.address))

    # Fill in missing fields
    if "chainId" not in tx_dict:
        tx_dict["chainId"] = CHAIN_IDS.get(chain, 1)
    if "nonce" not in tx_dict:
        tx_dict["nonce"] = w3.eth.get_transaction_count(acct.address, "pending")
    if "gasPrice" not in tx_dict:
        tx_dict["gasPrice"] = w3.eth.gas_price
    if "gas" not in tx_dict:
        try:
            tx_dict["gas"] = int(w3.eth.estimate_gas(tx_dict) * _GAS_BUFFER)
        except Exception:
            tx_dict["gas"] = 250_000

    signed = w3.eth.account.sign_transaction(tx_dict, private_key)
    # web3.py 6.x uses ``rawTransaction`` while newer releases expose
    # ``raw_transaction``. Support both so signing works across environments.
    raw_transaction = getattr(signed, "raw_transaction", None)
    if raw_transaction is None:
        raw_transaction = getattr(signed, "rawTransaction", None)
    if raw_transaction is None:
        raise RuntimeError("Signed transaction did not contain raw transaction bytes")
    return raw_transaction.hex()


async def _build_and_send(chain: str, private_key: str, tx_dict: dict) -> dict:
    try:
        rpc_url = _rpc_url(chain)
        raw_hex = await asyncio.to_thread(_sign_and_send_sync, chain, rpc_url, private_key, tx_dict)
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        return await _send_raw(chain, raw_hex)
    except Exception as exc:
        logger.error("_build_and_send [%s]: %s", chain, exc)
        return {"ok": False, "error": str(exc)}


# ── public API ─────────────────────────────────────────────────────────────────

async def execute_evm_transfer(
    private_key_hex: str,
    to_address: str,
    amount_native: float,
    chain: str = "ETH",
) -> dict:
    """
    Send native ETH or BNB to an address.
    Returns {"ok": bool, "tx_hash"/"error": str}.
    """
    try:
        if not is_valid_address(to_address, chain):
            return {"ok": False, "error": INVALID_ADDRESS_MESSAGE}
        acct = Account.from_key(private_key_hex)
        wei = int(amount_native * 1e18)
        if wei <= 0:
            return {"ok": False, "error": "Transfer amount must be greater than zero."}

        # Native transfers need both the requested amount and gas. Catch
        # attempts to drain the wallet before signing, with a useful message.
        balance_data = await _rpc(chain, "eth_getBalance", [acct.address, "pending"])
        gas_price = await _get_gas_price(chain)
        balance_hex = balance_data.get("result")
        if not balance_hex or gas_price <= 0:
            return {
                "ok": False,
                "error": f"Unable to read the current {chain} balance or gas price. Please retry.",
            }
        balance_wei = int(balance_hex, 16)
        gas_wei = gas_price * int(21_000 * _GAS_BUFFER)
        minimum_required = wei + gas_wei
        if balance_wei < minimum_required:
            available_wei = max(0, balance_wei - gas_wei)
            available_native = available_wei / 1e18
            required_native = minimum_required / 1e18
            return {
                "ok": False,
                "error": (
                    f"Insufficient {chain} balance for amount plus gas. "
                    f"Maximum sendable now: {available_native:.8f} {chain}; "
                    f"required balance: {required_native:.8f} {chain}."
                ),
            }

        tx = {
            "to":    to_address,
            "value": hex(wei),
            "from":  acct.address,
            "data":  "0x",
        }
        return await _build_and_send(chain, private_key_hex, tx)
    except Exception as exc:
        logger.error("execute_evm_transfer [%s]: %s", chain, exc)
        return {"ok": False, "error": str(exc)}


async def execute_evm_buy(
    private_key_hex: str,
    token_address: str,
    amount_native: float,
    slippage: float = 0.01,
    chain: str = "ETH",
) -> dict:
    """
    Swap ETH/BNB → ERC-20 token via Uniswap V2 / PancakeSwap V2.
    """
    if not is_valid_address(token_address, chain):
        return {"ok": False, "error": INVALID_ADDRESS_MESSAGE}

    def _sync_buy(rpc_url, pk, token, amount_wei, slip, chain_id, router_addr, wrapped):
        from web3 import Web3
        import time

        w3 = Web3(Web3.HTTPProvider(rpc_url))
        acct = Account.from_key(pk)
        checksum_token = w3.to_checksum_address(token)
        checksum_router = w3.to_checksum_address(router_addr)
        checksum_wrapped = w3.to_checksum_address(wrapped)

        router = w3.eth.contract(address=checksum_router, abi=_ROUTER_ABI)

        # Get expected output for slippage calculation
        path = [checksum_wrapped, checksum_token]
        try:
            amounts = router.functions.getAmountsOut(amount_wei, path).call()
            min_out = int(amounts[1] * (1 - slip))
        except Exception:
            min_out = 0

        deadline = int(time.time()) + 300
        tx = router.functions.swapExactETHForTokens(
            min_out, path, acct.address, deadline
        ).build_transaction({
            "from":     acct.address,
            "value":    amount_wei,
            "chainId":  chain_id,
            "nonce":    w3.eth.get_transaction_count(acct.address, "pending"),
            "gasPrice": w3.eth.gas_price,
        })
        try:
            tx["gas"] = int(w3.eth.estimate_gas(tx) * _GAS_BUFFER)
        except Exception:
            tx["gas"] = 300_000

        signed = w3.eth.account.sign_transaction(tx, pk)
        return signed.raw_transaction.hex()

    try:
        amount_wei = int(amount_native * 1e18)
        router_addr = UNISWAP_V2_ROUTER if chain == "ETH" else PANCAKESWAP_V2_ROUTER
        wrapped     = WETH if chain == "ETH" else WBNB
        chain_id    = CHAIN_IDS.get(chain, 1)
        rpc_url     = _rpc_url(chain)

        raw_hex = await asyncio.to_thread(
            _sync_buy,
            rpc_url, private_key_hex, token_address,
            amount_wei, slippage, chain_id, router_addr, wrapped,
        )
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        return await _send_raw(chain, raw_hex)
    except Exception as exc:
        logger.error("execute_evm_buy [%s]: %s", chain, exc)
        return {"ok": False, "error": str(exc)}


async def execute_evm_sell(
    private_key_hex: str,
    token_address: str,
    amount_tokens: float,
    chain: str = "ETH",
    slippage: float = 0.01,
) -> dict:
    """
    Swap ERC-20 token → ETH/BNB.  First approves the router, then swaps.
    """
    if not is_valid_address(token_address, chain):
        return {"ok": False, "error": INVALID_ADDRESS_MESSAGE}

    def _sync_sell(rpc_url, pk, token, amount_raw, slip, chain_id, router_addr, wrapped):
        from web3 import Web3
        import time

        w3 = Web3(Web3.HTTPProvider(rpc_url))
        acct = Account.from_key(pk)
        checksum_token  = w3.to_checksum_address(token)
        checksum_router = w3.to_checksum_address(router_addr)
        checksum_wrapped = w3.to_checksum_address(wrapped)

        # 1. Approve router to spend tokens
        erc20 = w3.eth.contract(address=checksum_token, abi=_ERC20_ABI)
        nonce = w3.eth.get_transaction_count(acct.address, "pending")
        approve_tx = erc20.functions.approve(
            checksum_router, amount_raw
        ).build_transaction({
            "from":     acct.address,
            "chainId":  chain_id,
            "nonce":    nonce,
            "gasPrice": w3.eth.gas_price,
            "gas":      100_000,
        })
        signed_approve = w3.eth.account.sign_transaction(approve_tx, pk)
        w3.eth.send_raw_transaction(signed_approve.raw_transaction)

        # 2. Swap tokens → ETH/BNB
        router = w3.eth.contract(address=checksum_router, abi=_ROUTER_ABI)
        path = [checksum_token, checksum_wrapped]
        try:
            amounts = router.functions.getAmountsOut(amount_raw, path).call()
            min_out = int(amounts[1] * (1 - slip))
        except Exception:
            min_out = 0

        deadline = int(time.time()) + 300
        nonce += 1
        swap_tx = router.functions.swapExactTokensForETH(
            amount_raw, min_out, path, acct.address, deadline
        ).build_transaction({
            "from":     acct.address,
            "chainId":  chain_id,
            "nonce":    nonce,
            "gasPrice": w3.eth.gas_price,
        })
        try:
            swap_tx["gas"] = int(w3.eth.estimate_gas(swap_tx) * _GAS_BUFFER)
        except Exception:
            swap_tx["gas"] = 300_000

        signed_swap = w3.eth.account.sign_transaction(swap_tx, pk)
        return signed_swap.raw_transaction.hex()

    try:
        decimals    = await _get_erc20_decimals(chain, token_address)
        amount_raw  = int(amount_tokens * (10 ** decimals))
        router_addr = UNISWAP_V2_ROUTER if chain == "ETH" else PANCAKESWAP_V2_ROUTER
        wrapped     = WETH if chain == "ETH" else WBNB
        chain_id    = CHAIN_IDS.get(chain, 1)
        rpc_url     = _rpc_url(chain)

        raw_hex = await asyncio.to_thread(
            _sync_sell,
            rpc_url, private_key_hex, token_address,
            amount_raw, slippage, chain_id, router_addr, wrapped,
        )
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        return await _send_raw(chain, raw_hex)
    except Exception as exc:
        logger.error("execute_evm_sell [%s]: %s", chain, exc)
        return {"ok": False, "error": str(exc)}

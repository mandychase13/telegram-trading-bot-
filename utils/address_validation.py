"""Chain-aware validation for wallet and token addresses.

Validation is deliberately local and fast. It never performs an RPC request,
so invalid user input is rejected immediately without waiting on a provider.
"""
from __future__ import annotations

from typing import Optional


INVALID_ADDRESS_MESSAGE = "❌ Invalid wallet address. Please check the address and try again."


def normalize_chain(chain: str | None) -> str:
    value = (chain or "").strip().upper()
    if value == "SOLANA":
        return "SOL"
    if value in {"ETHEREUM", "EVM"}:
        return "ETH"
    if value in {"BSC", "BINANCE", "BINANCE SMART CHAIN"}:
        return "BNB"
    return value


def is_valid_address(address: str | None, chain: str | None) -> bool:
    """Return whether *address* is syntactically valid for *chain*."""
    value = (address or "").strip()
    network = normalize_chain(chain)
    if not value or not network:
        return False

    if network == "SOL":
        try:
            from solders.pubkey import Pubkey

            Pubkey.from_string(value)
            return True
        except Exception:
            return False

    # ETH, BNB, and future EVM-compatible networks share the same address
    # format. Web3.is_address also validates the 0x prefix and 40 hex digits.
    if network in {"ETH", "BNB"} or network not in {"SOL"}:
        try:
            from web3 import Web3

            return bool(Web3.is_address(value))
        except Exception:
            return False

    return False


def validate_address(address: str | None, chain: str | None) -> Optional[str]:
    """Return a trimmed address or ``None`` when it is invalid."""
    value = (address or "").strip()
    return value if is_valid_address(value, chain) else None
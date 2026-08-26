"""
Cryptographic wallet generation for Solana, Ethereum, and BNB Chain.
Returns (address, private_key_hex) tuples – never log or persist the
raw private key; always encrypt before storage.
"""
from utils.logger import get_logger

logger = get_logger(__name__)


def generate_solana_wallet() -> tuple[str, str]:
    """Generate a new Solana keypair and return (address, private_key_hex)."""
    from solders.keypair import Keypair
    kp = Keypair()
    address = str(kp.pubkey())
    # 64-byte secret: first 32 are the seed, next 32 are the pubkey
    private_key_hex = kp.secret().hex()
    logger.debug("Generated Solana wallet: %s", address)
    return address, private_key_hex


def generate_eth_wallet() -> tuple[str, str]:
    """Generate a new Ethereum keypair and return (address, private_key_hex)."""
    from eth_account import Account
    acct = Account.create()
    logger.debug("Generated Ethereum wallet: %s", acct.address)
    return acct.address, acct.key.hex()


def generate_bnb_wallet() -> tuple[str, str]:
    """
    BNB Chain is EVM-compatible – generate using the same method as Ethereum.
    The same address format is used on both chains.
    """
    from eth_account import Account
    acct = Account.create()
    logger.debug("Generated BNB wallet: %s", acct.address)
    return acct.address, acct.key.hex()

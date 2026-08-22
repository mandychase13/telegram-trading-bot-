"""
Creates wallets for new users: generates keypairs for all three chains,
encrypts the private keys, and stores everything in the database.
"""
from blockchain.wallet_generator import generate_solana_wallet, generate_eth_wallet, generate_bnb_wallet
from database.operations import create_wallet, get_wallet, log_wallet_audit
from utils.encryption import encrypt
from utils.logger import get_logger
from config import settings

logger = get_logger(__name__)


async def create_user_wallets(user_id: int) -> dict:
    """
    Generate SOL / ETH / BNB wallets for user_id and persist them.
    Returns the wallet record dict.
    Idempotent – returns existing wallets if already created.
    """
    existing = await get_wallet(user_id)
    if existing:
        logger.debug("Wallets already exist for user_id=%s", user_id)
        return existing

    sol_addr, sol_pk = generate_solana_wallet()
    eth_addr, eth_pk = generate_eth_wallet()
    bnb_addr, bnb_pk = generate_bnb_wallet()

    enc_key = settings.encryption_key
    sol_pk_enc = encrypt(sol_pk, enc_key)
    eth_pk_enc = encrypt(eth_pk, enc_key)
    bnb_pk_enc = encrypt(bnb_pk, enc_key)

    wallet = await create_wallet(
        user_id=user_id,
        sol_address=sol_addr,
        sol_pk_enc=sol_pk_enc,
        eth_address=eth_addr,
        eth_pk_enc=eth_pk_enc,
        bnb_address=bnb_addr,
        bnb_pk_enc=bnb_pk_enc,
    )
    logger.info("Created wallets for user_id=%s  SOL=%s  ETH=%s  BNB=%s",
                user_id, sol_addr, eth_addr, bnb_addr)

    # Audit log one entry per chain (address hints only — no keys)
    for chain, address in (("SOL", sol_addr), ("ETH", eth_addr), ("BNB", bnb_addr)):
        await log_wallet_audit(
            user_id=user_id,
            action="WALLET_CREATED",
            chain=chain,
            address=address,
            details="Bot-generated keypair",
        )

    return wallet

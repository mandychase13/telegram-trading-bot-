"""
All database CRUD operations.
Every public function is async – sync DB calls are wrapped in asyncio.to_thread via
the connection helpers.
"""
from typing import Optional
from datetime import datetime

from .connection import execute, fetchone, fetchall, fetchone_returning
from utils.logger import get_logger

logger = get_logger(__name__)


async def get_internal_balances(user_id: int) -> dict[str, dict]:
    rows = await fetchall(
        """
        SELECT asset, network, balance, display_mode, updated_at
        FROM internal_balances WHERE user_id = %s
        """,
        (user_id,),
    )
    return {f"{row['asset']}:{row['network']}": row for row in rows}


async def get_internal_balance(user_id: int, asset: str, network: str) -> Optional[dict]:
    return await fetchone(
        """
        SELECT asset, network, balance, display_mode, updated_at
        FROM internal_balances
        WHERE user_id = %s AND asset = %s AND network = %s
        """,
        (user_id, asset.upper(), network.upper()),
    )


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> dict:
    existing = await fetchone(
        "SELECT * FROM users WHERE telegram_id = %s", (telegram_id,)
    )
    if existing:
        return existing
    row = await fetchone_returning(
        """
        INSERT INTO users (telegram_id, username, first_name, last_name)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (telegram_id, username, first_name, last_name),
    )
    # Also create default settings
    await execute(
        "INSERT INTO user_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (row["id"],),
    )
    logger.info("Created new user telegram_id=%s", telegram_id)
    return row


async def get_user(telegram_id: int) -> Optional[dict]:
    return await fetchone("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))


# ── Wallets ───────────────────────────────────────────────────────────────────

async def get_wallet(user_id: int) -> Optional[dict]:
    return await fetchone("SELECT * FROM wallets WHERE user_id = %s", (user_id,))


async def update_wallet_chain(
    user_id: int,
    chain: str,
    address: str,
    pk_enc: str,
    mnemonic_enc: Optional[str] = None,
    source: str = "imported_pk",
) -> None:
    """
    Overwrite one chain's address + encrypted private key on the existing
    wallet row.  Optionally stores an encrypted mnemonic for the chain.
    Column names are derived from the validated chain constant —
    never from raw user input.
    """
    chain = chain.upper()
    if chain == "SOL":
        addr_col, pk_col, mnemo_col = "sol_address", "sol_pk_enc", "sol_mnemonic_enc"
    elif chain == "ETH":
        addr_col, pk_col, mnemo_col = "eth_address", "eth_pk_enc", "eth_mnemonic_enc"
    elif chain == "BNB":
        addr_col, pk_col, mnemo_col = "bnb_address", "bnb_pk_enc", "bnb_mnemonic_enc"
    else:
        raise ValueError(f"Unknown chain: {chain}")

    if mnemonic_enc is not None:
        await execute(
            f"UPDATE wallets SET {addr_col} = %s, {pk_col} = %s, {mnemo_col} = %s WHERE user_id = %s",
            (address, pk_enc, mnemonic_enc, user_id),
        )
    else:
        await execute(
            f"UPDATE wallets SET {addr_col} = %s, {pk_col} = %s WHERE user_id = %s",
            (address, pk_enc, user_id),
        )

    # Keep wallet_metadata in sync
    await upsert_wallet_metadata(
        user_id=user_id,
        chain=chain,
        address=address,
        source=source,
        has_mnemonic=(mnemonic_enc is not None),
    )


async def create_wallet(
    user_id: int,
    sol_address: str, sol_pk_enc: str,
    eth_address: str, eth_pk_enc: str,
    bnb_address: str, bnb_pk_enc: str = "",
) -> dict:
    wallet = await fetchone_returning(
        """
        INSERT INTO wallets (user_id, sol_address, sol_pk_enc, eth_address, eth_pk_enc, bnb_address, bnb_pk_enc)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (user_id, sol_address, sol_pk_enc, eth_address, eth_pk_enc, bnb_address, bnb_pk_enc),
    )
    # Seed per-chain metadata rows for the generated wallets
    for chain, address in (
        ("SOL", sol_address),
        ("ETH", eth_address),
        ("BNB", bnb_address),
    ):
        if address:
            await upsert_wallet_metadata(
                user_id=user_id,
                chain=chain,
                address=address,
                source="generated",
                has_mnemonic=False,
            )
    return wallet


# ── Wallet metadata ───────────────────────────────────────────────────────────

async def upsert_wallet_metadata(
    user_id: int,
    chain: str,
    address: str,
    source: str = "generated",
    has_mnemonic: bool = False,
    label: str = "",
) -> None:
    """
    Insert or update the per-chain metadata row for this user.
    source must be one of: generated | imported_pk | imported_mnemonic
    """
    chain = chain.upper()
    await execute(
        """
        INSERT INTO wallet_metadata (user_id, chain, address, source, has_mnemonic, label)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, chain)
        DO UPDATE SET
            address      = EXCLUDED.address,
            source       = EXCLUDED.source,
            has_mnemonic = EXCLUDED.has_mnemonic,
            label        = COALESCE(NULLIF(EXCLUDED.label, ''), wallet_metadata.label),
            updated_at   = NOW()
        """,
        (user_id, chain, address, source, has_mnemonic, label),
    )


async def get_wallet_metadata(user_id: int) -> list[dict]:
    """Return all per-chain metadata rows for a user, ordered by chain."""
    return await fetchall(
        "SELECT * FROM wallet_metadata WHERE user_id = %s ORDER BY chain",
        (user_id,),
    )


async def get_wallet_metadata_chain(user_id: int, chain: str) -> Optional[dict]:
    """Return metadata for a single chain."""
    return await fetchone(
        "SELECT * FROM wallet_metadata WHERE user_id = %s AND chain = %s",
        (user_id, chain.upper()),
    )


# ── Audit log ─────────────────────────────────────────────────────────────────

async def log_wallet_audit(
    user_id: int,
    action: str,
    chain: Optional[str] = None,
    address: Optional[str] = None,
    details: str = "",
) -> None:
    """
    Append an immutable audit entry.
    action:  WALLET_CREATED | WALLET_IMPORTED | MNEMONIC_STORED |
             ADDRESS_REPLACED | WITHDRAWAL_APPROVED | WITHDRAWAL_REJECTED | WITHDRAWAL_FAILED
    address_hint: first 10 chars of the public address — never a private key or mnemonic.
    details: human-readable context — must never contain secrets.
    """
    address_hint = address[:10] + "…" if address and len(address) > 10 else (address or "")
    await execute(
        """
        INSERT INTO wallet_audit_log (user_id, action, chain, address_hint, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, action, chain, address_hint, details),
    )


async def get_wallet_audit_log(user_id: int, limit: int = 50) -> list[dict]:
    """Return the most recent audit entries for a user."""
    return await fetchall(
        """
        SELECT * FROM wallet_audit_log
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (user_id, limit),
    )


# ── Settings ──────────────────────────────────────────────────────────────────

async def get_user_settings(user_id: int) -> Optional[dict]:
    return await fetchone("SELECT * FROM user_settings WHERE user_id = %s", (user_id,))


async def update_user_settings(user_id: int, **kwargs) -> None:
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    await execute(
        f"UPDATE user_settings SET {set_clause}, updated_at = NOW() WHERE user_id = %s",
        tuple(values),
    )


# ── Followed wallets ──────────────────────────────────────────────────────────

async def get_followed_wallets(user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM followed_wallets WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,),
    )


async def get_active_followed_wallets_all() -> list[dict]:
    """Return all active followed wallets across all users (for the copy engine)."""
    return await fetchall(
        """
        SELECT fw.*, u.telegram_id
        FROM followed_wallets fw
        JOIN users u ON u.id = fw.user_id
        WHERE fw.is_active = TRUE
        """,
    )


async def add_followed_wallet(
    user_id: int, chain: str, wallet_address: str, label: Optional[str] = None
) -> dict:
    return await fetchone_returning(
        """
        INSERT INTO followed_wallets (user_id, chain, wallet_address, label)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, chain, wallet_address)
        DO UPDATE SET is_active = TRUE, label = EXCLUDED.label
        RETURNING *
        """,
        (user_id, chain, wallet_address, label),
    )


async def remove_followed_wallet(followed_wallet_id: int, user_id: int) -> None:
    await execute(
        "DELETE FROM followed_wallets WHERE id = %s AND user_id = %s",
        (followed_wallet_id, user_id),
    )


async def toggle_followed_wallet(followed_wallet_id: int, user_id: int, is_active: bool) -> None:
    await execute(
        "UPDATE followed_wallets SET is_active = %s WHERE id = %s AND user_id = %s",
        (is_active, followed_wallet_id, user_id),
    )


async def update_wallet_last_checked(followed_wallet_id: int, last_tx_sig: Optional[str] = None) -> None:
    await execute(
        """
        UPDATE followed_wallets
        SET last_checked_at = NOW(), last_tx_sig = COALESCE(%s, last_tx_sig)
        WHERE id = %s
        """,
        (last_tx_sig, followed_wallet_id),
    )


# ── Copy settings ─────────────────────────────────────────────────────────────

async def get_copy_settings(user_id: int, followed_wallet_id: int) -> Optional[dict]:
    return await fetchone(
        "SELECT * FROM copy_settings WHERE user_id = %s AND followed_wallet_id = %s",
        (user_id, followed_wallet_id),
    )


async def upsert_copy_settings(user_id: int, followed_wallet_id: int, **kwargs) -> None:
    defaults = dict(
        copy_percentage=10.0,
        max_trade_amount=1.0,
        min_trade_amount=0.0,
        slippage=1.0,
        priority_fee=0.001,
    )
    defaults.update(kwargs)
    await execute(
        """
        INSERT INTO copy_settings
            (user_id, followed_wallet_id, copy_percentage, max_trade_amount, min_trade_amount, slippage, priority_fee)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, followed_wallet_id)
        DO UPDATE SET
            copy_percentage  = EXCLUDED.copy_percentage,
            max_trade_amount = EXCLUDED.max_trade_amount,
            min_trade_amount = EXCLUDED.min_trade_amount,
            slippage         = EXCLUDED.slippage,
            priority_fee     = EXCLUDED.priority_fee,
            updated_at       = NOW()
        """,
        (
            user_id, followed_wallet_id,
            defaults["copy_percentage"], defaults["max_trade_amount"],
            defaults["min_trade_amount"], defaults["slippage"],
            defaults["priority_fee"],
        ),
    )


# ── Trades ────────────────────────────────────────────────────────────────────

async def save_trade(
    user_id: int,
    chain: str,
    trade_type: str,
    token_address: str,
    token_symbol: str,
    amount_in: float,
    amount_out: float = 0.0,
    tx_hash: str = "",
    is_copy_trade: bool = False,
    followed_wallet_id: Optional[int] = None,
    status: str = "pending",
) -> dict:
    return await fetchone_returning(
        """
        INSERT INTO trades
            (user_id, chain, trade_type, token_address, token_symbol,
             amount_in, amount_out, tx_hash, is_copy_trade, followed_wallet_id, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            user_id, chain, trade_type, token_address, token_symbol,
            amount_in, amount_out, tx_hash, is_copy_trade, followed_wallet_id, status,
        ),
    )


async def get_trades(user_id: int, limit: int = 20) -> list[dict]:
    return await fetchall(
        "SELECT * FROM trades WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )


async def update_trade_status(trade_id: int, status: str, tx_hash: str = "") -> None:
    await execute(
        "UPDATE trades SET status = %s, tx_hash = %s WHERE id = %s",
        (status, tx_hash, trade_id),
    )


# ── Portfolio ─────────────────────────────────────────────────────────────────

async def get_portfolio_tokens(user_id: int) -> list[dict]:
    return await fetchall(
        "SELECT * FROM portfolio_tokens WHERE user_id = %s AND balance > 0 ORDER BY updated_at DESC",
        (user_id,),
    )


async def upsert_portfolio_token(
    user_id: int,
    chain: str,
    token_address: str,
    token_symbol: str,
    balance: float,
    avg_buy_price: float = 0.0,
) -> None:
    await execute(
        """
        INSERT INTO portfolio_tokens (user_id, chain, token_address, token_symbol, balance, avg_buy_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, chain, token_address)
        DO UPDATE SET balance = EXCLUDED.balance, avg_buy_price = EXCLUDED.avg_buy_price, updated_at = NOW()
        """,
        (user_id, chain, token_address, token_symbol, balance, avg_buy_price),
    )


# ── Auto-trade settings ───────────────────────────────────────────────────────

async def get_autotrade_settings(user_id: int, chain: str) -> Optional[dict]:
    return await fetchone(
        "SELECT * FROM autotrade_settings WHERE user_id = %s AND chain = %s",
        (user_id, chain),
    )


async def upsert_autotrade_settings(user_id: int, chain: str, **kwargs) -> None:
    existing = await get_autotrade_settings(user_id, chain)
    if not existing:
        await execute(
            "INSERT INTO autotrade_settings (user_id, chain) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, chain),
        )
    if kwargs:
        set_clause = ", ".join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [user_id, chain]
        await execute(
            f"UPDATE autotrade_settings SET {set_clause}, updated_at = NOW() WHERE user_id = %s AND chain = %s",
            tuple(values),
        )


# ── Withdrawal requests ───────────────────────────────────────────────────────

async def create_withdrawal_request(
    user_id: int,
    chain: str,
    to_address: str,
    amount: float,
) -> dict:
    return await fetchone_returning(
        """
        INSERT INTO withdrawal_requests (user_id, chain, to_address, amount)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (user_id, chain, to_address, amount),
    )


async def get_withdrawal_request(wd_id: int) -> Optional[dict]:
    return await fetchone(
        "SELECT * FROM withdrawal_requests WHERE id = %s", (wd_id,)
    )


async def update_withdrawal_status(
    wd_id: int,
    status: str,
    tx_hash: str = "",
    admin_note: str = "",
) -> None:
    await execute(
        """
        UPDATE withdrawal_requests
        SET status = %s, tx_hash = %s, admin_note = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (status, tx_hash, admin_note, wd_id),
    )


async def get_user_by_id(user_id: int) -> Optional[dict]:
    """Fetch a user by their internal DB id (not telegram_id)."""
    return await fetchone("SELECT * FROM users WHERE id = %s", (user_id,))


async def get_all_users(limit: int = 200) -> list[dict]:
    """Return all registered users ordered by newest first (for admin panel)."""
    return await fetchall(
        """
        SELECT id, telegram_id, username, first_name, last_name, created_at
        FROM users
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


async def log_admin_transfer(
    admin_tg_id: int,
    user_id: int,
    chain: str,
    from_address: str,
    to_address: str,
    amount: float,
    tx_hash: str = "",
    status: str = "success",
    note: str = "",
) -> dict:
    """
    Persist a permanent record of an admin-initiated transfer.
    status: 'success' | 'failed'
    """
    return await fetchone_returning(
        """
        INSERT INTO admin_transfers
            (admin_tg_id, user_id, chain, from_address, to_address, amount, tx_hash, status, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (admin_tg_id, user_id, chain, from_address, to_address, amount, tx_hash, status, note),
    )


# ── Stats helpers ─────────────────────────────────────────────────────────────

async def count_active_copy_trades(user_id: int) -> int:
    row = await fetchone(
        "SELECT COUNT(*) AS cnt FROM followed_wallets WHERE user_id = %s AND is_active = TRUE",
        (user_id,),
    )
    return row["cnt"] if row else 0


async def count_open_positions(user_id: int) -> int:
    row = await fetchone(
        "SELECT COUNT(*) AS cnt FROM portfolio_tokens WHERE user_id = %s AND balance > 0",
        (user_id,),
    )
    return row["cnt"] if row else 0

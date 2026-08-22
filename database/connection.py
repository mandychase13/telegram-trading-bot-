import asyncio
import psycopg2
from psycopg2 import pool as pg_pool
from contextlib import contextmanager
from typing import Any, Optional

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

_pool: Optional[pg_pool.ThreadedConnectionPool] = None


def init_pool() -> None:
    global _pool
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    _pool = pg_pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=settings.database_url,
    )
    logger.info("Database connection pool initialised")


@contextmanager
def get_conn():
    if _pool is None:
        raise RuntimeError("Database pool not initialised – call init_pool() first")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


# ── Sync helpers ──────────────────────────────────────────────────────────────

def _execute_sync(sql: str, params: tuple = ()) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def _fetchone_sync(sql: str, params: tuple = ()) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))


def _fetchall_sync(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in rows]


def _fetchone_returning_sync(sql: str, params: tuple = ()) -> Optional[dict]:
    """Execute a query with RETURNING and return the first row."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))


def _apply_balance_adjustment_sync(
    admin_telegram_id: int,
    user_id: int,
    asset: str,
    network: str,
    action_type: str,
    amount: str,
    reason: str,
    idempotency_key: str,
) -> dict:
    """Apply an accounting-only adjustment and its audit record atomically."""
    from decimal import Decimal
    from config import settings

    if admin_telegram_id != settings.admin_telegram_id:
        raise PermissionError("Only the configured administrator may adjust balances")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM balance_adjustment_audit
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            )
            existing = cur.fetchone()
            if existing is not None:
                cols = [desc[0] for desc in cur.description]
                return {"duplicate": True, "audit": dict(zip(cols, existing))}

            cur.execute(
                """
                INSERT INTO internal_balances (user_id, asset, network)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, asset, network) DO NOTHING
                """,
                (user_id, asset, network),
            )
            cur.execute(
                """
                SELECT balance
                FROM internal_balances
                WHERE user_id = %s AND asset = %s AND network = %s
                FOR UPDATE
                """,
                (user_id, asset, network),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("Internal balance record could not be created")
            cur.execute("SELECT telegram_id FROM users WHERE id = %s FOR SHARE", (user_id,))
            user_row = cur.fetchone()
            if user_row is None:
                raise ValueError("Target user was not found")

            previous = Decimal(str(row[0]))
            requested = Decimal(amount)
            if action_type == "add":
                new_balance = previous + requested
            elif action_type == "subtract":
                new_balance = previous - requested
            elif action_type == "set":
                new_balance = requested
            else:
                raise ValueError("Unsupported balance action")

            if new_balance < 0:
                raise ValueError("Adjustment would make the internal balance negative")

            cur.execute(
                """
                INSERT INTO balance_adjustment_audit
                    (admin_telegram_id, user_id, user_telegram_id, asset, network, previous_balance,
                     adjustment_amount, new_balance, action_type, reason, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING *
                """,
                (
                    admin_telegram_id, user_id, user_row[0], asset, network, previous,
                    requested if action_type != "set" else new_balance - previous,
                    new_balance, action_type, reason, idempotency_key,
                ),
            )
            audit = cur.fetchone()
            if audit is None:
                raise RuntimeError("Could not create balance adjustment audit record")
            audit_cols = [desc[0] for desc in cur.description]

            cur.execute(
                """
                UPDATE internal_balances
                SET balance = %s, display_mode = 'internal', updated_at = NOW()
                WHERE user_id = %s AND asset = %s AND network = %s
                """,
                (new_balance, user_id, asset, network),
            )
            return {"duplicate": False, "audit": dict(zip(audit_cols, audit))}


# ── Async wrappers ────────────────────────────────────────────────────────────

async def execute(sql: str, params: tuple = ()) -> None:
    await asyncio.to_thread(_execute_sync, sql, params)


async def fetchone(sql: str, params: tuple = ()) -> Optional[dict]:
    return await asyncio.to_thread(_fetchone_sync, sql, params)


async def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(_fetchall_sync, sql, params)


async def fetchone_returning(sql: str, params: tuple = ()) -> Optional[dict]:
    return await asyncio.to_thread(_fetchone_returning_sync, sql, params)


async def apply_balance_adjustment(
    admin_telegram_id: int,
    user_id: int,
    asset: str,
    network: str,
    action_type: str,
    amount: str,
    reason: str,
    idempotency_key: str,
) -> dict:
    return await asyncio.to_thread(
        _apply_balance_adjustment_sync,
        admin_telegram_id, user_id, asset, network, action_type,
        amount, reason, idempotency_key,
    )

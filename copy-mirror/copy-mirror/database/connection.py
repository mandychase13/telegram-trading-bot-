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


# ── Async wrappers ────────────────────────────────────────────────────────────

async def execute(sql: str, params: tuple = ()) -> None:
    await asyncio.to_thread(_execute_sync, sql, params)


async def fetchone(sql: str, params: tuple = ()) -> Optional[dict]:
    return await asyncio.to_thread(_fetchone_sync, sql, params)


async def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(_fetchall_sync, sql, params)


async def fetchone_returning(sql: str, params: tuple = ()) -> Optional[dict]:
    return await asyncio.to_thread(_fetchone_returning_sync, sql, params)

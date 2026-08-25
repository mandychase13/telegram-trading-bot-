from .connection import _execute_sync
from utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT  UNIQUE NOT NULL,
    username    VARCHAR(255),
    first_name  VARCHAR(255),
    last_name   VARCHAR(255),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wallets (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
    sol_address  VARCHAR(255),
    sol_pk_enc   TEXT,
    eth_address  VARCHAR(255),
    eth_pk_enc   TEXT,
    bnb_address  VARCHAR(255),
    bnb_pk_enc   TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- Idempotent column additions (safe to run on existing databases)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='wallets' AND column_name='bnb_pk_enc'
    ) THEN
        ALTER TABLE wallets ADD COLUMN bnb_pk_enc TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='wallets' AND column_name='sol_mnemonic_enc'
    ) THEN
        ALTER TABLE wallets ADD COLUMN sol_mnemonic_enc TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='wallets' AND column_name='eth_mnemonic_enc'
    ) THEN
        ALTER TABLE wallets ADD COLUMN eth_mnemonic_enc TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='wallets' AND column_name='bnb_mnemonic_enc'
    ) THEN
        ALTER TABLE wallets ADD COLUMN bnb_mnemonic_enc TEXT;
    END IF;
END $$;

-- Per-chain metadata (one row per user per chain)
CREATE TABLE IF NOT EXISTS wallet_metadata (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chain        VARCHAR(10)  NOT NULL,
    address      VARCHAR(255) NOT NULL,
    source       VARCHAR(30)  NOT NULL DEFAULT 'generated',
    -- source values: generated | imported_pk | imported_mnemonic
    has_mnemonic BOOLEAN      DEFAULT FALSE,
    label        VARCHAR(100) DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, chain)
);

-- Immutable audit trail for all wallet events (no DELETE allowed in application code)
CREATE TABLE IF NOT EXISTS wallet_audit_log (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
    action       VARCHAR(50)  NOT NULL,
    -- action values: WALLET_CREATED | WALLET_IMPORTED | MNEMONIC_STORED |
    --                ADDRESS_REPLACED | WITHDRAWAL_APPROVED | WITHDRAWAL_REJECTED |
    --                WITHDRAWAL_FAILED | ADMIN_TRANSFER
    chain        VARCHAR(10),
    address_hint VARCHAR(20),   -- first 10 chars of public address only; never a key
    details      TEXT,          -- human-readable context; must never contain secrets
    created_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_settings (
    id                      SERIAL PRIMARY KEY,
    user_id                 INTEGER REFERENCES users(id) ON DELETE CASCADE UNIQUE,
    slippage                FLOAT   DEFAULT 1.0,
    priority_fee            FLOAT   DEFAULT 0.001,
    notifications_enabled   BOOLEAN DEFAULT TRUE,
    default_buy_sol         FLOAT   DEFAULT 0.1,
    default_buy_eth         FLOAT   DEFAULT 0.01,
    default_buy_bnb         FLOAT   DEFAULT 0.05,
    language                VARCHAR(10) DEFAULT 'en',
    max_daily_trades        INTEGER DEFAULT 10,
    stop_loss_pct           FLOAT   DEFAULT 10.0,
    take_profit_pct         FLOAT   DEFAULT 50.0,
    updated_at              TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS followed_wallets (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chain           VARCHAR(10) NOT NULL,
    wallet_address  VARCHAR(255) NOT NULL,
    label           VARCHAR(100),
    is_active       BOOLEAN   DEFAULT TRUE,
    last_tx_sig     TEXT,
    last_checked_at TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, chain, wallet_address)
);

CREATE TABLE IF NOT EXISTS copy_settings (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
    followed_wallet_id  INTEGER REFERENCES followed_wallets(id) ON DELETE CASCADE,
    copy_percentage     FLOAT   DEFAULT 10.0,
    max_trade_amount    FLOAT   DEFAULT 1.0,
    min_trade_amount    FLOAT   DEFAULT 0.0,
    slippage            FLOAT   DEFAULT 1.0,
    priority_fee        FLOAT   DEFAULT 0.001,
    is_enabled          BOOLEAN DEFAULT TRUE,
    updated_at          TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, followed_wallet_id)
);

-- Idempotent: add min_trade_amount to existing copy_settings tables
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='copy_settings' AND column_name='min_trade_amount'
    ) THEN
        ALTER TABLE copy_settings ADD COLUMN min_trade_amount FLOAT DEFAULT 0.0;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS trades (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chain               VARCHAR(10),
    trade_type          VARCHAR(10),
    token_address       VARCHAR(255),
    token_symbol        VARCHAR(50),
    amount_in           FLOAT,
    amount_out          FLOAT,
    tx_hash             VARCHAR(255),
    is_copy_trade       BOOLEAN DEFAULT FALSE,
    followed_wallet_id  INTEGER,
    status              VARCHAR(20) DEFAULT 'pending',
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portfolio_tokens (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chain          VARCHAR(10),
    token_address  VARCHAR(255),
    token_symbol   VARCHAR(50),
    balance        FLOAT DEFAULT 0,
    avg_buy_price  FLOAT DEFAULT 0,
    updated_at     TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, chain, token_address)
);

CREATE TABLE IF NOT EXISTS autotrade_settings (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chain             VARCHAR(10),
    is_enabled        BOOLEAN DEFAULT FALSE,
    stop_loss_pct     FLOAT   DEFAULT 10.0,
    take_profit_pct   FLOAT   DEFAULT 50.0,
    max_daily_trades  INTEGER DEFAULT 5,
    max_trade_amount  FLOAT   DEFAULT 1.0,
    updated_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, chain)
);

CREATE TABLE IF NOT EXISTS withdrawal_requests (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
    chain        VARCHAR(10)   NOT NULL,
    to_address   VARCHAR(255)  NOT NULL,
    amount       FLOAT         NOT NULL,
    status       VARCHAR(20)   DEFAULT 'pending',
    admin_note   TEXT,
    tx_hash      VARCHAR(255)  DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

-- Admin-initiated transfers: full audit record of every fund movement by the admin
CREATE TABLE IF NOT EXISTS admin_transfers (
    id           SERIAL PRIMARY KEY,
    admin_tg_id  BIGINT        NOT NULL,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    chain        VARCHAR(10)   NOT NULL,
    from_address VARCHAR(255)  DEFAULT '',
    to_address   VARCHAR(255)  NOT NULL,
    amount       FLOAT         NOT NULL,
    tx_hash      VARCHAR(255)  DEFAULT '',
    status       VARCHAR(20)   DEFAULT 'pending',
    note         TEXT          DEFAULT '',
    created_at   TIMESTAMP DEFAULT NOW()
);

-- Accounting-only balance overlay. This never represents or moves blockchain funds.
CREATE TABLE IF NOT EXISTS internal_balances (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    asset        VARCHAR(20) NOT NULL,
    network      VARCHAR(20) NOT NULL,
    balance      NUMERIC(38, 18) NOT NULL DEFAULT 0,
    display_mode VARCHAR(20) NOT NULL DEFAULT 'on_chain',
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, asset, network),
    CHECK (balance >= 0),
    CHECK (display_mode IN ('on_chain', 'internal'))
);

-- Admin-added trading credit is an internal risk/allocation budget only.
-- It is deliberately separate from verified on-chain funds and is never
-- spendable by the withdrawal executor.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='internal_balances' AND column_name='admin_credit'
    ) THEN
        ALTER TABLE internal_balances
            ADD COLUMN admin_credit NUMERIC(38, 18) NOT NULL DEFAULT 0;
        UPDATE internal_balances
        SET admin_credit = balance
        WHERE display_mode = 'internal' AND balance <> 0;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS internal_balances_copy_lookup
    ON internal_balances (user_id, asset, network);

-- Append-only application audit trail for manual accounting adjustments.
CREATE TABLE IF NOT EXISTS balance_adjustment_audit (
    id                  BIGSERIAL PRIMARY KEY,
    admin_telegram_id   BIGINT NOT NULL,
    user_id             INTEGER REFERENCES users(id) ON DELETE RESTRICT NOT NULL,
    user_telegram_id    BIGINT NOT NULL,
    asset               VARCHAR(20) NOT NULL,
    network             VARCHAR(20) NOT NULL,
    previous_balance    NUMERIC(38, 18) NOT NULL,
    adjustment_amount   NUMERIC(38, 18) NOT NULL,
    new_balance         NUMERIC(38, 18) NOT NULL,
    action_type         VARCHAR(10) NOT NULL CHECK (action_type IN ('add', 'subtract', 'set')),
    reason              TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    idempotency_key     VARCHAR(120) UNIQUE NOT NULL,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- Ensure the audit records cannot be changed or removed through the application role.
REVOKE UPDATE, DELETE ON balance_adjustment_audit FROM PUBLIC;

-- The source transaction is the idempotency key for a copied trader event.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='trades' AND column_name='source_tx_hash'
    ) THEN
        ALTER TABLE trades ADD COLUMN source_tx_hash VARCHAR(255);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS trades_copy_source_event_unique
    ON trades (user_id, followed_wallet_id, source_tx_hash)
    WHERE is_copy_trade = TRUE AND source_tx_hash IS NOT NULL AND source_tx_hash <> '';
"""


def create_tables() -> None:
    """Create all tables synchronously – called on startup before the event loop."""
    import psycopg2
    from config import settings
    conn = psycopg2.connect(settings.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
        conn.commit()
        logger.info("Database schema ready")
    finally:
        conn.close()

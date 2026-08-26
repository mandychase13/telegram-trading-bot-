# Copy Vault 🤖

**Professional Telegram Copy Trading Bot** — mirror top-performing wallets on Solana, Ethereum, and BNB Chain in real time.

---

## Features

| Module | Description |
|---|---|
| 💼 Wallet System | Auto-generated SOL / ETH / BNB wallets per user, encrypted private keys |
| 📈 Copy Trading | Monitor any wallet and auto-copy its trades proportionally |
| 🤖 Auto Trading | Rule-based stop-loss / take-profit / daily trade limits |
| 📊 Portfolio | Live token balances, P&L, open positions, trade history |
| 🔄 Transfer | Send funds from your bot wallet to any external address |
| 💰 Admin Balance Management | Audited internal balance adjustments without blockchain movement |
| ⚙️ Settings | Per-user slippage, priority fees, notification preferences |
| 🔐 Security | Fernet-encrypted private keys, crash recovery, structured logging |

---

## Quick Start

### 1 – Clone and install

```bash
cd copy-mirror
pip install -r requirements.txt
```

### 2 – Configure secrets

**In Replit** add these as *Secrets* (not plain env vars):

| Key | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `SOLANA_RPC_API_KEY` or `SOLANA_RPC_URL` | Your Solana provider key or full RPC URL |
| `ETHEREUM_RPC_API_KEY` or `ETHEREUM_RPC_URL` | Your Ethereum provider key or full RPC URL |
| `BNB_RPC_URL` | Your BNB-compatible RPC endpoint URL |
| `PRICE_API_KEY` | CoinGecko API key |
| `JUPITER_API_KEY` | Jupiter swap API key |
| `ENCRYPTION_KEY` | Random 32+ char string (stable across restarts!) |

`DATABASE_URL` is provided automatically by Replit's built-in PostgreSQL.

The bot is branded **Copy Vault**. Keep provider credentials in Replit Secrets; never place them in this repository or send them in chat.

### 3 – Run

```bash
python main.py
```

---

## Project Structure

```
copy-mirror/
├── main.py                  # Entry point
├── requirements.txt
├── config/
│   └── settings.py          # Reads all env vars
├── database/
│   ├── connection.py        # psycopg2 pool + async helpers
│   ├── schema.py            # CREATE TABLE statements
│   └── operations.py        # All CRUD operations
├── blockchain/
│   ├── wallet_generator.py  # Solana / ETH / BNB keypair generation
│   ├── solana_client.py     # Solana JSON-RPC
│   ├── eth_client.py        # Ethereum JSON-RPC
│   └── bnb_client.py        # BNB Chain JSON-RPC
├── services/
│   ├── wallet_service.py    # Creates and stores wallets
│   ├── price_service.py     # CoinGecko live prices
│   └── copy_engine.py       # Wallet monitoring + copy trade execution
├── handlers/
│   ├── start.py             # /start onboarding
│   ├── dashboard.py         # Main dashboard
│   ├── wallet_handler.py    # Wallet view / deposit / history
│   ├── trade_handler.py     # Buy / Sell / Transfer conversations
│   ├── portfolio_handler.py # Portfolio overview
│   ├── copytrade_handler.py # Add / manage / enable copy trades
│   ├── autotrade_handler.py # Auto trading configuration
│   ├── wallets_manager.py   # Manage tracked wallets
│   └── settings_handler.py  # User settings
└── utils/
    ├── logger.py            # Rotating file + console logger
    ├── encryption.py        # Fernet encrypt/decrypt
    ├── keyboards.py         # All InlineKeyboard definitions
    └── helpers.py           # Formatting helpers
```

---

## Database Schema

| Table | Purpose |
|---|---|
| `users` | Telegram ID, username, registration date |
| `wallets` | SOL / ETH / BNB addresses and encrypted private keys |
| `user_settings` | Slippage, fees, trade defaults, notifications |
| `followed_wallets` | Wallets being monitored for copy trading |
| `copy_settings` | Per-wallet copy %, max amount, slippage |
| `trades` | Full buy/sell/transfer history |
| `portfolio_tokens` | Current token holdings and avg buy price |
| `autotrade_settings` | Per-chain stop-loss, take-profit, daily limits |

---

## Security Notes

- Private keys are **Fernet-encrypted** before storage using `ENCRYPTION_KEY`.
- **Never change `ENCRYPTION_KEY`** after wallets have been created — existing keys will become unreadable.
- Never expose `ENCRYPTION_KEY` in logs, error messages, or git history.

## Copy-trading accounting

- Verified on-chain funds and admin-added trading credit are kept separate.
- Admin credit increases copy-trading allocation calculations but is not treated
  as a blockchain deposit and cannot be used by the withdrawal executor.
- Copy-trade source transaction hashes are recorded and deduplicated at the
  database level, so overlapping polling cycles cannot execute one trader event
  twice.
- RPC and swap failures log the service host and method without logging API
  query secrets, and temporary Solana connection failures are retried.
- The bot never logs or displays raw private keys.

## Internal balance adjustments

The admin panel's **Balance Management** feature is an accounting-only overlay.
It does not move Circle/on-chain funds, and never creates a transaction hash.
After the first adjustment for an asset/network, users see the internal available
balance while authorized admins continue to see both the verified on-chain and
internal balances.

Every add, subtract, or set operation requires a reason and explicit
confirmation. The database applies the balance and audit record in one
transaction, uses exact `NUMERIC` values, rejects negative results, and protects
against repeated requests with an idempotency key. Audit rows are not editable
or deletable through the application.

---

## 24/7 Hosting on Replit

1. Use Replit's **Always-On** feature (paid plan) or configure a UptimeRobot ping.
2. Set all secrets in the Replit Secrets panel.
3. The bot auto-reconnects on network drops (Telegram's polling handles retries).
4. Logs rotate at 5 MB and keep 5 backups in the `logs/` directory.

---

## Extending the Bot

- **Solana trade execution**: `blockchain/solana_executor.py` uses Jupiter Swap API v1 (`api.jup.ag/swap/v1`) with a network fallback and `solders` transaction signing.
- **ETH/BNB execution**: use `web3.py` with the Alchemy / GetBlock RPC to sign and broadcast EVM transactions.
- **More copy-trade analytics**: extend `copy_engine.py` with Alchemy's `alchemy_getAssetTransfers` to classify ERC-20 swaps.

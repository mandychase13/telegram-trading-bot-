"""
Copy Trading Engine — LIVE TRADING MODE
-----------------------------------------
Runs as a periodic JobQueue job (every 60 s).
For each active followed wallet it:
  1. Fetches new transactions since last_tx_sig.
  2. Classifies each transaction as BUY or SELL and extracts token address.
  3. Executes a matching trade with the user's real wallet using the copy %.
  4. Records the trade with the real tx_hash and notifies the user.
"""
import asyncio
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from database.operations import (
    get_active_followed_wallets_all,
    update_wallet_last_checked,
    get_copy_settings,
    save_copy_trade_once,
    update_trade_status,
    apply_copy_position_update,
    get_wallet,
    get_user,
)
from blockchain.solana_client import monitor_wallet_for_new_txs
from blockchain.solana_client import get_sol_balance
from blockchain.solana_executor import execute_sol_buy, execute_sol_sell
from blockchain.evm_executor import execute_evm_buy, execute_evm_sell
from utils.encryption import decrypt
from utils.logger import get_logger
from utils.card_generator import TradeCardData, generate_trade_card, CardGenerationError
from config import settings
from services.balance_service import resolve_available_balance

logger = get_logger(__name__)

# Solana native mint address
SOL_MINT = "So11111111111111111111111111111111111111112"


# ── transaction classification ─────────────────────────────────────────────────

async def _classify_sol_tx(tx: dict, watched_address: str) -> Optional[dict]:
    """
    Parse a Solana transaction and return trade info dict or None.
    Detects:
      - trade_type: "buy" (SOL out) or "sell" (SOL in)
      - token_address: SPL mint of the token swapped (from token balance diffs)
      - amount_in: SOL amount involved
    """
    meta = tx.get("meta", {})
    if not meta or meta.get("err") is not None:
        return None

    pre_bal  = meta.get("preBalances", [])
    post_bal = meta.get("postBalances", [])
    accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

    if not pre_bal or not post_bal or not accounts:
        return None

    # Find signer index
    signer_idx = None
    for i, acc in enumerate(accounts):
        key = acc if isinstance(acc, str) else acc.get("pubkey", "")
        if key == watched_address:
            signer_idx = i
            break

    if signer_idx is None or signer_idx >= len(pre_bal):
        return None

    sol_delta = (post_bal[signer_idx] - pre_bal[signer_idx]) / 1e9
    if abs(sol_delta) < 0.000_001:
        return None  # ignore dust / fee-only txs

    trade_type = "buy" if sol_delta < 0 else "sell"

    # Extract token address from token balance changes
    pre_tb  = {tb["accountIndex"]: tb for tb in meta.get("preTokenBalances",  [])}
    post_tb = {tb["accountIndex"]: tb for tb in meta.get("postTokenBalances", [])}
    all_idx = set(pre_tb.keys()) | set(post_tb.keys())

    token_address = ""
    token_amount = 0.0
    for idx in all_idx:
        pre_ui  = float((pre_tb.get(idx,  {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
        post_ui = float((post_tb.get(idx, {}).get("uiTokenAmount") or {}).get("uiAmount") or 0)
        delta   = post_ui - pre_ui
        owner   = (
            post_tb.get(idx, {}).get("owner")
            or pre_tb.get(idx, {}).get("owner", "")
        )
        mint = (
            post_tb.get(idx, {}).get("mint")
            or pre_tb.get(idx, {}).get("mint", "")
        )
        if owner == watched_address and mint and mint != SOL_MINT:
            # buy → token balance increased; sell → token balance decreased
            if trade_type == "buy" and delta > 0:
                token_address = mint
                token_amount = abs(delta)
                break
            elif trade_type == "sell" and delta < 0:
                token_address = mint
                token_amount = abs(delta)
                break

    tx_hash = tx.get("transaction", {}).get("signatures", [""])[0]

    return {
        "trade_type":    trade_type,
        "token_address": token_address,
        "token_symbol":  token_address[:6].upper() if token_address else "SOL",
        "amount_in":     abs(sol_delta),
        "token_amount":  token_amount,
        "tx_hash":       tx_hash,
    }


# ── EVM classification (Alchemy asset transfers) ───────────────────────────────

def _classify_evm_transfer(tx: dict, watched_address: str, chain: str) -> Optional[dict]:
    """
    Classify an Alchemy assetTransfer record.
    Outgoing ERC-20 transfer → sell; incoming → buy.
    """
    from_addr = (tx.get("from") or "").lower()
    to_addr   = (tx.get("to")   or "").lower()
    asset     = tx.get("asset", "")
    value     = float(tx.get("value") or 0)
    raw_contract = tx.get("rawContract", {})
    token_address = raw_contract.get("address") or ""
    tx_hash       = tx.get("hash", "")

    if not value or not token_address:
        return None
    if asset.upper() in ("ETH", "BNB", "MATIC"):
        return None  # skip native transfers

    watched = watched_address.lower()
    if from_addr == watched:
        trade_type = "sell"
    elif to_addr == watched:
        trade_type = "buy"
    else:
        return None

    return {
        "trade_type":    trade_type,
        "token_address": token_address,
        "token_symbol":  asset[:6].upper() if asset else token_address[:6].upper(),
        "amount_in":     value,
        "token_amount":  value,
        "tx_hash":       tx_hash,
    }


# ── main job ───────────────────────────────────────────────────────────────────

async def check_all_followed_wallets(context) -> None:
    """JobQueue callback: iterate every active followed wallet and execute copy trades."""
    try:
        followed = await get_active_followed_wallets_all()
        if not followed:
            return
        for fw in followed:
            try:
                await _process_followed_wallet(context, fw)
            except Exception as exc:
                logger.error("Error processing followed wallet id=%s: %s", fw["id"], exc)
    except Exception as exc:
        logger.error("Copy engine job error: %s", exc)


async def _process_followed_wallet(context, fw: dict) -> None:
    chain            = fw.get("chain", "").upper()
    wallet_address   = fw.get("wallet_address", "")
    user_id          = fw["user_id"]
    followed_id      = fw["id"]
    last_sig         = fw.get("last_tx_sig")
    telegram_id      = fw.get("telegram_id")

    if chain == "SOL":
        new_txs, latest_sig = await monitor_wallet_for_new_txs(wallet_address, last_sig)

        for tx in new_txs:
            trade_info = await _classify_sol_tx(tx, wallet_address)
            if not trade_info or not trade_info["token_address"]:
                continue
            await _execute_copy_trade(context, user_id, followed_id, fw, chain, trade_info, telegram_id)
        # Advance only after all discovered events have been attempted. Source
        # event deduplication makes successful events safe if a later one fails.
        await update_wallet_last_checked(followed_id, latest_sig)

    elif chain in ("ETH", "BNB"):
        from blockchain.eth_client import get_eth_transactions
        from blockchain.bnb_client import get_bnb_transactions

        if chain == "ETH":
            transfers = await get_eth_transactions(wallet_address, limit=5)
        else:
            transfers = await get_bnb_transactions(wallet_address, limit=5)

        # Simple last-seen deduplication using stored last_tx_sig as a tx hash
        new_transfers = []
        for t in transfers:
            h = t.get("hash", "") or t.get("uniqueId", "")
            if h == last_sig:
                break
            new_transfers.append(t)

        if new_transfers:
            latest_hash = new_transfers[0].get("hash") or new_transfers[0].get("uniqueId") or last_sig

        for transfer in new_transfers:
            trade_info = _classify_evm_transfer(transfer, wallet_address, chain)
            if not trade_info:
                continue
            await _execute_copy_trade(context, user_id, followed_id, fw, chain, trade_info, telegram_id)
        if new_transfers:
            await update_wallet_last_checked(followed_id, latest_hash)


async def _execute_copy_trade(
    context,
    user_id: int,
    followed_id: int,
    fw: dict,
    chain: str,
    trade_info: dict,
    telegram_id: Optional[int],
) -> None:
    """
    Apply copy settings and execute the matching trade on the user's wallet.
    """
    cs = await get_copy_settings(user_id, followed_id)
    if cs and not cs.get("is_enabled", True):
        return

    copy_pct      = Decimal(str(cs.get("copy_percentage", 10.0) if cs else 10.0)) / Decimal("100")
    max_amount    = Decimal(str(cs.get("max_trade_amount", 1.0) if cs else 1.0))
    min_amount    = Decimal(str(cs.get("min_trade_amount", 0.0) if cs else 0.0))
    slippage_bps  = int((cs.get("slippage",    1.0)     if cs else 1.0) * 100)
    trade_type    = trade_info["trade_type"]

    # The allocation includes verified on-chain funds plus admin trading
    # credit. Credit affects sizing, but never pretends to fund a blockchain
    # transaction; the executor still requires real native funds.
    onchain_balance = await _get_native_balance(chain, user_id)
    requested_amount = Decimal(str(trade_info["amount_in"])) * copy_pct
    if trade_type == "buy":
        available_allocation = Decimal(str(await resolve_available_balance(
            user_id, chain, chain, onchain_balance
        )))
        copy_amount = min(requested_amount, max_amount, available_allocation)
    else:
        # A sell mirrors the tracked token position; native balance/credit
        # should not be used as a substitute for token holdings.
        copy_amount = min(requested_amount, max_amount)
    copy_amount = copy_amount.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    if copy_amount <= 0 or copy_amount < min_amount:
        return
    if trade_type == "buy":
        if Decimal(str(onchain_balance)) < copy_amount:
            logger.warning(
                "Copy allocation includes admin credit but real funds are short "
                "user_id=%s chain=%s real_balance=%s requested=%s; executor will "
                "reject rather than fabricate a transaction",
                user_id, chain, onchain_balance, copy_amount,
            )

    # Get user's wallet + private key
    wallet = await get_wallet(user_id)
    if not wallet:
        logger.warning("No wallet for user_id=%s, skipping copy trade", user_id)
        return

    # Decrypt private key
    try:
        if chain == "SOL":
            pk = decrypt(wallet["sol_pk_enc"], settings.encryption_key)
        elif chain == "BNB":
            enc = wallet.get("bnb_pk_enc") or wallet.get("eth_pk_enc", "")
            pk = decrypt(enc, settings.encryption_key)
        else:  # ETH
            pk = decrypt(wallet["eth_pk_enc"], settings.encryption_key)
    except Exception as exc:
        logger.error("Could not decrypt private key for user_id=%s: %s", user_id, exc)
        return

    token_address = trade_info["token_address"]
    token_symbol  = trade_info["token_symbol"]
    # Save as pending
    source_tx_hash = trade_info.get("tx_hash", "")
    trade = await save_copy_trade_once(
        user_id=user_id,
        chain=chain,
        trade_type=trade_type,
        token_address=token_address,
        token_symbol=token_symbol,
        followed_wallet_id=followed_id,
        status="pending",
        amount_in=copy_amount,
        source_tx_hash=source_tx_hash,
    )
    if trade is None:
        logger.info(
            "Skipping duplicate copy event user_id=%s followed_wallet_id=%s source_tx=%s",
            user_id, followed_id, source_tx_hash[:20],
        )
        return
    trade_id = trade["id"]

    # Execute on-chain
    if chain == "SOL":
        if trade_type == "buy":
            result = await execute_sol_buy(pk, token_address, copy_amount, slippage_bps=slippage_bps)
        else:
            result = await execute_sol_sell(pk, token_address, copy_amount, slippage_bps=slippage_bps)
    else:
        slip = slippage_bps / 10_000
        if trade_type == "buy":
            result = await execute_evm_buy(pk, token_address, copy_amount, slippage=slip, chain=chain)
        else:
            result = await execute_evm_sell(pk, token_address, copy_amount, chain=chain, slippage=slip)

    if result["ok"]:
        tx_hash = result["tx_hash"]
        await update_trade_status(trade_id, "confirmed", tx_hash)
        await apply_copy_position_update(
            user_id=user_id,
            chain=chain,
            token_address=token_address,
            token_symbol=token_symbol,
            trade_type=trade_type,
            amount=float(trade_info.get("token_amount") or copy_amount * copy_pct),
        )
        status_icon = "✅"
        status_line = f"Tx: `{tx_hash[:24]}…`"
        explorer_url = _explorer_url(chain, tx_hash)
    else:
        await update_trade_status(trade_id, "failed")
        status_icon = "❌"
        status_line = f"Error: {result['error'][:80]}"
        explorer_url = None

    # ── Generate and send trade summary card for completed sell copy trades ──
    if result["ok"] and trade_type == "sell" and telegram_id and context:
        try:
            from datetime import datetime, timezone as _tz
            _now = datetime.now(_tz.utc)
            _card_data = TradeCardData(
                token_name      = token_symbol,
                token_symbol    = token_symbol,
                token_pair      = f"{token_symbol}/{chain}",
                network         = chain,
                amount_invested = copy_amount,
                trade_duration  = "—",
                date            = _now.strftime("%b %-d, %Y"),
                time_str        = _now.strftime("%H:%M UTC"),
                chain_currency  = chain,
                is_demo         = False,
            )
            _png = generate_trade_card(_card_data)
            await context.bot.send_photo(
                chat_id=telegram_id,
                photo=_png,
                caption="📊 *Copy Trade Summary*",
                parse_mode="Markdown",
            )
        except CardGenerationError:
            logger.error("Failed to generate trade summary card for copy sell")
        except Exception as _card_err:
            logger.error("Unexpected error generating copy sell card: %s", _card_err)

    # Notify user
    if telegram_id and context:
        label = fw.get("label") or (fw["wallet_address"][:8] + "…")
        msg = (
            f"🔂 *Copy Trade {status_icon}*\n\n"
            f"Wallet: `{label}`\n"
            f"Action: *{trade_type.upper()}*\n"
            f"Token:  `{token_address[:20]}…`\n"
            f"Amount: {float(copy_amount):.6f} {chain}\n"
            f"{status_line}"
        )
        if explorer_url:
            msg += f"\n🔗 [Explorer]({explorer_url})"
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Could not notify user %s: %s", telegram_id, exc)


def _explorer_url(chain: str, tx_hash: str) -> str:
    if chain == "SOL":
        return f"https://solscan.io/tx/{tx_hash}"
    elif chain == "ETH":
        return f"https://etherscan.io/tx/{tx_hash}"
    elif chain == "BNB":
        return f"https://bscscan.com/tx/{tx_hash}"
    return ""


async def _get_native_balance(chain: str, user_id: int) -> float:
    """Read the user's real native balance for allocation and execution logs."""
    wallet = await get_wallet(user_id)
    if not wallet:
        return 0.0
    try:
        if chain == "SOL":
            return await get_sol_balance(wallet.get("sol_address") or "")
        if chain == "ETH":
            from blockchain.eth_client import get_eth_balance
            return await get_eth_balance(wallet.get("eth_address") or "")
        if chain == "BNB":
            from blockchain.bnb_client import get_bnb_balance
            return await get_bnb_balance(wallet.get("bnb_address") or "")
    except Exception as exc:
        logger.warning("Native balance lookup failed for copy allocation user_id=%s chain=%s: %s",
                       user_id, chain, exc)
    return 0.0

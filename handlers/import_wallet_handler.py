"""
Wallet Import — lets users import an existing wallet via:
  • Private Key  (SOL hex / 0x-hex / base58 / JSON-array, ETH/BNB hex)
  • Recovery Phrase  (12/24-word BIP-39 mnemonic, any chain)

The private key / mnemonic is NEVER logged.  It is encrypted with
Fernet before being written to the wallets table.

Conversation-state hygiene
──────────────────────────
Every code path — success, validation error, unexpected exception, /start
command, timeout — ends with ConversationHandler.END and a context.user_data
cleanup.  The user can never get permanently stuck in the import flow.
"""
import re
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from database.operations import (
    get_user, get_wallet, update_wallet_chain, log_wallet_audit,
)
from utils.keyboards import Keyboards
from utils.encryption import encrypt
from utils.admin_notify import notify_wallet_import
from config import settings
from utils.logger import get_logger
from utils.address_validation import is_valid_address

logger = get_logger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
IMP_METHOD, IMP_CHAIN, IMP_INPUT, IMP_CONFIRM = range(4)

# Callback data
CB_IMP_PK       = "import:pk"
CB_IMP_MNEMONIC = "import:mnemonic"
CB_IMP_YES      = "import:confirm:yes"
CB_IMP_NO       = "import:confirm:no"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Always wipe import state from memory."""
    context.user_data.pop("import", None)


async def _send(update: Update, context: ContextTypes.DEFAULT_TYPE,
                text: str, **kwargs) -> None:
    """Send a message safely whether or not the triggering message was deleted."""
    chat_id = update.effective_chat.id
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as exc:
        logger.warning("Could not send import message: %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

def _import_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Private Key",      callback_data=CB_IMP_PK),
            InlineKeyboardButton("📝 Recovery Phrase",  callback_data=CB_IMP_MNEMONIC),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=Keyboards.CB_CANCEL)],
    ])


async def import_wallet_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via inline button callback."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await context.bot.send_message(update.effective_chat.id, "❌ Account not found. Type /start.")
        return ConversationHandler.END

    _cleanup(context)
    context.user_data["import"] = {}
    logger.info("User %s started wallet import via callback", tg_user.id)

    await context.bot.send_message(update.effective_chat.id,
        "📥 *Import Wallet*\n\nHow would you like to import your wallet?",
        parse_mode="Markdown",
        reply_markup=_import_start_keyboard(),
    )
    return IMP_METHOD


async def import_wallet_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/import and /importwallet command entry — sends a new message."""
    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        await update.message.reply_text("❌ Account not found. Type /start.")
        return ConversationHandler.END

    _cleanup(context)
    context.user_data["import"] = {}
    logger.info("User %s started wallet import via command", tg_user.id)

    await update.message.reply_text(
        "📥 *Import Wallet*\n\nHow would you like to import your wallet?",
        parse_mode="Markdown",
        reply_markup=_import_start_keyboard(),
    )
    return IMP_METHOD


# ── Method selected ───────────────────────────────────────────────────────────

async def import_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    method = "pk" if query.data == CB_IMP_PK else "mnemonic"
    context.user_data["import"]["method"] = method

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("◎ Solana",    callback_data="import:chain:SOL"),
            InlineKeyboardButton("Ξ Ethereum",  callback_data="import:chain:ETH"),
            InlineKeyboardButton("🟡 BNB Chain", callback_data="import:chain:BNB"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=Keyboards.CB_CANCEL)],
    ])
    label = "private key" if method == "pk" else "recovery phrase"
    await context.bot.send_message(update.effective_chat.id,
        f"📥 *Import via {label}*\n\nSelect the chain for this wallet:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return IMP_CHAIN


# ── Chain selected ────────────────────────────────────────────────────────────

async def import_chain_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chain = query.data.split(":")[-1]   # SOL / ETH / BNB
    context.user_data["import"]["chain"] = chain
    method = context.user_data["import"]["method"]

    if method == "pk":
        if chain == "SOL":
            hint = (
                "Enter your Solana private key in any supported format:\n"
                "• 64-char hex seed  (e.g. `a1b2c3…`)\n"
                "• `0x`-prefixed hex  (e.g. `0xa1b2c3…`)\n"
                "• Base58 keypair  (87–88 chars from Phantom / Solflare)\n"
                "• JSON byte-array  (e.g. `[1,2,3,…,64]` from CLI wallets)"
            )
        else:
            hint = f"Enter your {chain} private key (hex, with or without `0x` prefix):"
    else:
        hint = (
            f"Enter your {chain} recovery phrase\n"
            "_(12 or 24 words separated by spaces)_:"
        )

    await context.bot.send_message(update.effective_chat.id,
        f"📥 *Import {chain} Wallet*\n\n"
        f"⚠️ _Send your secret in this chat only. The message will be deleted immediately._\n\n"
        + hint,
        parse_mode="Markdown",
        reply_markup=Keyboards.cancel_only(),
    )
    return IMP_INPUT


# ── Secret entered ────────────────────────────────────────────────────────────

async def import_input_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()

    # Delete the user's message immediately to protect the secret.
    # After this point we MUST use context.bot.send_message (not update.message.reply_text)
    # because the original message no longer exists.
    try:
        await update.message.delete()
    except Exception:
        pass

    chat_id = update.effective_chat.id

    imp = context.user_data.get("import", {})
    method = imp.get("method")
    chain  = imp.get("chain")

    if not method or not chain:
        # State was lost — end cleanly
        _cleanup(context)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Session data lost. Please tap *Import Wallet* again.",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
        )
        return ConversationHandler.END

    # ── Validate & derive address ──────────────────────────────────────────
    try:
        if method == "pk":
            address, pk_hex = _derive_from_private_key(chain, raw)
            mnemonic_to_store = None
        else:
            address, pk_hex = _derive_from_mnemonic(chain, raw)
            mnemonic_to_store = " ".join(raw.lower().split())
        if not is_valid_address(address, chain):
            raise ValueError("Derived wallet address is invalid.")

    except ValueError as exc:
        # Specific validation error — show it to the user and let them retry
        logger.info("Import validation error (user %s, %s %s): %s",
                    update.effective_user.id, method, chain, exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ *Invalid input*\n\n{exc}\n\nPlease try again or tap Cancel:",
            parse_mode="Markdown",
            reply_markup=Keyboards.cancel_only(),
        )
        return IMP_INPUT   # stay in state — user can retry or cancel

    except Exception as exc:
        # Unexpected error — clean up and return user to dashboard
        logger.error("Unexpected error during %s import for user %s: %s",
                     chain, update.effective_user.id, exc, exc_info=True)
        _cleanup(context)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ *An unexpected error occurred.*\n\nPlease try again from the dashboard.",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
        )
        return ConversationHandler.END

    context.user_data["import"]["address"]  = address
    context.user_data["import"]["pk_hex"]   = pk_hex
    context.user_data["import"]["mnemonic"] = mnemonic_to_store

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=CB_IMP_YES),
            InlineKeyboardButton("❌ Cancel",  callback_data=CB_IMP_NO),
        ]
    ])
    chain_icon = {"SOL": "◎", "ETH": "Ξ", "BNB": "🟡"}.get(chain, "🔗")
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📥 *Confirm Wallet Import*\n\n"
            f"{chain_icon} *Chain:*   {chain}\n"
            f"📬 *Address:* `{address}`\n\n"
            "This will *replace* your current wallet for this chain.\n"
            "Proceed?"
        ),
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return IMP_CONFIRM


# ── Confirmed ─────────────────────────────────────────────────────────────────

async def import_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db_user = await get_user(tg_user.id)
    if not db_user:
        _cleanup(context)
        await context.bot.send_message(update.effective_chat.id, "❌ Account not found. Type /start.")
        return ConversationHandler.END

    wallet = await get_wallet(db_user["id"])
    if not wallet:
        _cleanup(context)
        await context.bot.send_message(update.effective_chat.id, "❌ No wallet record. Type /start first.")
        return ConversationHandler.END

    imp      = context.user_data.get("import", {})
    chain    = imp.get("chain")
    address  = imp.get("address")
    pk_hex   = imp.get("pk_hex")
    mnemonic = imp.get("mnemonic")   # str or None

    if not (chain and address and pk_hex):
        _cleanup(context)
        await context.bot.send_message(update.effective_chat.id, "❌ Session data lost. Please start over.")
        return ConversationHandler.END

    method = imp.get("method", "pk")

    # Encrypt sensitive material, then wipe from context immediately
    pk_enc       = encrypt(pk_hex, settings.encryption_key)
    mnemonic_enc = encrypt(mnemonic, settings.encryption_key) if mnemonic else None
    _cleanup(context)

    source = "imported_mnemonic" if mnemonic_enc else "imported_pk"

    # Persist to database
    try:
        await update_wallet_chain(
            db_user["id"],
            chain,
            address,
            pk_enc,
            mnemonic_enc=mnemonic_enc,
            source=source,
        )
    except Exception as exc:
        logger.error("DB error saving imported %s wallet for user %s: %s",
                     chain, tg_user.id, exc)
        await context.bot.send_message(update.effective_chat.id,
            "❌ *Database error* — wallet could not be saved. Please try again.",
            parse_mode="Markdown",
            reply_markup=Keyboards.back_to_dashboard(),
        )
        return ConversationHandler.END

    # Audit log (no secrets — address hint only)
    if mnemonic_enc:
        await log_wallet_audit(
            user_id=db_user["id"],
            action="MNEMONIC_STORED",
            chain=chain,
            address=address,
            details=f"Encrypted mnemonic stored for {chain} import",
        )
    await log_wallet_audit(
        user_id=db_user["id"],
        action="WALLET_IMPORTED",
        chain=chain,
        address=address,
        details=f"method={method}, source={source}" + (", mnemonic_stored=yes" if mnemonic_enc else ""),
    )

    logger.info("User %s imported %s wallet  address=%s  source=%s  mnemonic=%s",
                tg_user.id, chain, address[:10] + "…", source, bool(mnemonic_enc))

    # Admin notification (fire-and-forget)
    try:
        await notify_wallet_import(query.get_bot(), tg_user, chain, address)
    except Exception:
        pass

    withdrawal_updated = await _finish_pending_withdrawal_import(
        query.get_bot(), context, tg_user.id, query.message.chat_id
    )
    if withdrawal_updated:
        try:
            await query.message.delete()
        except Exception as exc:
            logger.warning("Could not remove wallet confirmation for user %s: %s",
                           tg_user.id, exc)
        return ConversationHandler.END

    chain_icon = {"SOL": "◎", "ETH": "Ξ", "BNB": "🟡"}.get(chain, "🔗")
    mnemonic_note = "\n🔒 _Recovery phrase also securely stored._" if mnemonic_enc else ""
    await context.bot.send_message(update.effective_chat.id,
        f"✅ *Wallet Imported Successfully!*\n\n"
        f"{chain_icon} *Chain:*   {chain}\n"
        f"📬 *Address:* `{address}`\n"
        f"📂 *Source:*  {source.replace('_', ' ').title()}"
        f"{mnemonic_note}\n\n"
        "Your wallet is ready to use.",
        parse_mode="Markdown",
        reply_markup=Keyboards.back_to_dashboard(),
    )
    return ConversationHandler.END


async def _finish_pending_withdrawal_import(
    bot,
    context: ContextTypes.DEFAULT_TYPE,
    telegram_user_id: int,
    chat_id: int,
 ) -> bool:
    """Replace the user's withdrawal prompt after a successful wallet import."""
    pending = context.user_data.pop("withdrawal_pending_import", None)
    processing_text = "Your withdrawal is being processed."

    if pending:
        try:
            await bot.send_message(
                chat_id=pending["chat_id"],
                text=processing_text,
            )
            return True
        except Exception as exc:
            logger.warning("Could not update withdrawal prompt for user %s: %s",
                           telegram_user_id, exc)

    # If the process restarted between the withdrawal and import, the original
    # message ID is unavailable. Keep the user-facing state private and show
    # only the same processing message rather than exposing request details.
    try:
        from database.connection import fetchall
        from database.operations import get_user

        db_user = await get_user(telegram_user_id)
        if db_user:
            rows = await fetchall(
                """
                SELECT id FROM withdrawal_requests
                WHERE user_id = %s AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (db_user["id"],),
            )
            if rows:
                await bot.send_message(chat_id=chat_id, text=processing_text)
                return True
    except Exception as exc:
        logger.warning("Could not recover withdrawal processing state for user %s: %s",
                       telegram_user_id, exc)
    return False


# ── Cancelled (by button or /start fallback) ──────────────────────────────────

async def import_cancelled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _cleanup(context)
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(update.effective_chat.id,
            "❌ Import cancelled.", reply_markup=Keyboards.back_to_dashboard()
        )
    elif update.message:
        # /start was typed mid-conversation — hand off to the real start handler
        from handlers.start import start_handler
        await start_handler(update, context)
    return ConversationHandler.END


# ── Crypto helpers ────────────────────────────────────────────────────────────

def _derive_from_private_key(chain: str, raw: str) -> tuple[str, str]:
    """Validate and derive address from a raw private key string.
    Returns (address, pk_hex) — never logs the key.
    """
    raw = raw.strip()
    if chain == "SOL":
        return _sol_from_pk(raw)
    else:  # ETH or BNB
        return _evm_from_pk(raw)


def _sol_from_pk(raw: str) -> tuple[str, str]:
    """Accept Solana private keys in all formats produced by major wallets:

    1. 64-char lowercase/uppercase hex seed  (32 bytes)
    2. 0x-prefixed hex  (some tools add this prefix)
    3. Base58-encoded 64-byte keypair  (Phantom / Solflare export, ~88 chars)
    4. Base58-encoded 32-byte seed  (~44 chars)
    5. JSON byte-array  ([n, n, n, …] — Solana CLI / keygen export)
    """
    from solders.keypair import Keypair
    import base58 as _base58

    try:
        # ── Format 5: JSON array [0..255, ...] ────────────────────────────
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                arr = json.loads(stripped)
                if not (isinstance(arr, list) and all(isinstance(x, int) for x in arr)):
                    raise ValueError("Not a valid byte array.")
                raw_bytes = bytes(arr)
                if len(raw_bytes) == 64:
                    kp = Keypair.from_bytes(raw_bytes)
                elif len(raw_bytes) == 32:
                    kp = Keypair.from_seed(raw_bytes)
                else:
                    raise ValueError(
                        f"JSON array must contain 32 or 64 bytes (got {len(raw_bytes)})."
                    )
                return str(kp.pubkey()), kp.secret().hex()
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid JSON byte-array: {exc}") from exc

        # ── Formats 1 & 2: hex (with or without 0x prefix) ───────────────
        hex_candidate = stripped.removeprefix("0x").removeprefix("0X")
        if re.fullmatch(r"[0-9a-fA-F]{64}", hex_candidate):
            seed = bytes.fromhex(hex_candidate)
            kp   = Keypair.from_seed(seed)
            return str(kp.pubkey()), kp.secret().hex()

        # ── Formats 3 & 4: Base58 ─────────────────────────────────────────
        try:
            decoded = _base58.b58decode(stripped)
        except Exception as exc:
            raise ValueError(
                f"Could not decode as Base58 — check for invalid characters "
                f"(Base58 does not include 0, O, I, l): {exc}"
            ) from exc

        if len(decoded) == 64:
            kp = Keypair.from_bytes(decoded)
        elif len(decoded) == 32:
            kp = Keypair.from_seed(decoded)
        else:
            raise ValueError(
                f"Decoded key is {len(decoded)} bytes; expected 32 (seed) or 64 (full keypair).\n"
                "Make sure you are copying the *private key*, not the wallet address."
            )
        return str(kp.pubkey()), kp.secret().hex()

    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not parse Solana private key: {exc}") from exc


def _evm_from_pk(raw: str) -> tuple[str, str]:
    from eth_account import Account

    raw = raw.removeprefix("0x").removeprefix("0X").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        raise ValueError(
            "EVM private key must be 64 hex characters (with or without '0x' prefix)."
        )
    try:
        acct = Account.from_key(bytes.fromhex(raw))
        return acct.address, "0x" + raw.lower()
    except Exception as exc:
        raise ValueError(f"Invalid EVM private key: {exc}") from exc


def _derive_from_mnemonic(chain: str, phrase: str) -> tuple[str, str]:
    """Validate BIP-39 mnemonic and derive address + private key for the given chain."""
    from mnemonic import Mnemonic
    mnemo  = Mnemonic("english")
    phrase = " ".join(phrase.lower().split())
    word_count = len(phrase.split())
    if word_count not in (12, 15, 18, 21, 24):
        raise ValueError(
            f"Recovery phrase must be 12, 15, 18, 21, or 24 words (got {word_count})."
        )
    if not mnemo.check(phrase):
        raise ValueError(
            "Invalid recovery phrase — one or more words are not in the BIP-39 word list.\n"
            "Double-check the spelling of each word."
        )

    if chain == "SOL":
        return _sol_from_mnemonic(phrase)
    else:
        return _evm_from_mnemonic(phrase)


def _sol_from_mnemonic(phrase: str) -> tuple[str, str]:
    """Derive Solana keypair from BIP-39 mnemonic via BIP44 path m/44'/501'/0'/0'."""
    try:
        from bip_utils import (
            Bip39SeedGenerator,
            Bip44,
            Bip44Coins,
            Bip44Changes,
        )
        # Correct bip_utils 2.x API: instantiate then call Generate()
        seed_bytes = Bip39SeedGenerator(phrase).Generate()
        bip44_mst  = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        bip44_acc  = (
            bip44_mst
            .Purpose()
            .Coin()
            .Account(0)
            .Change(Bip44Changes.CHAIN_EXT)
            .AddressIndex(0)
        )
        private_key_bytes = bip44_acc.PrivateKey().Raw().ToBytes()

        from solders.keypair import Keypair
        kp = Keypair.from_seed(private_key_bytes)
        return str(kp.pubkey()), private_key_bytes.hex()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not derive Solana wallet from phrase: {exc}") from exc


def _evm_from_mnemonic(phrase: str) -> tuple[str, str]:
    """Derive EVM keypair from BIP-39 mnemonic via eth_account (BIP44 m/44'/60'/0'/0/0)."""
    try:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        acct = Account.from_mnemonic(phrase)
        return acct.address, acct.key.hex()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not derive EVM wallet from phrase: {exc}") from exc


# ── ConversationHandler builder ───────────────────────────────────────────────

def import_wallet_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(import_wallet_start, pattern=r"^import:wallet$"),
            CommandHandler("import", import_wallet_start_command),
            CommandHandler("importwallet", import_wallet_start_command),
        ],
        states={
            IMP_METHOD: [
                CallbackQueryHandler(import_method_selected, pattern=r"^import:(pk|mnemonic)$"),
            ],
            IMP_CHAIN: [
                CallbackQueryHandler(import_chain_selected, pattern=r"^import:chain:(SOL|ETH|BNB)$"),
            ],
            IMP_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_input_received),
            ],
            IMP_CONFIRM: [
                CallbackQueryHandler(import_confirmed, pattern=r"^import:confirm:yes$"),
                CallbackQueryHandler(import_cancelled, pattern=r"^import:confirm:no$"),
            ],
        },
        fallbacks=[
            # Cancel button at any stage
            CallbackQueryHandler(import_cancelled, pattern=f"^{Keyboards.CB_CANCEL}$"),
            # /start typed mid-flow — always exits cleanly and re-runs start
            CommandHandler("start", import_cancelled),
        ],
        allow_reentry=True,   # re-entering import always restarts cleanly
        per_message=False,
    )

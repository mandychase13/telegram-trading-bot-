from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup


class Keyboards:
    # ── Callback data constants ──────────────────────────────────────────────
    # Main
    CB_DASHBOARD        = "menu:dashboard"
    CB_WALLET           = "menu:wallet"
    CB_COPYTRADE        = "menu:copytrade"
    CB_PORTFOLIO        = "menu:portfolio"
    CB_AUTOTRADE        = "menu:autotrade"
    CB_MANAGE_WALLETS   = "menu:manage_wallets"
    CB_SETTINGS         = "menu:settings"
    CB_HELP             = "menu:help"
    CB_CONTINUE         = "menu:continue"

    # Wallet Import
    CB_IMPORT_WALLET    = "import:wallet"

    # Withdrawal
    CB_WITHDRAW         = "trade:withdraw"

    # Wallet
    CB_WALLET_REFRESH   = "wallet:refresh"
    CB_WALLET_DEPOSIT   = "wallet:deposit"
    CB_WALLET_HISTORY   = "wallet:history"
    CB_WALLET_TRANSFER  = "wallet:transfer_menu"

    # Trade
    CB_BUY              = "trade:buy"
    CB_SELL             = "trade:sell"
    CB_TRANSFER         = "trade:transfer"
    CB_CHAIN_SOL        = "chain:SOL"
    CB_CHAIN_ETH        = "chain:ETH"
    CB_CHAIN_BNB        = "chain:BNB"
    CB_CONFIRM_YES      = "confirm:yes"
    CB_CONFIRM_NO       = "confirm:no"
    CB_CANCEL           = "action:cancel"

    # Copytrade
    CB_COPY_ADD         = "copy:add"
    CB_COPY_LIST        = "copy:list"
    CB_COPY_ENABLE      = "copy:enable"
    CB_COPY_DISABLE     = "copy:disable"

    # Autotrade
    CB_AUTO_ENABLE      = "auto:enable"
    CB_AUTO_DISABLE     = "auto:disable"
    CB_AUTO_CONFIG      = "auto:config"

    # Settings categories
    CB_SET_SLIPPAGE     = "set:slippage"
    CB_SET_PRIORITY     = "set:priority_fee"
    CB_SET_NOTIF        = "set:notifications"
    CB_SET_LANGUAGE     = "set:language"
    CB_SET_DEFAULTS     = "set:defaults"
    CB_SET_STOPLOSS     = "set:stop_loss"
    CB_SET_TAKEPROFIT   = "set:take_profit"

    # ── Keyboard builders ────────────────────────────────────────────────────
    @staticmethod
    def continue_button() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("▶️  Continue", callback_data=Keyboards.CB_CONTINUE)
        ]])

    @staticmethod
    def returning_user_wallet() -> InlineKeyboardMarkup:
        """Single prominent wallet entry point for returning users."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("💼 Go to Wallet", callback_data=Keyboards.CB_WALLET)
        ]])

    @staticmethod
    def dashboard_main(addresses: dict[str, str] | None = None) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("🔂 Refresh", callback_data=Keyboards.CB_WALLET_REFRESH),
                InlineKeyboardButton("🛒 Buy",     callback_data=Keyboards.CB_BUY),
                InlineKeyboardButton("💰 Sell",    callback_data=Keyboards.CB_SELL),
            ],
            [
                InlineKeyboardButton("🔄 Transfer",  callback_data=Keyboards.CB_TRANSFER),
                InlineKeyboardButton("💸 Withdraw",  callback_data=Keyboards.CB_WITHDRAW),
            ],
            [
                InlineKeyboardButton("📈 Copytrade",      callback_data=Keyboards.CB_COPYTRADE),
                InlineKeyboardButton("🤖 Autotrade",      callback_data=Keyboards.CB_AUTOTRADE),
            ],
            [
                InlineKeyboardButton("👥 Manage Wallets", callback_data=Keyboards.CB_MANAGE_WALLETS),
                InlineKeyboardButton("📊 Portfolio",      callback_data=Keyboards.CB_PORTFOLIO),
            ],
            [
                InlineKeyboardButton("💼 Wallet",        callback_data=Keyboards.CB_WALLET),
                InlineKeyboardButton("📥 Import Wallet", callback_data=Keyboards.CB_IMPORT_WALLET),
            ],
            [
                InlineKeyboardButton("⚙️ Settings", callback_data=Keyboards.CB_SETTINGS),
            ],
        ]
        if addresses:
            rows.insert(0, Keyboards._copy_address_rows(addresses))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _copy_address_rows(addresses: dict[str, str]) -> list[InlineKeyboardButton]:
        """Return full-address buttons that copy the complete address."""
        labels = (("SOL", "◎"), ("ETH", "Ξ"), ("BNB", "🟡"))
        return [
            InlineKeyboardButton(
                f"{icon} {chain}: {addresses.get(chain, '')}",
                copy_text=CopyTextButton(text=addresses.get(chain, "")),
            )
            for chain, icon in labels
            if addresses.get(chain)
        ]

    @staticmethod
    def wallet_menu(addresses: dict[str, str] | None = None) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("🔂 Refresh Balance", callback_data=Keyboards.CB_WALLET_REFRESH),
            ],
            [
                InlineKeyboardButton("📥 Deposit",  callback_data=Keyboards.CB_WALLET_DEPOSIT),
                InlineKeyboardButton("🔄 Transfer", callback_data=Keyboards.CB_WALLET_TRANSFER),
            ],
            [
                InlineKeyboardButton("📜 History",  callback_data=Keyboards.CB_WALLET_HISTORY),
            ],
            [
                InlineKeyboardButton("⬅️ Back",     callback_data=Keyboards.CB_DASHBOARD),
            ],
        ]
        if addresses:
            rows.insert(0, Keyboards._copy_address_rows(addresses))
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def address_copy_menu(addresses: dict[str, str]) -> InlineKeyboardMarkup:
        """Copy buttons plus a dashboard back button for address-only screens."""
        rows = [Keyboards._copy_address_rows(addresses)]
        rows.append([
            InlineKeyboardButton("⬅️ Back", callback_data=Keyboards.CB_DASHBOARD)
        ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def chain_select(action: str = "buy") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("◎ Solana (SOL)", callback_data=f"chain_sel:{action}:SOL"),
                InlineKeyboardButton("Ξ Ethereum (ETH)", callback_data=f"chain_sel:{action}:ETH"),
            ],
            [
                InlineKeyboardButton("🟡 BNB Chain", callback_data=f"chain_sel:{action}:BNB"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=Keyboards.CB_CANCEL)],
        ])

    @staticmethod
    def confirm(extra: str = "") -> InlineKeyboardMarkup:
        suffix = f":{extra}" if extra else ""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:yes{suffix}"),
                InlineKeyboardButton("❌ Cancel",  callback_data=Keyboards.CB_CANCEL),
            ]
        ])

    @staticmethod
    def back_to_dashboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back to Dashboard", callback_data=Keyboards.CB_DASHBOARD)
        ]])

    @staticmethod
    def copytrade_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Wallet to Copy",   callback_data=Keyboards.CB_COPY_ADD)],
            [InlineKeyboardButton("📋 View Followed Wallets", callback_data=Keyboards.CB_COPY_LIST)],
            [
                InlineKeyboardButton("▶️ Enable Copy Trading",  callback_data=Keyboards.CB_COPY_ENABLE),
                InlineKeyboardButton("⏹ Disable",              callback_data=Keyboards.CB_COPY_DISABLE),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=Keyboards.CB_DASHBOARD)],
        ])

    @staticmethod
    def autotrade_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Configure Autotrade", callback_data=Keyboards.CB_AUTO_CONFIG)],
            [
                InlineKeyboardButton("▶️ Enable",  callback_data=Keyboards.CB_AUTO_ENABLE),
                InlineKeyboardButton("⏹ Disable", callback_data=Keyboards.CB_AUTO_DISABLE),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=Keyboards.CB_DASHBOARD)],
        ])

    @staticmethod
    def settings_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📉 Slippage",       callback_data=Keyboards.CB_SET_SLIPPAGE),
                InlineKeyboardButton("⚡ Priority Fee",   callback_data=Keyboards.CB_SET_PRIORITY),
            ],
            [
                InlineKeyboardButton("🔔 Notifications",  callback_data=Keyboards.CB_SET_NOTIF),
                InlineKeyboardButton("🌐 Language",       callback_data=Keyboards.CB_SET_LANGUAGE),
            ],
            [
                InlineKeyboardButton("◎ Default SOL",    callback_data="set:default_sol"),
                InlineKeyboardButton("Ξ Default ETH",    callback_data="set:default_eth"),
                InlineKeyboardButton("🟡 Default BNB",   callback_data="set:default_bnb"),
            ],
            [
                InlineKeyboardButton("🛑 Stop Loss",      callback_data=Keyboards.CB_SET_STOPLOSS),
                InlineKeyboardButton("🎯 Take Profit",    callback_data=Keyboards.CB_SET_TAKEPROFIT),
            ],
            [
                InlineKeyboardButton("🔢 Max Daily Trades", callback_data="set:max_trades"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=Keyboards.CB_DASHBOARD)],
        ])

    @staticmethod
    def manage_wallets_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Wallet",    callback_data="mgwallet:add")],
            [InlineKeyboardButton("📋 View Wallets",  callback_data="mgwallet:list")],
            [InlineKeyboardButton("🗑 Remove Wallet", callback_data="mgwallet:remove")],
            [InlineKeyboardButton("⬅️ Back",          callback_data=Keyboards.CB_DASHBOARD)],
        ])

    @staticmethod
    def cancel_only() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=Keyboards.CB_CANCEL)
        ]])

    @staticmethod
    def manage_wallets_with_import() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Wallet",      callback_data="mgwallet:add")],
            [InlineKeyboardButton("📥 Import Wallet",   callback_data=Keyboards.CB_IMPORT_WALLET)],
            [InlineKeyboardButton("📋 View Wallets",    callback_data="mgwallet:list")],
            [InlineKeyboardButton("🗑 Remove Wallet",   callback_data="mgwallet:remove")],
            [InlineKeyboardButton("⬅️ Back",             callback_data=Keyboards.CB_DASHBOARD)],
        ])

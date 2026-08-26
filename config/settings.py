import os
from urllib.parse import urlparse
from dataclasses import dataclass


@dataclass
class Settings:
    # Telegram
    telegram_bot_token: str = ""

    # Database
    database_url: str = ""

    # Blockchain RPCs
    solana_rpc_api_key: str = ""
    solana_rpc_url_override: str = ""
    ethereum_rpc_api_key: str = ""
    ethereum_rpc_url_override: str = ""
    bnb_rpc_url: str = ""

    # APIs
    price_api_key: str = ""
    jupiter_api_key: str = ""

    # Security
    encryption_key: str = ""

    # Admin
    admin_telegram_id: int = 0

    # Bot
    log_level: str = "INFO"

    @staticmethod
    def _is_url(value: str) -> bool:
        return value.strip().lower().startswith(("http://", "https://"))

    @staticmethod
    def _valid_endpoint(value: str) -> bool:
        parsed = urlparse(value.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)

    @staticmethod
    def endpoint_host(value: str) -> str:
        """Return a safe endpoint identifier for logs, without query secrets."""
        parsed = urlparse(value.strip())
        return parsed.netloc or "<invalid-endpoint>"

    @property
    def solana_rpc_url(self) -> str:
        if self.solana_rpc_url_override:
            return self.solana_rpc_url_override.strip()
        if self.solana_rpc_api_key:
            if self._is_url(self.solana_rpc_api_key):
                return self.solana_rpc_api_key.strip()
            return f"https://mainnet.helius-rpc.com/?api-key={self.solana_rpc_api_key}"
        return "https://api.mainnet-beta.solana.com"

    @property
    def ethereum_rpc_url(self) -> str:
        if self.ethereum_rpc_url_override:
            return self.ethereum_rpc_url_override.strip()
        if self.ethereum_rpc_api_key:
            if self._is_url(self.ethereum_rpc_api_key):
                return self.ethereum_rpc_api_key.strip()
            return f"https://eth-mainnet.g.alchemy.com/v2/{self.ethereum_rpc_api_key}"
        return "https://cloudflare-eth.com"

    @property
    def bnb_rpc_endpoint(self) -> str:
        return self.bnb_rpc_url or "https://bsc-dataseed1.binance.org/"


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        database_url=os.environ.get("DATABASE_URL", ""),
        solana_rpc_api_key=os.environ.get("SOLANA_RPC_API_KEY", ""),
        solana_rpc_url_override=os.environ.get("SOLANA_RPC_URL", ""),
        ethereum_rpc_api_key=os.environ.get("ETHEREUM_RPC_API_KEY", ""),
        ethereum_rpc_url_override=os.environ.get("ETHEREUM_RPC_URL", ""),
        bnb_rpc_url=os.environ.get("BNB_RPC_URL", os.environ.get("BNB_RPC_API_KEY", "")),
        price_api_key=os.environ.get("PRICE_API_KEY", ""),
        jupiter_api_key=os.environ.get("JUPITER_API_KEY", ""),
        encryption_key=os.environ.get("ENCRYPTION_KEY", os.environ.get("SESSION_SECRET", "default-key-please-set-ENCRYPTION_KEY")),
        admin_telegram_id=int(os.environ.get("ADMIN_TELEGRAM_ID", "0") or "0"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


settings = load_settings()

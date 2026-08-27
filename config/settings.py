import os
from urllib.parse import urlparse
from dataclasses import dataclass

_DEFAULT_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_DEFAULT_ETHEREUM_RPC = "https://cloudflare-eth.com"
_DEFAULT_BNB_RPC = "https://bsc-dataseed1.binance.org/"


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
    jupiter_request_interval_seconds: float = 1.10

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
        if Settings._is_placeholder(value):
            return False
        parsed = urlparse(value.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        candidate = value.strip().lower()
        parsed = urlparse(candidate)
        hostname = parsed.hostname or candidate
        return (
            not candidate
            or hostname.startswith(("your_", "your-", "replace_", "replace-", "placeholder"))
            or hostname in {"changeme", "change-me", "none", "null"}
        )

    @staticmethod
    def endpoint_host(value: str) -> str:
        """Return a safe hostname:port identifier without URL credentials/query."""
        parsed = urlparse(value.strip())
        if not parsed.hostname:
            return "<invalid-endpoint>"
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            port = ""
        return f"{parsed.hostname}{port}"

    def endpoint_diagnostics(self) -> list[dict[str, str]]:
        """Return safe endpoint diagnostics; never include keys or query strings."""
        endpoints = (
            ("solana_rpc", self.solana_rpc_url, self.solana_rpc_url_override, "SOLANA_RPC_URL"),
            ("ethereum_rpc", self.ethereum_rpc_url, self.ethereum_rpc_url_override, "ETHEREUM_RPC_URL"),
            ("bnb_rpc", self.bnb_rpc_endpoint, self.bnb_rpc_url, "BNB_RPC_URL"),
        )
        return [
            {
                "service": service,
                "host": self.endpoint_host(endpoint),
                "valid": str(self._valid_endpoint(endpoint)).lower(),
                "configured_host": (
                    self.endpoint_host(configured)
                    if self._is_url(configured)
                    else "<unset-or-api-key>"
                ),
                "configured_valid": (
                    str(self._valid_endpoint(configured)).lower()
                    if configured
                    else "unset"
                ),
                "source": source if configured else "default",
            }
            for service, endpoint, configured, source in endpoints
        ]

    @property
    def solana_rpc_url(self) -> str:
        if self._valid_endpoint(self.solana_rpc_url_override):
            return self.solana_rpc_url_override.strip()
        if self.solana_rpc_api_key:
            if self._is_url(self.solana_rpc_api_key):
                if self._valid_endpoint(self.solana_rpc_api_key):
                    return self.solana_rpc_api_key.strip()
            elif not self._is_placeholder(self.solana_rpc_api_key):
                return f"https://mainnet.helius-rpc.com/?api-key={self.solana_rpc_api_key}"
        return _DEFAULT_SOLANA_RPC

    @property
    def ethereum_rpc_url(self) -> str:
        if self._valid_endpoint(self.ethereum_rpc_url_override):
            return self.ethereum_rpc_url_override.strip()
        if self.ethereum_rpc_api_key:
            if self._is_url(self.ethereum_rpc_api_key):
                if self._valid_endpoint(self.ethereum_rpc_api_key):
                    return self.ethereum_rpc_api_key.strip()
            elif not self._is_placeholder(self.ethereum_rpc_api_key):
                return f"https://eth-mainnet.g.alchemy.com/v2/{self.ethereum_rpc_api_key}"
        return _DEFAULT_ETHEREUM_RPC

    @property
    def bnb_rpc_endpoint(self) -> str:
        if self._valid_endpoint(self.bnb_rpc_url):
            return self.bnb_rpc_url.strip()
        return _DEFAULT_BNB_RPC


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
        jupiter_request_interval_seconds=float(
            os.environ.get("JUPITER_REQUEST_INTERVAL_SECONDS", "1.10") or "1.10"
        ),
        encryption_key=os.environ.get("ENCRYPTION_KEY", os.environ.get("SESSION_SECRET", "default-key-please-set-ENCRYPTION_KEY")),
        admin_telegram_id=int(os.environ.get("ADMIN_TELEGRAM_ID", "0") or "0"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


settings = load_settings()

import os
from typing import Optional

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Systémová konfigurace. Hodnoty lze přebít pomocí .env."""

    # Project
    PROJECT_NAME: str = "PREDATOR"
    ENV: str = "development"

    # Database
    DB_PATH: str = "data/predator.db"

    # Solana RPC
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_RPC_FALLBACK: Optional[str] = None

    # API Endpoints
    DEXSCREENER_API: str = "https://api.dexscreener.com/latest/dex"
    JUPITER_API_URL: str = "https://quote-api.jup.ag/v6"

    # System mode
    SYSTEM_MODE: str = "SHADOW"

    # Position limits
    MAX_POSITION_SIZE: float = 2.0
    MAX_OPEN_POSITIONS: int = 3
    DAILY_DRAWDOWN_PCT: float = -30.0
    SOLANA_RESERVE: float = 0.005

    # Scanner
    SCAN_INTERVAL_MIN: int = 45
    SCAN_INTERVAL_MAX: int = 60
    MAX_COIN_AGE_MINUTES: int = 30

    # Hunter
    HUNTER_MIN_LIQUIDITY: float = 2000.0
    HUNTER_MIN_VOLUME: float = 1000.0

    # Logging
    LOG_LEVEL: str = "INFO"

    # Notifications
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    X_BEARER_TOKEN: Optional[str] = None

    # Notifier
    NOTIFIER_FOLLOWUP_SEC: int = 1800

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


def _load_yaml_config() -> dict:
    """Načte config/system.yaml pokud existuje."""
    yaml_path = os.path.join(os.path.dirname(__file__), "..", "config", "system.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


yaml_config = _load_yaml_config()

settings = Settings()

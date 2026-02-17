"""
core/config.py – Systémová konfigurace (Pydantic Settings).

Rozšíření: přidáno VIRTUAL_BALANCE, X_API_KEY/SECRET, X_ACCESS_TOKEN/SECRET
pro OAuth 1.0a, slippage settings.
"""

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

    # Virtual balance for SHADOW/PAPER mode (SOL)
    VIRTUAL_BALANCE_SOL: float = 10.0

    # Scanner
    SCAN_INTERVAL_MIN: int = 45
    SCAN_INTERVAL_MAX: int = 60
    MAX_COIN_AGE_MINUTES: int = 30

    # Hunter
    HUNTER_MIN_LIQUIDITY: float = 2000.0
    HUNTER_MIN_VOLUME: float = 1000.0

    # Slippage
    SLIPPAGE_MAX_BPS: int = 500  # 5% max slippage
    PRIORITY_FEE_LAMPORTS: int = 10000

    # Logging
    LOG_LEVEL: str = "INFO"

    # Notifications - Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Notifications - X (Twitter) OAuth 1.0a (4 tokeny)
    X_API_KEY: Optional[str] = None
    X_API_SECRET: Optional[str] = None
    X_ACCESS_TOKEN: Optional[str] = None
    X_ACCESS_TOKEN_SECRET: Optional[str] = None
    # Legacy bearer (pro čtení, ne pro POST)
    X_BEARER_TOKEN: Optional[str] = None

    # Notifier
    NOTIFIER_FOLLOWUP_SEC: int = 1800

    # Wallet
    WALLET_PRIVATE_KEY: Optional[str] = None

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

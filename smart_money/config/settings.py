from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API credentials
    moralis_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Database — SQLite (default) or PostgreSQL
    db_path: str = "smart_money.db"
    use_postgres: bool = False
    postgres_url: str = ""      # postgresql+asyncpg://user:pass@localhost/smart_money_db

    # Chains
    enabled_chains: str = "eth,bsc,polygon,base"

    # Moralis plan
    moralis_plan: str = "starter"

    # Scan cadence (seconds)
    scan_interval_wallets: int = 30
    scan_interval_tokens: int = 60
    score_update_interval: int = 300
    top100_sync_interval: int = 600
    graph_update_interval: int = 1800

    # Signal thresholds
    smart_wallet_score_threshold: int = 70
    coordinated_buy_min_wallets: int = 3
    coordinated_buy_window_hours: int = 4
    whale_move_min_usd: float = 50_000.0

    # Top wallet count
    top_wallet_count: int = 100

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/smart_money.log"

    # Seed tokens for wallet discovery (comma-separated addresses)
    seed_tokens: str = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

    # Web API / dashboard
    api_port: int = 8000
    api_host: str = "0.0.0.0"

    # Moralis Streams
    webhook_base_url: str = ""   # http://YOUR_SERVER_IP:8000
    streams_secret: str = ""     # HMAC secret used by Moralis to sign webhooks
    moralis_stream_id: str = ""  # persisted after first stream creation

    # Derived
    @property
    def chains(self) -> List[str]:
        return [c.strip() for c in self.enabled_chains.split(",") if c.strip()]

    @property
    def seed_token_list(self) -> List[str]:
        return [t.strip() for t in self.seed_tokens.split(",") if t.strip()]

    @property
    def cu_per_second(self) -> int:
        return {"starter": 1000, "pro": 2000, "business": 5000}.get(
            self.moralis_plan.lower(), 1000
        )

    @property
    def db_url(self) -> str:
        if self.use_postgres and self.postgres_url:
            return self.postgres_url
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in self.db_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

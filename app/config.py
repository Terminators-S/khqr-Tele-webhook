from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./khqr-dev.db"
    internal_secret: str = "dev-internal-secret-change-me"
    checkout_ttl_seconds: int = 480
    late_match_grace_seconds: int = 300
    history_recovery_seconds: int = 604800
    offset_max_minor: int = 29
    webhook_max_attempts: int = 12
    webhook_timeout_seconds: int = 10
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_credentials_path: str = "runtime/telegram-credentials.json"
    telegram_session_name: str = "khqr_collector"
    telegram_shadow_only: bool = True
    allow_live_telegram: bool = False
    allow_shadow_promotion: bool = False
    dashboard_enabled: bool = True
    dashboard_session_ttl_seconds: int = 43200
    dashboard_cookie_secure: bool = False
    log_level: str = "INFO"

    @property
    def reservation_seconds(self) -> int:
        return self.checkout_ttl_seconds + self.late_match_grace_seconds


@lru_cache
def get_settings() -> Settings:
    return Settings()

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LinkPlease Webhook Service"
    environment: str = "development"
    database_url: str = "sqlite:///./linkplease.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend: str | None = None
    pseudogram_api_key: str = ""
    pseudogram_base_url: str = "https://pseudogram-api.onrender.com"
    pseudogram_send_rate_limit: int = 10
    pseudogram_send_rate_window_seconds: int = 60
    dm_max_attempts: int = 5
    dm_sending_stale_seconds: int = 300
    dm_max_delivery_attempts: int = 3
    dm_reconcile_delay_seconds: int = 30
    dm_reconciling_stale_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    def validate_runtime(self) -> None:
        if self.environment.lower() == "production" and self.database_url.startswith("sqlite"):
            raise RuntimeError("SQLite DATABASE_URL is not allowed in production")

    def require_pseudogram_api_key(self) -> str:
        if not self.pseudogram_api_key:
            raise RuntimeError("PSEUDOGRAM_API_KEY is required")
        return self.pseudogram_api_key


settings = Settings()
settings.validate_runtime()

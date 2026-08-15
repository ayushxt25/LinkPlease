from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LinkPlease Webhook Service"
    database_url: str = "sqlite:///./linkplease.db"
    redis_url: str = "redis://localhost:6379/0"
    pseudogram_api_key: str = ""
    pseudogram_base_url: str = "https://pseudogram-api.onrender.com"
    dm_max_attempts: int = 5
    dm_sending_stale_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LinkPlease Webhook Service"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

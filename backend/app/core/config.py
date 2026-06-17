from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_base_url: str = "http://localhost:8000"
    app_name: str = "Auditimi Teknik Bot"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://atf:atf@localhost:5432/atf"
    alembic_database_url: str = "postgresql://atf:atf@localhost:5432/atf"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "atf_minio"
    minio_secret_key: str = "atf_minio_secret"
    minio_bucket: str = "atf"
    minio_secure: bool = False

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = Field(default="change-me", min_length=1)

    openai_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


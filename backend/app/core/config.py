from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_base_url: str = "http://localhost:8000"
    app_name: str = "Auditimi Teknik Bot"
    app_timezone: str = "Europe/Tirane"
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

    user_api_key_encryption_secret: str = "local-dev-insecure-change-me"

    openai_api_key: str = ""
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 45
    openai_max_output_tokens: int = 1800
    ai_senior_review_enabled: bool = True

    ocr_enabled: bool = True
    ocr_languages: str = "sqi+eng"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_min_confidence: float = Field(default=30.0, ge=0, le=100)
    ocr_page_timeout_seconds: int = Field(default=120, ge=10, le=600)

    mpp_extractor_command: str = ""
    mpp_extractor_timeout_seconds: int = Field(default=120, ge=5, le=600)

    prompt_worker_lease_seconds: int = Field(default=300, ge=30, le=3_600)
    prompt_parse_poll_seconds: int = Field(default=5, ge=2, le=300)
    prompt_parse_timeout_seconds: int = Field(default=7_200, ge=300, le=86_400)
    prompt_confirmation_timeout_seconds: int = Field(default=900, ge=60, le=86_400)
    prompt_review_poll_seconds: int = Field(default=10, ge=2, le=300)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

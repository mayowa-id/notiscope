from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    app_secret_key: str = "change-me"
    log_level: str = "INFO"

    # PostgreSQL
    database_url: str = "postgresql://notiscope:notiscope_secret@localhost:5432/notiscope"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_max_retries: int = 5
    celery_retry_backoff: int = 2
    celery_task_soft_time_limit: int = 60
    celery_task_time_limit: int = 90

    # SendGrid
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "noreply@notiscope.dev"
    sendgrid_from_name: str = "Notiscope"

    # AWS SES
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    ses_from_email: str = "noreply@notiscope.dev"

    # Idempotency
    idempotency_key_ttl_hours: int = 24
    redis_lock_ttl_seconds: int = 30

    # DLQ
    dlq_check_interval_minutes: int = 5
    dlq_failed_threshold_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton, call this everywhere you need config."""
    return Settings()

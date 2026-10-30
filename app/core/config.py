from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Bayline API"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    rate_limit_enabled: bool = True
    rate_limit_ip_per_minute: int = Field(default=1200, ge=1, le=100000)
    rate_limit_user_per_minute: int = Field(default=300, ge=1, le=100000)
    rate_limit_anonymous_per_minute: int = Field(default=120, ge=1, le=100000)
    rate_limit_read_per_minute: int = Field(default=120, ge=1, le=100000)
    rate_limit_write_per_minute: int = Field(default=60, ge=1, le=100000)
    rate_limit_expensive_per_minute: int = Field(default=20, ge=1, le=100000)
    rate_limit_auth_per_minute: int = Field(default=30, ge=1, le=100000)

    allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    database_url: str
    secret_key: str
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    platform_admin_email: str
    platform_admin_password: str

    recovery_otp_minutes: int = Field(default=10, ge=1, le=30)
    recovery_otp_attempts: int = Field(default=5, ge=1, le=10)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = Field(default=None, repr=False)
    smtp_from_email: str | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = Field(default=None, repr=False)
    twilio_from_number: str | None = None

    logs_dir: str = "logs"
    logs_timezone: str = "America/Caracas"

    vehicle_financing_webhook_secret: str | None = Field(default=None, min_length=32, repr=False)

    push_enabled: bool = False
    firebase_project_id: str | None = None
    firebase_credentials_file: str | None = None
    private_uploads_dir: str = "private-uploads"
    s3_private_bucket_name: str | None = None
    uploads_environment: str | None = None

    uploads_dir: str = "uploads"
    max_upload_mb: int = 8

    # S3-backed uploads — optional. When s3_bucket_name is unset, every
    # upload falls back to local disk (uploads_dir) exactly as before, so
    # dev/test environments never need real AWS credentials.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str = "us-east-1"
    s3_bucket_name: str | None = None

    @property
    def cookie_secure(self) -> bool:
        return self.app_env in {"production", "staging"}

    @model_validator(mode="after")
    def validate_http_security(self):
        if not self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must not be empty")
        for origin in self.allowed_origins:
            url = urlsplit(origin)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or "*" in origin
                or url.path
                or url.query
                or url.fragment
                or url.username
                or url.password
            ):
                raise ValueError("ALLOWED_ORIGINS must contain exact origins without paths")
            if self.cookie_secure and url.scheme != "https":
                raise ValueError("Production/staging require HTTPS origins")
        if self.cookie_secure and (self.debug or len(self.secret_key) < 32):
            raise ValueError(
                "Production/staging require DEBUG=false and a SECRET_KEY of at least 32 characters"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of the application settings."""
    return Settings()

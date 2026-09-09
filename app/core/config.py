from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Bayline API"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    database_url: str
    secret_key: str
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    platform_admin_email: str
    platform_admin_password: str

    uploads_dir: str = "uploads"
    max_upload_mb: int = 8

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

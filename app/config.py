"""Application configuration (12-factor, env driven)."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    APP_NAME: str = "Online Quran College OS"
    APP_ENV: str = "development"
    SECRET_KEY: str = "dev-secret-change-me"
    DATABASE_URL: str = "sqlite:///./data/oqc.db"

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_database_url(cls, v: str) -> str:
        """Managed hosts (Render, Heroku, Railway) hand out postgres:// or postgresql:// URLs.

        Bare "postgresql://" makes SQLAlchemy reach for psycopg2, which is not installed - this
        project uses psycopg 3. Pin the driver explicitly so the same URL works everywhere.
        """
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    BASE_URL: str = "http://127.0.0.1:8000"
    SESSION_HOURS: int = 12
    ACCESS_TOKEN_MINUTES: int = 720
    BASE_CURRENCY: str = "PKR"
    DEFAULT_TIMEZONE: str = "Asia/Karachi"
    VIDEO_PROVIDER: str = "jitsi"
    JITSI_DOMAIN: str = "meet.jit.si"
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_ID: str = ""
    GHL_API_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@onlinequrancollege.local"
    AI_PROVIDER: str = "simulated"
    AI_API_KEY: str = ""
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15

    @property
    def storage_dir(self) -> Path:
        return BASE_DIR / "storage"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

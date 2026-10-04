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
        # A relative SQLite path is resolved against the current working directory, so the app only
        # starts when it happens to be launched from the project root ("unable to open database
        # file" otherwise). Anchor it to the project instead, so any launcher works.
        for prefix in ("sqlite:///./", "sqlite:///"):
            if v.startswith(prefix):
                rest = v[len(prefix):]
                if rest and not rest.startswith("/") and not (len(rest) > 1 and rest[1] == ":"):
                    return "sqlite:///" + (BASE_DIR / rest).as_posix()
                break
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
    # The scheduler runs inside the web process (one per uvicorn worker). Set false on the web workers when a
    # separate jobs process runs with it on, so more than one worker does not run every job twice.
    SCHEDULER_ENABLED: bool = True
    # Secure flag on the session cookie. None = derived from APP_ENV (production => HTTPS only). Set
    # explicitly to serve a production-mode instance over plain HTTP (demo) or to force it on in staging.
    COOKIE_SECURE: bool | None = None

    @property
    def storage_dir(self) -> Path:
        # Always <repo>/storage: the database stores file paths relative to the repository root, so the
        # directory is not relocatable. Containers mount their volume at /app/storage for the same reason.
        return BASE_DIR / "storage"

    @property
    def cookie_secure(self) -> bool:
        return self.APP_ENV == "production" if self.COOKIE_SECURE is None else self.COOKIE_SECURE

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

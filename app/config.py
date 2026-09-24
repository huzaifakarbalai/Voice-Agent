import os

from dotenv import load_dotenv

load_dotenv()


def _normalize_database_url(raw: str) -> str:
    """Neon hands out postgresql:// URLs. SQLAlchemy 2.0 needs the driver named
    explicitly, and this project installs psycopg 3 rather than psycopg2."""
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg://", 1)
    return raw


class Settings:
    def __init__(self) -> None:
        self.database_url = _normalize_database_url(
            os.environ.get("DATABASE_URL", "sqlite+pysqlite:///./local.db")
        )
        # Optional. When set, /voice/webhook rejects requests without a matching header.
        self.vapi_secret = os.environ.get("VAPI_SECRET") or None


settings = Settings()

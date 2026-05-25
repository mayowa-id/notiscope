from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,       # reconnect on stale connections
    pool_size=10,
    max_overflow=20,
    echo=settings.app_env == "development",
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a DB session per request, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

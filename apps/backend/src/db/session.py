from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import settings

# pool_size/max_overflow sized past the largest thread pool that opens its own
# Session per worker (crawl.py's ACCOUNTS_PER_QUERY, vision.py's VIDEO_WORKERS,
# both 20) — the SQLAlchemy default (5 + 10 overflow = 15) is short of that,
# and the shortfall silently drops accounts via a 30s pool-checkout timeout
# rather than raising anywhere obvious.
engine = create_engine(settings.database_url, pool_size=20, max_overflow=20)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

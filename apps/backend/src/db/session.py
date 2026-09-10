from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import settings

# pool_size/max_overflow sized past crawl.py's INGEST_WORKERS (20 today, and
# headroom kept for the 80-worker experiment it may be raised to again) — each
# ingest worker's session checks out a connection on its first SELECT
# (get_existing_post_external_ids, ingest.py) and doesn't release it until
# store_account_and_posts commits at the very end, so a connection is held
# for that worker's *entire* account (including the serial video-download
# loop), not just its brief actual queries. Concurrent workers therefore map
# ~1:1 onto held connections, not "occasional checkouts" — undersizing this
# pool doesn't error, it silently drops accounts via a 30s pool-checkout
# timeout. Also covers vision.py's VIDEO_WORKERS (50), which runs after the
# ingest phase, not concurrently with it. Postgres's own max_connections
# (docker-compose.yml) must clear this too.
engine = create_engine(settings.database_url, pool_size=90, max_overflow=30)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

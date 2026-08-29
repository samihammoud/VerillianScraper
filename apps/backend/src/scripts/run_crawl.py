"""CLI entry point for the crawl loop.

One blocking script, no daemon and no scheduler — a multi-round run is a
multi-hour (eventually multi-day) foreground process. Advisory-locked, copying
run_enrich: two overlapping runs would double-spend RapidAPI and Gemini quota
on identical work.

Usage: make crawl WORLD=pets ROUNDS=1
   or: python -m src.scripts.run_crawl pets 1
"""

import sys

from sqlalchemy import text

from src.db.session import SessionLocal, engine
from src.services.crawl import run_round

ADVISORY_LOCK_KEY = 8802  # 8801 is run_enrich's


def main() -> None:
    world_slug = sys.argv[1] if len(sys.argv) > 1 else "pets"
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            print("another crawl run is active; exiting")
            return

        db = SessionLocal()
        try:
            for _ in range(rounds):
                if not run_round(db, world_slug):
                    break
        finally:
            db.close()
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()


if __name__ == "__main__":
    main()

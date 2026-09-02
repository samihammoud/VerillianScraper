"""CLI entry point for stage 1 (bulk ingest): video list + cover bytes only.

Usage: make ingest HANDLES=handle1,handle2 COUNT=100 WORLD=romance
   or: python -m src.scripts.run_ingest handle1,handle2 [count] [world]
"""

import sys

from src.db.session import SessionLocal
from src.services.ingest import ingest_account

DEFAULT_COUNT = 15


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("usage: run_ingest.py handle1,handle2,... [count] [world]", file=sys.stderr)
        raise SystemExit(1)

    handles = [h.strip() for h in sys.argv[1].split(",") if h.strip()]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COUNT
    world = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].strip() else None

    db = SessionLocal()
    try:
        for handle in handles:
            account_id = ingest_account(db, handle, count, discovered_by_world=world)
            print(f"{handle}: {'ok' if account_id else 'no videos found'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

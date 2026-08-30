"""Phase 7: rebuild topology.world_term_stats for one world (or every world).

Run after `make route` — it reads world_posts, not raw posts. Guarded by an
advisory lock like run_enrich.py/run_crawl.py, so an overrunning cron and a
manual run can't stomp on each other's world_term_stats delete+insert.

Usage: make overview WORLD=pets
   or: python -m src.scripts.run_overview pets
   or (no args): every world with at least one routed post
"""

import sys

from sqlalchemy import select, text

from src.db.models import World
from src.db.session import SessionLocal, engine
from src.services.overview import rollup

ADVISORY_LOCK_KEY = 8803  # 8801 is run_enrich's, 8802 is run_crawl's


def _print_top(stats: list[dict], facet: str, n: int = 25) -> None:
    top = sorted((s for s in stats if s["facet"] == facet), key=lambda s: -s["lift"])[:n]
    print(f"\ntop {n} {facet}:")
    for s in top:
        print(f"  {s['view_ratio']:.2f}x  n_posts={s['n_posts']:<4} n_accounts={s['n_accounts']:<3} {s['canon_term']}")


def run_overview(world_slug: str) -> None:
    db = SessionLocal()
    try:
        print(f"world={world_slug}")
        stats = rollup(db, world_slug)
        print(f"  wrote {len(stats)} ranked terms")
        _print_top(stats, "product")
        _print_top(stats, "format")
    finally:
        db.close()


def main() -> None:
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar()
        if not got:
            print("another overview run is active; exiting")
            return

        try:
            if len(sys.argv) > 1:
                run_overview(sys.argv[1].strip())
            else:
                db = SessionLocal()
                try:
                    slugs = [w.slug for w in db.execute(select(World).order_by(World.slug)).scalars()]
                finally:
                    db.close()
                for slug in slugs:
                    run_overview(slug)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()


if __name__ == "__main__":
    main()

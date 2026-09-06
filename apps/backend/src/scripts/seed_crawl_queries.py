"""Seeds round-0 crawl_queries for a world from data/<slug>/crawl/seed_queries.txt.

Round 0 has always been "insert by hand" (see Makefile) because no world had
needed it twice. Kicking off ai-romance-subworld is the second time, so it's
worth the ten lines: one line per non-comment row in seed_queries.txt, intent
"seed", status "pending". Skips entirely if the world already has any round-0
rows — reseeding a world that's already past round 0 is not this script's job.

Usage: python -m src.scripts.seed_crawl_queries ai-romance-subworld
"""

import sys

from sqlalchemy import select

from src.db.models import CrawlQuery
from src.db.session import SessionLocal
from src.services.crawl_config import DATA_DIR


def seed(world_slug: str) -> int:
    path = DATA_DIR / world_slug / "crawl" / "seed_queries.txt"
    queries = [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]

    db = SessionLocal()
    try:
        existing = db.execute(
            select(CrawlQuery.id).where(CrawlQuery.world_slug == world_slug, CrawlQuery.round_no == 0)
        ).first()
        if existing:
            print(f"{world_slug} already has round-0 queries; not reseeding")
            return 0

        db.add_all(
            CrawlQuery(world_slug=world_slug, round_no=0, query_text=q, intent="seed", status="pending")
            for q in queries
        )
        db.commit()
        print(f"seeded {len(queries)} round-0 queries for {world_slug}")
        return len(queries)
    finally:
        db.close()


if __name__ == "__main__":
    seed(sys.argv[1])

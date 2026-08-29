"""Seeds round 0 of a world's crawl with 10 hand-written queries and exits.

Never generates and never marks anything done — that is run_crawl's job.
Refuses if that world already has queries, because re-seeding a live ledger
would hand the generator a duplicated history to reason from. The refusal is
scoped to the world, so seeding a second world is not blocked by the first.

Usage: make seed-crawl WORLD=pets
   or: python -m src.scripts.seed_crawl pets
"""

import sys

from sqlalchemy import func, select

from src.db.models import CrawlQuery, World
from src.db.session import SessionLocal

SEED_QUERIES = {
    "pets": [
        "pets",
        "dog toys",
        "cat products",
        "puppy training",
        "pet grooming",
        "dog food review",
        "aquarium setup",
        "small pet care",
        "pet gadgets",
        "rescue dog",
    ],
}


def main() -> None:
    world_slug = sys.argv[1] if len(sys.argv) > 1 else "pets"

    queries = SEED_QUERIES.get(world_slug)
    if not queries:
        print(f"no seed queries written for world {world_slug!r} — add them to SEED_QUERIES")
        raise SystemExit(1)

    db = SessionLocal()
    try:
        if not db.execute(select(World.id).where(World.slug == world_slug)).scalar_one_or_none():
            print(f"no such world {world_slug!r} — run seed_worlds first")
            raise SystemExit(1)

        existing = db.execute(
            select(func.count()).select_from(CrawlQuery).where(CrawlQuery.world_slug == world_slug)
        ).scalar()
        if existing:
            print(f"{world_slug} already has {existing} queries — refusing to re-seed")
            return

        db.add_all(
            CrawlQuery(world_slug=world_slug, round_no=0, query_text=text, intent="seed", status="pending")
            for text in queries
        )
        db.commit()
        print(f"seeded {len(queries)} queries at round 0 for {world_slug}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

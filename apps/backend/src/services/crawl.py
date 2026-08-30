"""The crawl round loop: queries -> handles -> ingest -> VLM -> next queries.

Owns the loop and nothing else. Every step it drives already exists and is
reused unchanged (ingest_account, describe_posts, query_gen.generate); this
module only sequences them and keeps the ledger honest.

There is no round status column and no state machine. Every step is resumable
from data the pipeline already writes — crawl_queries.status committed per
query, on_conflict_do_nothing on (account_id, external_id) for ingest, and
`visual_description IS NULL AND visual_attempts < 3` for the VLM. Kill it
mid-round and rerun: it continues.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.db.models import Account, CrawlQuery
from src.services import query_gen
from src.services.ingest import ingest_account
from src.services.mapper import page_cursor
from src.services.mapper import search_handles as _handles_from_page
from src.services.tiktok_client import search_videos
from src.services.vision import describe_posts

logger = logging.getLogger(__name__)

POSTS_PER_ACCOUNT = 40  # below ~30 the peak baseline degrades to its top-10% fallback
SEARCH_PAGES = 3
QUERIES_PER_ROUND = 5  # testing-volume cap; matches query_gen.QUERIES_PER_ROUND
ACCOUNTS_PER_QUERY = 5  # testing-volume cap on how many found handles actually get ingested


def search_handles(keyword: str) -> list[str]:
    """Distinct author handles for a keyword. The only place search results are
    touched — they are never mapped into posts, since ingest re-fetches each
    account properly."""
    seen: dict[str, None] = {}
    cursor = "0"
    for _ in range(SEARCH_PAGES):
        try:
            page = search_videos(keyword, cursor=cursor)
        except Exception as exc:
            logger.warning("search failed for %r at cursor=%s: %s", keyword, cursor, exc)
            break

        handles = _handles_from_page(page)
        seen.update(dict.fromkeys(handles))

        has_more, cursor = page_cursor(page)
        if not handles or not has_more:
            break

    if not seen:
        logger.warning("search returned no handles for %r", keyword)
    return list(seen)


def _known_handles(db: Session, handles: list[str]) -> set[str]:
    if not handles:
        return set()
    return set(
        db.execute(select(Account.handle).where(Account.handle.in_(handles))).scalars().all()
    )


def run_query(db: Session, query: CrawlQuery) -> tuple[int, int]:
    """Search one query, ingest every handle it found, mark it done.

    Committed per query so a crash costs one query, not the whole round.
    Returns (handles_found, new_handles) — written even when both are zero,
    because a zero row is the strongest signal the generator gets.
    """
    handles = search_handles(query.query_text)
    known = _known_handles(db, handles)  # sampled before ingest, which would make them all known
    new = [h for h in handles if h not in known]

    for handle in handles[:ACCOUNTS_PER_QUERY]:
        try:
            ingest_account(db, handle, POSTS_PER_ACCOUNT, discovered_by_world=query.world_slug)
        except Exception as exc:
            logger.warning("ingest failed for %s: %s", handle, exc)

    db.execute(
        update(CrawlQuery)
        .where(CrawlQuery.id == query.id)
        .values(
            status="done",
            handles_found=len(handles),
            new_handles=len(new),
            executed_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return len(handles), len(new)


def run_round(db: Session, world_slug: str) -> bool:
    """One full round. Returns False when there was no pending work."""
    pending = db.execute(
        select(CrawlQuery)
        .where(CrawlQuery.world_slug == world_slug, CrawlQuery.status == "pending")
        .order_by(CrawlQuery.round_no, CrawlQuery.id)
        .limit(QUERIES_PER_ROUND)
    ).scalars().all()

    if not pending:
        print(f"no pending queries for {world_slug} — run seed_crawl first")
        return False

    round_no = pending[0].round_no
    print(f"{world_slug} round {round_no}: {len(pending)} pending queries")
    print(f"{'query':<40} {'handles':>8} {'new':>6}")

    for query in pending:
        found, new = run_query(db, query)
        print(f"{query.query_text[:40]:<40} {found:>8} {new:>6}")

    n_described = describe_posts()
    print(f"described {n_described} posts")

    next_round = (
        db.execute(
            select(func.max(CrawlQuery.round_no)).where(CrawlQuery.world_slug == world_slug)
        ).scalar()
        or round_no
    ) + 1
    inserted = query_gen.generate(db, world_slug, next_round)
    print(f"generated {len(inserted)} queries for round {next_round}")
    return True

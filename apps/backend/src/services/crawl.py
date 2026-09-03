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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.db.models import Account, CrawlQuery
from src.db.session import SessionLocal
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
ACCOUNTS_PER_QUERY = 20  # was 5 (a labeled testing cap)


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


def _ingest_one(handle: str, world_slug: str, query_id: UUID) -> float:
    """Ingests one handle on its own Session — a Session isn't thread-safe to
    share, so each worker opens, uses, and closes its own, same pattern as
    enrich.py's per-thread sessions. Failures are logged and swallowed here
    (not re-raised) so one bad handle can't take down the others in the pool.
    Returns elapsed seconds so run_query can print a live per-account rate —
    the pool being "20 at once" is invisible from the log otherwise, since a
    fast/no-op handle (nothing new to download) and a slow one look identical
    without a completion timestamp attached."""
    start = time.monotonic()
    db = SessionLocal()
    try:
        ingest_account(db, handle, POSTS_PER_ACCOUNT, discovered_by_world=world_slug, discovered_by_query_id=query_id)
    except Exception as exc:
        logger.warning("ingest failed for %s: %s", handle, exc)
    finally:
        db.close()
    return time.monotonic() - start


def run_query(db: Session, query: CrawlQuery) -> tuple[int, int]:
    """Search one query, ingest every handle it found, mark it done.

    Committed per query so a crash costs one query, not the whole round.
    Returns (handles_found, new_handles) — written even when both are zero,
    because a zero row is the strongest signal the generator gets.

    Ingest fans out across ACCOUNTS_PER_QUERY handles at once — each is an
    independent account with its own Session (see _ingest_one), so there's no
    shared mutable state between them beyond Postgres itself, which already
    handles concurrent inserts to different rows via on_conflict_do_nothing.
    """
    handles = search_handles(query.query_text)
    known = _known_handles(db, handles)  # sampled before ingest, which would make them all known
    new = [h for h in handles if h not in known]

    batch = handles[:ACCOUNTS_PER_QUERY]
    round_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=ACCOUNTS_PER_QUERY) as pool:
        futures = {pool.submit(_ingest_one, h, query.world_slug, query.id): h for h in batch}
        for i, future in enumerate(as_completed(futures), start=1):
            handle = futures[future]
            elapsed = future.result()
            print(f"  [{i}/{len(batch)}] {handle} done in {elapsed:.1f}s "
                  f"(query elapsed {time.monotonic() - round_start:.1f}s)")

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
        print(f"no pending queries for {world_slug} — insert round-0 rows into crawl_queries first")
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

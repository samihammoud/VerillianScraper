"""The crawl round loop: queries -> handles -> ingest -> VLM -> next queries.

Owns the loop and nothing else. Every step it drives already exists and is
reused unchanged (ingest_account, describe_posts, query_gen.generate); this
module only sequences them and keeps the ledger honest.

There is no round status column and no state machine. Every step is resumable
from data the pipeline already writes — crawl_queries.status committed per
query, on_conflict_do_nothing on (account_id, external_id) for ingest, and
`visual_description IS NULL AND visual_attempts < 3` for the VLM. Kill it
mid-round and rerun: it continues.

A round searches ALL of its queries before ingesting ANY of their handles, so
one shared worker pool can run flat out across the round's whole handle list.
Per-query pools (the earlier shape) idled up to 19 of 20 workers waiting on a
single slow account before the next query could even start searching.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from types import SimpleNamespace
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
QUERIES_PER_ROUND = 10  # matches query_gen.QUERIES_PER_ROUND; also the round-0 seed count
ACCOUNTS_PER_QUERY = 20  # was 5 (a labeled testing cap) — how many handles one query contributes
INGEST_WORKERS = 20  # concurrent account ingests, now round-wide rather than per-query


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
    Returns elapsed seconds so run_round can print a live per-account rate —
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


def _search_all(db: Session, pending: list[CrawlQuery]) -> dict[str, UUID]:
    """Search every query in the round up front and write its ledger counts.

    Returns handle -> owning query_id, each handle assigned to the FIRST query
    that found it, so a handle two queries both return is ingested once.

    The counts are the whole reason searching is separated from ingesting:
    `new_handles` has to mean "new when this query ran", so `known` is the DB
    plus everything earlier queries in this round already claimed — otherwise
    two queries returning the same handle would each bill it as new.

    status stays "pending" here. It flips to "done" only once every handle the
    query owns has been ingested (see run_round), which is what keeps a crash
    costing the unfinished queries rather than the whole round.
    """
    owner: dict[str, UUID] = {}
    seen: set[str] = set()

    print(f"{'query':<40} {'handles':>8} {'new':>6}")
    for query in pending:
        handles = search_handles(query.query_text)
        known = _known_handles(db, handles) | seen
        new = [h for h in handles if h not in known]
        seen.update(handles)

        for handle in handles[:ACCOUNTS_PER_QUERY]:
            owner.setdefault(handle, query.id)

        db.execute(
            update(CrawlQuery)
            .where(CrawlQuery.id == query.id)
            .values(
                handles_found=len(handles),
                new_handles=len(new),
                executed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        print(f"{query.query_text[:40]:<40} {len(handles):>8} {len(new):>6}")

    return owner


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

    owner = _search_all(db, pending)

    # One pool over the round's whole handle list rather than one pool per
    # query: a query's slowest account used to hold 19 idle workers hostage
    # before the next query could start searching (2026-09-08, a single
    # 30-minute query in round 0). Workers now pull the next handle from
    # whatever query as soon as they free up.
    outstanding: dict[UUID, set[str]] = {q.id: set() for q in pending}
    for handle, query_id in owner.items():
        outstanding[query_id].add(handle)

    def _finish(query_id: UUID) -> None:
        db.execute(update(CrawlQuery).where(CrawlQuery.id == query_id).values(status="done"))
        db.commit()

    for query_id, handles in outstanding.items():
        if not handles:  # found nothing, or only handles an earlier query already owns
            _finish(query_id)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=INGEST_WORKERS) as pool:
        futures = {pool.submit(_ingest_one, h, world_slug, q): h for h, q in owner.items()}
        for i, future in enumerate(as_completed(futures), start=1):
            handle = futures[future]
            elapsed = future.result()
            query_id = owner[handle]
            outstanding[query_id].discard(handle)
            if not outstanding[query_id]:
                _finish(query_id)
            print(f"  [{i}/{len(owner)}] {handle} done in {elapsed:.1f}s "
                  f"(round elapsed {time.monotonic() - started:.1f}s)")

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


def _self_check() -> None:
    """Exercises the round scheduler's bookkeeping with no DB and no network:
    handle dedupe across queries, what `new_handles` counts, and which queries
    can be marked done before any ingest runs."""
    class FakeDB:
        """Records the ledger UPDATEs instead of executing them."""
        def __init__(self, known=()):
            self.known, self.updates = set(known), []

        def execute(self, stmt):
            compiled = str(stmt)
            if compiled.startswith("UPDATE"):
                self.updates.append(stmt.compile().params)
                return None
            return self  # the _known_handles SELECT

        def scalars(self):
            return self

        def all(self):
            return list(self.known)

        def commit(self):
            pass

    results = {
        "alpha": ["a", "b", "c"],
        "beta": ["c", "d"],      # c already owned by alpha
        "gamma": [],             # dead vein
        "delta": ["a"],          # entirely a duplicate
    }
    queries = [SimpleNamespace(id=name, query_text=name, round_no=1) for name in results]

    # patch this module's own globals: under `python -m` the running module is
    # __main__, so importing it by name would patch a second, unused copy —
    # and the real search_handles would quietly spend RapidAPI calls.
    original = globals()["search_handles"]
    globals()["search_handles"] = lambda kw: results[kw]
    try:
        db = FakeDB(known={"b"})  # b is already an Account in the DB
        owner = _search_all(db, queries)
    finally:
        globals()["search_handles"] = original

    # every handle ingested once, attributed to the first query that found it
    assert owner == {"a": "alpha", "b": "alpha", "c": "alpha", "d": "beta"}, owner

    counts = {p["crawl_queries_id" if "crawl_queries_id" in p else "id_1"]: p for p in db.updates}
    # alpha: 3 found, b is a known Account -> 2 new
    assert (counts["alpha"]["handles_found"], counts["alpha"]["new_handles"]) == (3, 2), counts["alpha"]
    # beta: c was already claimed by alpha this round, so only d is new
    assert (counts["beta"]["handles_found"], counts["beta"]["new_handles"]) == (2, 1), counts["beta"]
    assert (counts["gamma"]["handles_found"], counts["gamma"]["new_handles"]) == (0, 0), counts["gamma"]
    assert (counts["delta"]["handles_found"], counts["delta"]["new_handles"]) == (1, 0), counts["delta"]
    # nothing is marked done by searching alone — ingest does that
    assert all("status" not in p for p in db.updates)

    # queries owning no handles must not wait on the pool to be finished
    outstanding = {q.id: set() for q in queries}
    for handle, query_id in owner.items():
        outstanding[query_id].add(handle)
    assert {q for q, hs in outstanding.items() if not hs} == {"gamma", "delta"}, outstanding
    assert sum(len(hs) for hs in outstanding.values()) == len(owner)  # no handle counted twice

    print("crawl self-check ok")


if __name__ == "__main__":
    _self_check()

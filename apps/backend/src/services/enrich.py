"""Stage 2 of the bulk pipeline: backfill comments and VLM descriptions for
posts that lack them.

Comments and visual descriptions are deliberately NOT coupled to each other —
different quotas (RapidAPI vs. Gemini), different failure domains, different
concurrency ceilings. Coupled, a RapidAPI 429 would block VLM work that would
otherwise have succeeded. So this is one module with two independent
functions, each with its own claim query and its own bounded worker pool.

No separate rate limiter beyond the concurrency cap: at 5 concurrent workers
and realistic per-call latency, neither service's actual RPM ceiling is
reachable in practice, and the failures actually hit this session (RapidAPI's
monthly quota, Gemini's free-tier daily quota) were total-budget exhaustion,
which no rate limiter — pacing *how fast* requests go out — can prevent
anyway. ThreadPoolExecutor's max_workers is the whole throttle.

No queue table, no broker. The claim queries ARE the coordination mechanism —
"needs enrichment" is already a visible, derivable fact about a post row
(comments/visual_description IS NULL), so there's nothing to duplicate into a
separate table. See the handoff doc's "On queues" section for the full
reasoning; the short version is that a queue's usual jobs (durable handoff,
retry, ordering, visibility) are already covered by the posts table + the
attempts columns + an ORDER BY, and the one job a queue does that this doesn't
(distributing work across *competing* consumers) isn't needed while a single
enrich run is fast enough — enforced by the advisory lock in run_enrich().
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging

from sqlalchemy import select, text, update

from src.db.models import Account, Post
from src.db.session import SessionLocal, engine
from src.services.cover_storage import load_cover
from src.services.mapper import map_comments
from src.services.tiktok_client import get_comment_list
from src.services.vision import MODEL as VISION_MODEL
from src.services.vision import describe_image

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
COMMENT_WORKERS = 5
VISUAL_WORKERS = 5
CLAIM_BATCH_SIZE = 200
ADVISORY_LOCK_KEY = 8801


def _claim_comment_candidates(limit: int = CLAIM_BATCH_SIZE) -> list[dict]:
    db = SessionLocal()
    try:
        stmt = (
            select(Post.id, Post.external_id, Account.handle)
            .join(Account, Account.id == Post.account_id)
            .where(Post.comments.is_(None), Post.comment_attempts < MAX_ATTEMPTS)
            .order_by(Post.id)
            .limit(limit)
        )
        return [{"id": row.id, "external_id": row.external_id, "handle": row.handle} for row in db.execute(stmt)]
    finally:
        db.close()


def _claim_visual_candidates(limit: int = CLAIM_BATCH_SIZE) -> list[dict]:
    db = SessionLocal()
    try:
        stmt = (
            select(Post.id, Post.cover_key)
            .where(
                Post.cover_key.is_not(None),
                Post.visual_description.is_(None),
                Post.visual_attempts < MAX_ATTEMPTS,
            )
            .order_by(Post.id)
            .limit(limit)
        )
        return [{"id": row.id, "cover_key": row.cover_key} for row in db.execute(stmt)]
    finally:
        db.close()


def _enrich_one_comment(candidate: dict) -> None:
    db = SessionLocal()
    try:
        # Attempts incremented BEFORE the call, and committed immediately: a
        # crash mid-call costs one attempt (safe), rather than being stamped
        # only on success (unsafe — a row that reliably kills the worker would
        # retry forever, since it would never leave the claim query's WHERE).
        db.execute(update(Post).where(Post.id == candidate["id"]).values(comment_attempts=Post.comment_attempts + 1))
        db.commit()

        try:
            comments = map_comments(get_comment_list(candidate["handle"], candidate["external_id"]))
        except Exception as exc:
            logger.warning("comment enrich failed for post=%s: %s", candidate["id"], exc)
            return

        db.execute(update(Post).where(Post.id == candidate["id"]).values(comments=comments))
        db.commit()
    finally:
        db.close()


def _enrich_one_visual(candidate: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(update(Post).where(Post.id == candidate["id"]).values(visual_attempts=Post.visual_attempts + 1))
        db.commit()

        try:
            image_bytes = load_cover(candidate["cover_key"])
            description = describe_image(image_bytes)
        except Exception as exc:
            logger.warning("visual enrich failed for post=%s: %s", candidate["id"], exc)
            return

        if description is None:
            return  # attempt already recorded; retried next run up to MAX_ATTEMPTS

        db.execute(
            update(Post)
            .where(Post.id == candidate["id"])
            .values(
                visual_description=description,
                visual_model=VISION_MODEL,
                visual_generated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()


def enrich_comments() -> int:
    """One pass: claim a batch of comment-less posts, fetch, persist. Returns count claimed."""
    candidates = _claim_comment_candidates()
    if not candidates:
        return 0

    with ThreadPoolExecutor(max_workers=COMMENT_WORKERS) as pool:
        list(pool.map(_enrich_one_comment, candidates))
    return len(candidates)


def enrich_visual() -> int:
    """One pass: claim a batch of description-less posts, describe, persist. Returns count claimed."""
    candidates = _claim_visual_candidates()
    if not candidates:
        return 0

    with ThreadPoolExecutor(max_workers=VISUAL_WORKERS) as pool:
        list(pool.map(_enrich_one_visual, candidates))
    return len(candidates)


def run_enrich() -> None:
    """Runs both enrich passes once, guarded by a Postgres advisory lock so two
    overlapping runs (e.g. an overrunning cron) can't double-spend the quota.

    Idempotent by design (claim queries + attempts columns), so double-spend
    here means wasted API calls, not data corruption — the lock just avoids
    paying for that waste. Released automatically when this connection closes.
    """
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar()
        if not got:
            print("another enrich run is active; exiting")
            return

        try:
            n_comments = enrich_comments()
            print(f"comments: {n_comments} posts processed")

            n_visual = enrich_visual()
            print(f"visual: {n_visual} posts processed")
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()

"""Stage 2 of the bulk pipeline: backfill comments for posts that lack them.

The visual half used to live here too (cover image -> VLM prose). It moved to
vision.describe_posts once the VLM's input became the video rather than the
cover frame, and was deleted rather than left in place: both claim queries key
off `visual_description IS NULL`, so keeping both would have had two commands
silently racing to spend Gemini quota on the same rows — one of them on the
strictly weaker signal.

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
import logging

from sqlalchemy import select, text, update

from src.db.models import Account, Post
from src.db.session import SessionLocal, engine
from src.services.mapper import map_comments
from src.services.tiktok_client import get_comment_list

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
COMMENT_WORKERS = 5
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


def enrich_comments() -> int:
    """One pass: claim a batch of comment-less posts, fetch, persist. Returns count claimed."""
    candidates = _claim_comment_candidates()
    if not candidates:
        return 0

    with ThreadPoolExecutor(max_workers=COMMENT_WORKERS) as pool:
        list(pool.map(_enrich_one_comment, candidates))
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
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()

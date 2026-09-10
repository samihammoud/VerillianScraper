"""One-off backfill: re-download video bytes for posts that still lack
vlm_json but no longer have a file on disk (the stored media_urls.play is a
signed TikTok CDN link that expires within hours of ingest, so it can't be
reused later — see ingest.py's _fetch_video docstring).

Re-lists each affected account via get_user_videos to get a fresh signed URL,
matches the response back to existing post rows by external_id, and downloads
only what's still missing. Does not touch crawl_queries or re-run search —
the account handles are already known from the posts table.

Scoping is by `posts.discovered_by_world`, NOT by topology.world_posts the way
redescribe_world.py does it. That looks like an inconsistency and isn't: a post
only reaches world_posts once routing has embedded its visual_description, and
a post with no video has no description to embed — so the posts this script
exists to rescue are exactly the ones routing cannot have placed yet. Scoping
on the routing result would match nothing. discovered_by_world is stamped at
ingest and is available immediately.

Usage: python -m src.scripts.backfill_videos [world-slug]
   or: make backfill-videos WORLD=discount-shopping
"""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from sqlalchemy import select, text

from src.db.models import Account, Post
from src.db.session import SessionLocal, engine
from src.scripts.run_crawl import ADVISORY_LOCK_KEY as CRAWL_LOCK_KEY
from src.services.ingest import _fetch_video
from src.services.mapper import map_account_and_posts
from src.services.tiktok_client import get_user_videos
from src.services.video_storage import list_non_described_ids
from src.services.vision import describe_posts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FETCH_COUNT = 100  # generous single call, no pagination — matches ingest_account's approach
WORKERS = 5  # accounts in parallel; deliberately low — the link saturates around 20 (see crawl.INGEST_WORKERS)
MAX_ATTEMPTS = 3  # mirrors vision.MAX_ATTEMPTS — a post the VLM already gave up on won't be claimed, so fetching its video is waste


def _scope(stmt, world: str | None):
    """Undescribed, still claimable, optionally one world's crawl."""
    stmt = stmt.where(Post.vlm_json.is_(None), Post.visual_attempts < MAX_ATTEMPTS)
    return stmt.where(Post.discovered_by_world == world) if world else stmt


def _accounts_needing_backfill(world: str | None = None) -> list[str]:
    db = SessionLocal()
    try:
        stmt = _scope(
            select(Account.handle).join(Post, Post.account_id == Account.id), world
        ).distinct()
        return list(db.execute(stmt).scalars().all())
    finally:
        db.close()


def _backfill_account(handle: str, available: set, world: str | None = None) -> int:
    db = SessionLocal()
    try:
        missing = db.execute(
            _scope(
                select(Post.id, Post.external_id)
                .join(Account, Account.id == Post.account_id)
                .where(Account.handle == handle),
                world,
            )
        ).all()
        missing = [(pid, ext_id) for pid, ext_id in missing if pid not in available]
        if not missing:
            return 0

        raw = get_user_videos(handle, count=FETCH_COUNT)
        _, posts_data = map_account_and_posts(raw)
        fresh_by_external_id = {p["external_id"]: p for p in posts_data}

        n = 0
        for post_id, external_id in missing:
            fresh = fresh_by_external_id.get(external_id)
            if fresh is None:
                logger.warning("post=%s (external_id=%s) no longer in %s's video list — skipping", post_id, external_id, handle)
                continue
            if _fetch_video({"id": post_id, "media_urls": fresh["media_urls"]}):
                n += 1
        return n
    except Exception as exc:
        logger.warning("backfill failed for account=%s: %s", handle, exc)
        return 0
    finally:
        db.close()


def backfill_videos(world: str | None = None) -> int:
    handles = _accounts_needing_backfill(world)
    print(f"{len(handles)} accounts have posts missing vlm_json" + (f" in world={world}" if world else ""))

    # One GCS listing for the whole run, shared across workers — "already has a
    # video" is a prefix listing now, not a per-post stat().
    available = list_non_described_ids()

    total = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        work = partial(_backfill_account, available=available, world=world)
        for handle, n in zip(handles, pool.map(work, handles)):
            total += n
            print(f"{handle}: {n} videos re-downloaded")

    print(f"backfill done: {total} videos re-downloaded")
    return total


if __name__ == "__main__":
    world_arg = sys.argv[1] if len(sys.argv) > 1 else None

    # Same advisory lock run_crawl takes: a crawl's own ingest and VLM pass
    # would otherwise race this one for the same posts (double-claiming in
    # describe_posts) and, worse, for the same saturated uplink — concurrent
    # transfers are what produced the ~15% loss rate this script exists to
    # repair. Better to refuse than to make the problem worse.
    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": CRAWL_LOCK_KEY}).scalar():
            print("a crawl is running; wait for it to finish before backfilling")
            sys.exit(1)
        try:
            backfill_videos(world_arg)

            # Loop until a pass claims nothing: describe_posts() handles one
            # batch worth at a time, and a backfill can restore far more videos
            # than that.
            described_total = 0
            while True:
                n = describe_posts()
                described_total += n
                print(f"describe_posts pass: {n} claimed (total {described_total})")
                if n == 0:
                    break
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CRAWL_LOCK_KEY})
            conn.commit()

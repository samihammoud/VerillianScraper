"""One-off backfill: re-download video bytes for posts that still lack
vlm_json but no longer have a file on disk (the stored media_urls.play is a
signed TikTok CDN link that expires within hours of ingest, so it can't be
reused later — see ingest.py's _fetch_video docstring).

Re-lists each affected account via get_user_videos to get a fresh signed URL,
matches the response back to existing post rows by external_id, and downloads
only what's still missing. Does not touch crawl_queries or re-run search —
the account handles are already known from the posts table.

Usage: python -m src.scripts.backfill_videos
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from src.db.models import Account, Post
from src.db.session import SessionLocal
from src.services.ingest import _fetch_video
from src.services.mapper import map_account_and_posts
from src.services.tiktok_client import get_user_videos
from src.services.video_storage import list_non_described_ids
from src.services.vision import describe_posts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FETCH_COUNT = 100  # generous single call, no pagination — matches ingest_account's approach
WORKERS = 5


def _accounts_needing_backfill() -> list[str]:
    db = SessionLocal()
    try:
        stmt = (
            select(Account.handle)
            .join(Post, Post.account_id == Account.id)
            .where(Post.vlm_json.is_(None))
            .distinct()
        )
        return list(db.execute(stmt).scalars().all())
    finally:
        db.close()


def _backfill_account(handle: str, available: set) -> int:
    db = SessionLocal()
    try:
        missing = db.execute(
            select(Post.id, Post.external_id)
            .join(Account, Account.id == Post.account_id)
            .where(Account.handle == handle, Post.vlm_json.is_(None))
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


def backfill_videos() -> int:
    handles = _accounts_needing_backfill()
    print(f"{len(handles)} accounts have posts missing vlm_json")

    # One GCS listing for the whole run, shared across workers — "already has a
    # video" is a prefix listing now, not a per-post stat().
    available = list_non_described_ids()

    total = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for handle, n in zip(handles, pool.map(_backfill_account, handles, [available] * len(handles))):
            total += n
            print(f"{handle}: {n} videos re-downloaded")

    print(f"backfill done: {total} videos re-downloaded")
    return total


if __name__ == "__main__":
    backfill_videos()

    described_total = 0
    while True:
        n = describe_posts()
        described_total += n
        print(f"describe_posts pass: {n} claimed (total {described_total})")
        if n == 0:
            break

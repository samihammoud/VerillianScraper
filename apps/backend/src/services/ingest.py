"""Ingests one account end to end: scrape -> map -> comments -> visual descriptions -> persist.

Shared by GET /accounts/{handle}/videos, POST /accounts/{handle}/ingest-and-route, and
src/scripts/run_smoke_test.py — the ingest body used to live inside the route handler,
which meant it couldn't run without a server and couldn't be reused. It lives here once now.

Thumbnails are described here, synchronously with the scrape, not later in routing:
TikTok's cover URLs are signed and expire within hours, so describing them seconds
after the scrape (rather than whenever a routing batch job happens to run) removes
that expiry risk entirely.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from src.services.mapper import map_account_and_posts, map_comments
from src.services.persistence import get_existing_post_external_ids, store_account_and_posts
from src.services.tiktok_client import get_comment_list, get_user_videos
from src.services.vision import MODEL as VISION_MODEL
from src.services.vision import describe_thumbnail

logger = logging.getLogger(__name__)

COMMENT_FETCH_WORKERS = 5
THUMBNAIL_FETCH_WORKERS = 5


def _fetch_comments(handle: str, video_id: str) -> dict | None:
    try:
        return map_comments(get_comment_list(handle, video_id))
    except httpx.HTTPStatusError as exc:
        logger.warning("comment fetch failed for video_id=%s: %s", video_id, exc)
        return None


def ingest_account(db: Session, handle: str, count: int) -> UUID | None:
    """Fetch an account's videos, their comments, and their thumbnail descriptions;
    persist all of it. Returns the account id."""
    raw = get_user_videos(handle, count=count)
    account_data, posts_data = map_account_and_posts(raw)

    if not account_data:
        return None

    existing_ids = get_existing_post_external_ids(db, account_data["platform"], account_data["external_id"])
    new_posts = [post for post in posts_data if post["external_id"] not in existing_ids]

    if new_posts:
        with ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS) as pool:
            futures = {pool.submit(_fetch_comments, handle, post["external_id"]): post for post in new_posts}
            for future in as_completed(futures):
                futures[future]["comments"] = future.result()

        thumbnail_posts = [post for post in new_posts if post.get("thumbnail_url")]
        with ThreadPoolExecutor(max_workers=THUMBNAIL_FETCH_WORKERS) as pool:
            futures = {pool.submit(describe_thumbnail, post["thumbnail_url"]): post for post in thumbnail_posts}
            for future in as_completed(futures):
                post = futures[future]
                post["visual_description"] = future.result()
                post["visual_model"] = VISION_MODEL
                post["visual_generated_at"] = datetime.now(timezone.utc)
                # posts without a thumbnail_url stay all-None — nothing to describe,
                # distinct from a post whose describe attempt failed.

    return store_account_and_posts(db, account_data, posts_data)

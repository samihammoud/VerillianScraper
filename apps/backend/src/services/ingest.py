"""Stage 1 of the bulk pipeline: video list + cover bytes. Nothing else.

Comments and visual descriptions are backfilled separately by enrich.py (stage
2), on their own schedule and their own retry budget. This split exists
because nothing was durable until everything was: the old coupled ingest built
each post as an in-memory dict, stuffed comments and a VLM description into
it, and only then called a single insert. A crash during the VLM pass lost the
entire account's scrape — including the video-list call already paid for.

Cover bytes are downloaded here, synchronously with the scrape, because the
*signed URL* is what expires, not the underlying frame. Downloading it now
converts a deadline on the whole pipeline into a deadline on one cheap CDN GET
that doesn't touch the RapidAPI quota.
"""

import logging
import uuid
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from src.services.cover_storage import store_cover
from src.services.mapper import map_account_and_posts
from src.services.persistence import get_existing_post_external_ids, store_account_and_posts
from src.services.tiktok_client import get_user_videos

logger = logging.getLogger(__name__)

COVER_FETCH_TIMEOUT = 15.0
COVER_FETCH_ATTEMPTS = 2  # a cheap CDN GET, not RapidAPI/Gemini quota — light retry is free
MAX_PAGES = 50  # safety cap against a pagination loop that never terminates


def _fetch_cover(post: dict) -> None:
    """Downloads + stores the cover image. Always leaves cover_status terminal.

    Never rolls back the post over a missing cover: per-account rollback kills
    every good post over one bad cover; per-post rollback silently drops rows
    and hides the drop rate, and contradicts the existing rule that a post
    with no visual signal must still route.
    """
    thumbnail_url = post.get("thumbnail_url")
    if not thumbnail_url:
        post["cover_status"] = "missing"
        return

    for attempt in range(COVER_FETCH_ATTEMPTS):
        try:
            response = httpx.get(thumbnail_url, timeout=COVER_FETCH_TIMEOUT)
            response.raise_for_status()
            post["cover_key"] = store_cover(post["id"], response.content)
            post["cover_status"] = "stored"
            return
        except Exception as exc:
            if attempt == COVER_FETCH_ATTEMPTS - 1:
                logger.warning("cover fetch failed for post=%s: %s", post["id"], exc)

    post["cover_status"] = "missing"


def ingest_account(db: Session, handle: str, count: int) -> UUID | None:
    """Fetch an account's video list (paginated) and cover bytes only.

    No comments, no VLM — see enrich.py. Returns the account id.
    """
    account_data: dict = {}
    posts_data: list[dict] = []
    cursor = "0"

    for _ in range(MAX_PAGES):
        if len(posts_data) >= count:
            break

        raw = get_user_videos(handle, count=count - len(posts_data), cursor=cursor)
        page_account, page_posts = map_account_and_posts(raw)
        if not page_account:
            break

        account_data = page_account
        posts_data.extend(page_posts)

        data = raw.get("data", {})
        if not data.get("hasMore") or not page_posts:
            break
        cursor = data.get("cursor", cursor)

    if not account_data:
        return None

    # Every post dict needs an id up front (not just new ones) so the bulk insert
    # sees a uniform key set across all rows — already-existing posts' ids are
    # simply discarded by on_conflict_do_nothing.
    for post in posts_data:
        post["id"] = uuid.uuid4()

    existing_ids = get_existing_post_external_ids(db, account_data["platform"], account_data["external_id"])
    new_posts = [post for post in posts_data if post["external_id"] not in existing_ids]

    for post in new_posts:
        _fetch_cover(post)

    return store_account_and_posts(db, account_data, posts_data)

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
from src.services.video_storage import store_video

logger = logging.getLogger(__name__)

COVER_FETCH_TIMEOUT = 15.0
COVER_FETCH_ATTEMPTS = 2  # a cheap CDN GET, not RapidAPI/Gemini quota — light retry is free
VIDEO_FETCH_TIMEOUT = 120.0

# TikTok's CDN rejects bare requests for playAddr. The referer/UA pair is a
# calibration knob, not a constant — if downloads start 403ing, this is the
# first thing to re-check against a live browser request.
_VIDEO_HEADERS = {
    "Referer": "https://www.tiktok.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


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


def _fetch_video(post: dict) -> None:
    """Downloads the video bytes to disk. Same contract as _fetch_cover: log on
    failure, never raise, never roll back the post.

    Downloaded at ingest rather than at VLM time because the play URL is signed
    and short-lived — by the time a batch runs, it's dead. A post with no video
    file is simply skipped by the VLM claim query.

    Photo-mode/slideshow posts (images + a music track, no real video) still
    populate `play`, but it resolves to the song CDN (content-type audio/*).
    Storing that as a .mp4 would force Gemini to parse audio as video, which
    fails deterministically on every attempt and burns visual_attempts for a
    post that structurally can never succeed — so it's rejected here, upstream
    of both the disk write and the VLM claim query.
    """
    url = (post.get("media_urls") or {}).get("play")
    if not url:
        return

    try:
        response = httpx.get(url, headers=_VIDEO_HEADERS, timeout=VIDEO_FETCH_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("video/"):
            logger.warning("play url for post=%s is not a video (content-type=%s) — skipping",
                           post["id"], response.headers.get("content-type"))
            return
        store_video(post["id"], response.content)
    except Exception as exc:
        logger.warning("video fetch failed for post=%s: %s", post["id"], exc)


def ingest_account(db: Session, handle: str, count: int, discovered_by_world: str | None = None) -> UUID | None:
    """Fetch an account's video list (single call, no pagination) and cover bytes only.

    No comments, no VLM — see enrich.py. Returns the account id.
    """
    raw = get_user_videos(handle, count=count)
    account_data, posts_data = map_account_and_posts(raw)
    if not account_data:
        return None

    # Every post dict needs an id up front (not just new ones) so the bulk insert
    # sees a uniform key set across all rows — already-existing posts' ids are
    # simply discarded by on_conflict_do_nothing.
    for post in posts_data:
        post["id"] = uuid.uuid4()
        post["discovered_by_world"] = discovered_by_world

    existing_ids = get_existing_post_external_ids(db, account_data["platform"], account_data["external_id"])
    new_posts = [post for post in posts_data if post["external_id"] not in existing_ids]

    for post in new_posts:
        _fetch_cover(post)
        _fetch_video(post)

    return store_account_and_posts(db, account_data, posts_data)

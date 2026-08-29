"""Describes a post's video with a VLM, for the visual modality routing signal.

Takes an already-downloaded file (see video_storage.py), not a URL: the play
URL is signed and short-lived, and retrying a Gemini failure must never
re-fetch from TikTok. Output is structured JSON (vision_schema.py) at
temperature 0; the prose rendering routing actually embeds is derived from it
by flatten_for_blob.

The cover-image prompt this module used to hold is gone — a still frame was
always a stand-in for the video, and the video is now downloaded at ingest.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from google import genai
from google.genai import types
from sqlalchemy import select, update

from src.config.settings import settings
from src.db.models import Post
from src.db.session import SessionLocal
from src.services.video_storage import clear_videos, video_path
from src.services.vision_schema import GENERATION_CONFIG, flatten_for_blob

logger = logging.getLogger(__name__)

VIDEO_MODEL = "gemini-3.7-flash"
MAX_ATTEMPTS = 3  # initial attempt + 2 retries
VIDEO_WORKERS = 5
CLAIM_BATCH_SIZE = 200

_client = genai.Client(api_key=settings.gemini_api_key)

# describe_posts is the video equivalent of the old cover-image pass: same claim
# query shape, same attempts-before-the-call discipline, same bounded pool. It
# writes vlm_json (the raw structured object) AND visual_description (a prose
# rendering of it), because routing/blob.py only ever reads the prose and must
# not learn about the JSON.
#
# ponytail: synchronous one-call-per-post, per the handoff's "build the
# synchronous version first". The Batch API swap (upload -> JSONL -> submit ->
# poll -> collect) replaces this function's body only; the signature is the
# same either way, and the schema will change once real output has been read.


def _claim_video_candidates(limit: int = CLAIM_BATCH_SIZE) -> list[dict]:
    """Posts still lacking a description, that actually have a video on disk.

    Disk is the source of truth for "has a video" — no column tracks it, since
    the path is derivable from the post id.
    """
    db = SessionLocal()
    try:
        stmt = (
            select(Post.id)
            .where(Post.visual_description.is_(None), Post.visual_attempts < MAX_ATTEMPTS)
            .order_by(Post.id)
            .limit(limit * 4)  # over-select: most rows are filtered out by the disk check
        )
        return [{"id": row.id} for row in db.execute(stmt) if video_path(row.id).exists()][:limit]
    finally:
        db.close()


def _describe_one_video(candidate: dict) -> None:
    post_id = candidate["id"]
    path = video_path(post_id)

    db = SessionLocal()
    try:
        # Incremented before the call, committed immediately — a row that
        # reliably kills the worker must not retry forever. Same reasoning as
        # enrich.py.
        db.execute(update(Post).where(Post.id == post_id).values(visual_attempts=Post.visual_attempts + 1))
        db.commit()

        try:
            response = _client.models.generate_content(
                model=VIDEO_MODEL,
                contents=[types.Part.from_bytes(data=path.read_bytes(), mime_type="video/mp4")],
                config=types.GenerateContentConfig(**GENERATION_CONFIG),
            )
            obj = json.loads(response.text)
        except Exception as exc:
            logger.warning("video describe failed for post=%s: %s", post_id, exc)
            return

        db.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(
                vlm_json=obj,
                visual_description=flatten_for_blob(obj),
                visual_model=VIDEO_MODEL,
                visual_generated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        path.unlink(missing_ok=True)
    finally:
        db.close()


def describe_posts() -> int:
    """One pass: claim posts with a downloaded video, describe, persist.

    Returns the number claimed. Sweeps out/videos/ on the way out so orphans
    from a dead run don't accumulate — deliberately at exit, not entry, since
    at entry those files are exactly the work still to do.
    """
    candidates = _claim_video_candidates()
    if not candidates:
        return 0

    with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
        list(pool.map(_describe_one_video, candidates))

    clear_videos()
    return len(candidates)

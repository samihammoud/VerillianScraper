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
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID

from google import genai
from google.genai import types
from sqlalchemy import select, update

from src.config.settings import settings
from src.db.models import Post
from src.db.session import SessionLocal
from src.services.video_storage import delete_video, video_path
from src.services.vision_schema import GENERATION_CONFIG, flatten_for_blob

logger = logging.getLogger(__name__)

VIDEO_MODEL = "gemini-3.7-flash"
MAX_ATTEMPTS = 3  # initial attempt + 2 retries
VIDEO_WORKERS = 5  # parallel upload workers; the batch call itself is one request
CLAIM_BATCH_SIZE = 2500  # bounded by the Files API's 20GB live-storage quota, not by ingest volume: every claimed
# video is uploaded and sits in storage simultaneously until the whole batch job is collected, so this is really
# "how many videos can be in flight at once" — measured avg video size in this corpus is ~6.9MB (not the ~2MB
# originally assumed), so 2500 * ~6.9MB ~= 17GB, leaving headroom under the 20GB cap. Safe to size conservatively
# now: _finalize_videos only deletes on success/exhaustion, so an undersized claim just waits for the next pass
# instead of being permanently lost.
FILE_ACTIVE_POLL_SEC = 5
FILE_ACTIVE_TIMEOUT_SEC = 300
BATCH_POLL_SEC = 30

_client = genai.Client(api_key=settings.gemini_api_key)

# describe_posts is the video equivalent of the old cover-image pass: same claim
# query shape, same attempts-before-the-call discipline. It writes vlm_json
# (the raw structured object) AND visual_description (a prose rendering of
# it), because routing/blob.py only ever reads the prose and must not learn
# about the JSON.
#
# Batch flow: each claimed video is uploaded via the Files API (which needs to
# reach ACTIVE state before it's referenceable), then all requests go into one
# batches.create call carrying post_id as request metadata for correlation on
# the way back out. inlined_requests/inlined_responses (plain Python lists)
# stand in for the upload->JSONL->submit->poll->collect flow the SDK would
# otherwise need hand-rolled: the SDK serializes/parses that JSONL internally
# when given a list of InlinedRequest, so there's nothing to hand-roll here.
#
# Every uploaded file is explicitly deleted (_delete_uploaded_file) right after
# its result is collected, rather than left to the Files API's 48h auto-expiry
# — at multi-thousand-video crawl volume, uploads can otherwise outpace that
# clock and the account's 20GB Files API quota fills up mid-run.

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


def _mark_attempted(post_ids: list[UUID]) -> None:
    # Incremented before the batch call, committed immediately — a row that
    # reliably kills the batch must not retry forever. Same reasoning as
    # enrich.py, just applied to the whole claimed set at once.
    db = SessionLocal()
    try:
        db.execute(update(Post).where(Post.id.in_(post_ids)).values(visual_attempts=Post.visual_attempts + 1))
        db.commit()
    finally:
        db.close()


def _delete_uploaded_file(file_name: str) -> None:
    """Best-effort cleanup of a Gemini Files API object. Failures are logged,
    not raised — the 48h auto-expiry is the backstop, this is just what keeps
    a multi-day, multi-thousand-video crawl from stacking uploads faster than
    that backstop clears them and tripping the 20GB Files API quota."""
    try:
        _client.files.delete(name=file_name)
    except Exception as exc:
        logger.warning("failed to delete uploaded file=%s: %s", file_name, exc)


def _upload_video(candidate: dict) -> types.InlinedRequest | None:
    post_id = candidate["id"]
    path = video_path(post_id)
    file = None
    try:
        file = _client.files.upload(file=path, config={"mime_type": "video/mp4"})
        deadline = time.monotonic() + FILE_ACTIVE_TIMEOUT_SEC
        while file.state == types.FileState.PROCESSING:
            if time.monotonic() > deadline:
                raise TimeoutError("file stuck in PROCESSING")
            time.sleep(FILE_ACTIVE_POLL_SEC)
            file = _client.files.get(name=file.name)
        if file.state != types.FileState.ACTIVE:
            raise RuntimeError(f"file upload ended in state={file.state}")
    except Exception as exc:
        logger.warning("video upload failed for post=%s: %s", post_id, exc)
        if file is not None:
            # Uploaded but never became usable (timed out / FAILED state) — it
            # still counts against the Files API quota until deleted or expired.
            _delete_uploaded_file(file.name)
        return None

    return types.InlinedRequest(
        model=VIDEO_MODEL,
        contents=[types.Part.from_uri(file_uri=file.uri, mime_type="video/mp4")],
        config=types.GenerateContentConfig(**GENERATION_CONFIG),
        metadata={"post_id": str(post_id), "file_name": file.name},
    )


def _collect_result(request: types.InlinedRequest, item: types.InlinedResponse) -> None:
    post_id = request.metadata["post_id"]
    if item.error or item.response is None:
        logger.warning("video describe failed for post=%s: %s", post_id, item.error)
        return
    try:
        obj = json.loads(item.response.text)
    except Exception as exc:
        logger.warning("video describe unparseable for post=%s: %s", post_id, exc)
        return

    db = SessionLocal()
    try:
        db.execute(
            update(Post)
            .where(Post.id == UUID(post_id))
            .values(
                vlm_json=obj,
                visual_description=flatten_for_blob(obj),
                visual_model=VIDEO_MODEL,
                visual_generated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()


def _finalize_videos(candidate_ids: list[UUID]) -> None:
    """Deletes the local video only for posts that reached a terminal state
    this pass — succeeded (has vlm_json), or exhausted MAX_ATTEMPTS. A post
    that failed but still has retries left keeps its video on disk so the
    next describe_posts() call can retry it directly, without a re-ingest.
    This is what makes a partial-failure pass (quota exhaustion, upload
    timeouts) recoverable instead of a silent, permanent loss."""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Post.id, Post.vlm_json, Post.visual_attempts).where(Post.id.in_(candidate_ids))
        ).all()
        for post_id, vlm_json, attempts in rows:
            if vlm_json is not None or attempts >= MAX_ATTEMPTS:
                delete_video(post_id)
    finally:
        db.close()


def describe_posts() -> int:
    """One pass: claim posts with a downloaded video, batch-describe, persist.

    Returns the number claimed. Deletes each claimed post's video individually
    once it's truly done with (see _finalize_videos) — never the whole
    directory at once, so a partial-failure pass doesn't strand the posts it
    didn't get to.
    """
    candidates = _claim_video_candidates()
    if not candidates:
        return 0

    candidate_ids = [c["id"] for c in candidates]
    _mark_attempted(candidate_ids)

    with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
        requests = [r for r in pool.map(_upload_video, candidates) if r is not None]

    if requests:
        job = _client.batches.create(model=VIDEO_MODEL, src=requests)
        while not job.done:
            time.sleep(BATCH_POLL_SEC)
            job = _client.batches.get(name=job.name)

        responses = (job.dest.inlined_responses or []) if job.dest else []
        for request, item in zip(requests, responses):
            _collect_result(request, item)
            _delete_uploaded_file(request.metadata["file_name"])

    _finalize_videos(candidate_ids)
    return len(candidates)

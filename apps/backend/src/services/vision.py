"""Describes a post's video with a VLM, for the visual modality routing signal.

Takes an already-downloaded file (see video_storage.py), not a URL: the play
URL is signed and short-lived, and retrying a Gemini failure must never
re-fetch from TikTok. Every request carries vision_schema.SYSTEM_INSTRUCTION
and RESPONSE_SCHEMA (structured JSON at temperature 0) — both shared and
world-blind, not per-post: describe_posts() claims across every world at
once, and routing hasn't run yet at claim time, so which world a post belongs
to isn't known here. The prose rendering routing actually embeds is derived
from the JSON by flatten_for_blob.

The cover-image prompt this module used to hold is gone — a still frame was
always a stand-in for the video, and the video is now downloaded at ingest.
"""

import json
import logging
import threading
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
from src.services.video_storage import VIDEOS_DIR, delete_video, video_path
from src.services.vision_schema import GENERATION_CONFIG, SYSTEM_INSTRUCTION, flatten_for_blob

logger = logging.getLogger(__name__)

VIDEO_MODEL = "gemini-3.7-flash"
MAX_ATTEMPTS = 3  # initial attempt + 2 retries
VIDEO_WORKERS = 50  # parallel upload workers; the batch call itself is one request.
# Not RPM-bound: uploads hit the Files API, not generateContent/batches.create, and
# Google publishes no per-minute cap for it. Ceiling here is local (bandwidth,
# transient upload errors) — _upload_video has no retry, so a failed upload burns
# one of MAX_ATTEMPTS permanently. Dial back if the next crawl logs upload failures.
CLAIM_BATCH_SIZE = 2500  # bounded by the Files API's 20GB live-storage quota, not by ingest volume: every claimed
# video is uploaded and sits in storage simultaneously until the whole batch job is collected, so this is really
# "how many videos can be in flight at once" — measured avg video size in this corpus is ~6.9MB (not the ~2MB
# originally assumed), so 2500 * ~6.9MB ~= 17GB, leaving headroom under the 20GB cap. Safe to size conservatively
# now: _finalize_videos only deletes on success/exhaustion, so an undersized claim just waits for the next pass
# instead of being permanently lost.
FILE_ACTIVE_POLL_SEC = 5
FILE_ACTIVE_TIMEOUT_SEC = 300
BATCH_POLL_SEC = 30

HTTP_TIMEOUT_SEC = 120  # unset means httpx waits forever on a stalled connection — seen hanging a full upload pass

_client = genai.Client(
    api_key=settings.gemini_api_key,
    http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_SEC * 1000),
)

# Local record of every Gemini file name uploaded this pass — the Files API has
# no cheap "what's mine" query (files.list returns everyone's incident debris
# too, and has been outright down before), so a crash between upload and
# cleanup used to strand files with no way to find them again short of
# listing everything. Workers append here as they upload; a crashed pass
# leaves the file non-empty, and the next describe_posts() call reads it and
# deletes every entry before claiming new work.
UPLOAD_LEDGER = VIDEOS_DIR.parent / "gemini_uploads.txt"
_ledger_lock = threading.Lock()


def _ledger_append(file_name: str) -> None:
    UPLOAD_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock:
        with open(UPLOAD_LEDGER, "a") as f:
            f.write(file_name + "\n")


def _recover_orphaned_uploads() -> None:
    if not UPLOAD_LEDGER.exists():
        return
    names = [line.strip() for line in UPLOAD_LEDGER.read_text().splitlines() if line.strip()]
    if names:
        logger.info("recovering %d file(s) left over from an interrupted pass", len(names))
        for name in names:
            _delete_uploaded_file(name)
    UPLOAD_LEDGER.unlink(missing_ok=True)

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
    the path is derivable from the post id. Filters on vlm_json, not
    visual_description: a handful of posts carry a visual_description from an
    older pre-vlm_json pass (legacy "PRODUCTS: ..." format, visual_model
    "gemini-3.6-flash") with no vlm_json to match — filtering on
    visual_description silently orphaned them from ever being reclaimed.
    """
    db = SessionLocal()
    try:
        stmt = (
            select(Post.id)
            .where(Post.vlm_json.is_(None), Post.visual_attempts < MAX_ATTEMPTS)
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


def _submit_upload(candidate: dict) -> tuple[dict, types.File] | None:
    """Upload only — no poll-to-ACTIVE here. Waiting for a file to finish
    transcoding is decoupled into _await_active, which polls the whole batch
    together in shared rounds instead of tying up one worker per file: the
    transcode happens on Gemini's side regardless of how many local threads
    are spent waiting for it, so blocking a worker on it just caps how many
    files can be in flight at once for no benefit."""
    post_id = candidate["id"]
    try:
        file = _client.files.upload(file=video_path(post_id), config={"mime_type": "video/mp4"})
        _ledger_append(file.name)
        return candidate, file
    except Exception as exc:
        logger.warning("video upload failed for post=%s: %s", post_id, exc)
        return None


def _check_status(item: tuple[dict, types.File]) -> tuple[dict, types.File]:
    candidate, file = item
    return candidate, _client.files.get(name=file.name)


def _await_active(uploads: list[tuple[dict, types.File]]) -> list[tuple[dict, types.File]]:
    """Polls every uploaded file together, round by round, on a shared
    deadline — one round-trip per file per round instead of one blocked
    worker per file for the file's whole transcode time."""
    deadline = time.monotonic() + FILE_ACTIVE_TIMEOUT_SEC
    pending = uploads
    resolved: list[tuple[dict, types.File]] = []

    while pending:
        with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
            checked = list(pool.map(_check_status, pending))

        pending = []
        for candidate, file in checked:
            if file.state == types.FileState.PROCESSING:
                pending.append((candidate, file))
            elif file.state == types.FileState.ACTIVE:
                resolved.append((candidate, file))
            else:
                logger.warning("video upload failed for post=%s: file upload ended in state=%s", candidate["id"], file.state)
                _delete_uploaded_file(file.name)

        if pending and time.monotonic() > deadline:
            for candidate, file in pending:
                logger.warning("video upload failed for post=%s: file stuck in PROCESSING", candidate["id"])
                _delete_uploaded_file(file.name)
            break
        if pending:
            time.sleep(FILE_ACTIVE_POLL_SEC)

    return resolved


def _strip_nul(obj):
    """Postgres text/json columns reject the NUL byte outright, and Gemini
    occasionally transcribes one from OCR garbage in on_screen_text. Stripped
    recursively before persisting so one bad post can't crash the whole
    collection loop (see the incident this fixed: an uncaught NUL byte took
    down a 2500-video collection pass partway through)."""
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, list):
        return [_strip_nul(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _strip_nul(v) for k, v in obj.items()}
    return obj


def _collect_result(item: types.InlinedResponse) -> None:
    # post_id comes from the RESPONSE's own echoed metadata, not from zipping
    # against the request list positionally. Real incident, 2026-09-03: a
    # video correctly identified at scrape time (caption matched the live
    # TikTok post exactly) ended up with vlm_json describing a totally
    # different video — the batch API's response order is not a documented,
    # verified guarantee at production scale (thousands of long-running video
    # requests, vs. the handful of trivial replies a quick correctness check
    # would use), and `zip(requests, responses)` trusted it anyway. Every
    # InlinedRequest/InlinedResponse carries its own `metadata` field
    # specifically so a batch caller doesn't have to trust ordering — this
    # was set on the request and never read back off the response.
    if not item.metadata or "post_id" not in item.metadata:
        logger.warning("video describe response missing metadata, cannot attribute to a post — dropped")
        return
    post_id = item.metadata["post_id"]
    if item.error or item.response is None:
        logger.warning("video describe failed for post=%s: %s", post_id, item.error)
        return
    try:
        obj = _strip_nul(json.loads(item.response.text))
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
    except Exception as exc:
        logger.warning("video describe persist failed for post=%s: %s", post_id, exc)
        db.rollback()
    finally:
        db.close()


def _collect_and_cleanup(item: types.InlinedResponse) -> None:
    """One item's DB write + Gemini file cleanup, threaded — each is an
    independent post row and an independent file delete (a real network
    round-trip), so there's no reason to serialize hundreds/thousands of
    them one at a time the way the upload and status-poll steps already
    aren't. Takes the response alone (see _collect_result) — file_name for
    cleanup also comes from the response's own echoed metadata, not a
    zipped-in request."""
    _collect_result(item)
    if item.metadata and "file_name" in item.metadata:
        _delete_uploaded_file(item.metadata["file_name"])
    else:
        logger.warning("video describe response missing metadata, cannot clean up its uploaded file")


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
    _recover_orphaned_uploads()

    candidates = _claim_video_candidates()
    if not candidates:
        return 0

    candidate_ids = [c["id"] for c in candidates]
    _mark_attempted(candidate_ids)
    print(f"VLM: {len(candidates)} candidates claimed, uploading...")

    with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
        uploads = [r for r in pool.map(_submit_upload, candidates) if r is not None]
    print(f"VLM: {len(uploads)}/{len(candidates)} uploaded, waiting for ACTIVE...")

    active = _await_active(uploads)
    print(f"VLM: {len(active)}/{len(uploads)} active, building batch request...")
    requests = [
        types.InlinedRequest(
            model=VIDEO_MODEL,
            contents=[types.Part.from_uri(file_uri=file.uri, mime_type="video/mp4")],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, **GENERATION_CONFIG),
            metadata={"post_id": str(candidate["id"]), "file_name": file.name},
        )
        for candidate, file in active
    ]

    if requests:
        job = _client.batches.create(model=VIDEO_MODEL, src=requests)
        print(f"VLM: batch submitted ({len(requests)} requests), waiting for Gemini...")
        while not job.done:
            time.sleep(BATCH_POLL_SEC)
            job = _client.batches.get(name=job.name)
        print("VLM: batch done, collecting results...")

        responses = (job.dest.inlined_responses or []) if job.dest else []
        with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
            done = 0
            for _ in pool.map(_collect_and_cleanup, responses):
                done += 1
                if done % 50 == 0 or done == len(responses):
                    print(f"VLM: collected {done}/{len(responses)}")

    _finalize_videos(candidate_ids)
    UPLOAD_LEDGER.unlink(missing_ok=True)  # every entry from this pass is now deleted or terminal
    print(f"VLM: pass complete — {len(candidates)} claimed")
    return len(candidates)


def _self_check() -> None:
    assert _strip_nul("a\x00b") == "ab"
    assert _strip_nul({"on_screen_text": ["ok\x00", "fine"], "n": 3}) == {"on_screen_text": ["ok", "fine"], "n": 3}
    assert _strip_nul([1, "x\x00y", None]) == [1, "xy", None]

    global UPLOAD_LEDGER
    real_ledger = UPLOAD_LEDGER
    UPLOAD_LEDGER = real_ledger.parent / "gemini_uploads.selfcheck.txt"
    try:
        UPLOAD_LEDGER.unlink(missing_ok=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_ledger_append, [f"files/fake{i}" for i in range(20)]))
        recovered = [ln for ln in UPLOAD_LEDGER.read_text().splitlines() if ln]
        assert sorted(recovered) == sorted(f"files/fake{i}" for i in range(20)), "concurrent appends lost a line"
        UPLOAD_LEDGER.unlink()
    finally:
        UPLOAD_LEDGER = real_ledger
    print("vision self-check ok")


if __name__ == "__main__":
    _self_check()

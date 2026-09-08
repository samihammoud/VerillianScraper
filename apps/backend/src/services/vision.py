"""Describes a post's video with a VLM, for the visual modality routing signal.

Bytes live in GCS (see video_storage.py) and are *registered* with the Gemini
Files API by gs:// URI — never uploaded. Registration is a metadata call, so
the 20GB Files API live-storage quota that used to bound how many videos could
be in flight at once no longer applies at all.

Registration reads the bucket as the service account the Gemini API key is
bound to, NOT as this project's own service account — verified 2026-09-04 from
GCS's denial message, which named `ais-gemini-key-...@676164407184` (a Google-
side AI Studio account) as the principal being refused. That account needs
roles/storage.objectViewer on the bucket; granting it to our own service
account does nothing for registration.

Two passes, because "has a video" and "is registered with Gemini" are
different facts with different sources of truth — a GCS listing and a DB
column respectively:

  pass A  gemini_file_name IS NULL + video present in GCS
          -> register_files(uris=[gs://...]), one URI per call
          -> write gemini_file_name immediately, per post

  pass B  gemini_file_name IS NOT NULL + vlm_json IS NULL
          -> confirm each registration is still ACTIVE, batch-describe, persist

Splitting them is what makes the whole thing crash-safe without a ledger file:
registration state is a committed Postgres row, so an interrupted pass resumes
from a claim query instead of from out/gemini_uploads.txt. That ledger, and
_recover_orphaned_uploads with it, is gone.

Every request carries vision_schema.SYSTEM_INSTRUCTION and RESPONSE_SCHEMA
(structured JSON at temperature 0) — both shared and world-blind, not
per-post: describe_posts() claims across every world at once, and routing
hasn't run yet at claim time, so which world a post belongs to isn't known
here. The prose rendering routing actually embeds is derived from the JSON by
flatten_for_blob.
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
from src.services.video_storage import list_non_described_ids, mark_described, video_uri
from src.services.vision_schema import GENERATION_CONFIG, SYSTEM_INSTRUCTION, flatten_for_blob

logger = logging.getLogger(__name__)

VIDEO_MODEL = "gemini-3.7-flash"
MAX_ATTEMPTS = 3  # initial attempt + 2 retries
VIDEO_WORKERS = 50  # parallel workers for register/status calls; the batch call itself is one request.
# These are metadata round-trips now, not byte uploads — cheap and fast per
# call, so there's no bandwidth argument for batching many URIs into one
# register_files() call (and a good correctness argument against it, below).

# Replaces CLAIM_BATCH_SIZE, whose 20GB-Files-quota rationale died with the
# byte uploads. What actually bounds a pass now is the size of the one
# batches.create body carrying every inlined request. 20MB is a deliberately
# conservative stand-in for an API body limit Google doesn't publish for this
# endpoint — a calibration knob, not a constant. Raise it if passes are
# needlessly small; lower it if batches.create starts rejecting bodies.
# Undersizing is cheap: unclaimed posts just wait for the next pass, exactly
# as they did under the old count cap.
BATCH_REQUEST_SIZE_LIMIT = 20 * 1024**2

# Blast radius, not payload size — the real reason a pass is capped by count.
# _bump_attempts fires BEFORE the batch is submitted, so a job that fails
# wholesale burns one of MAX_ATTEMPTS for every post in it at once; and results
# only land when the entire job completes, so an oversized batch means hours
# with nothing persisted. At ~2.3KB/request the size budget alone would allow
# ~9200 posts in one job. 1000 keeps a bad batch cheap and makes progress
# land incrementally — describe_posts() is meant to be called in a loop until
# it returns 0, so a smaller cap costs round trips, never coverage.
MAX_BATCH_POSTS = 10000  # deliberately above the whole romance backlog: one job, not a loop of 1000s.
# The blast-radius argument above still holds and is the reason to put this back
# to 1000 once the one-shot romance pass is done.
REGISTER_BATCH_SIZE = 10000  # cap on one pass's registration work; nothing accumulates, so this is just pacing
REGISTER_ATTEMPTS = 3  # per-call retries for transient socket errors — see _register_one

FILE_ACTIVE_POLL_SEC = 5
FILE_ACTIVE_TIMEOUT_SEC = 300
BATCH_POLL_SEC = 30

HTTP_TIMEOUT_SEC = 120  # unset means httpx waits forever on a stalled connection — seen hanging a full upload pass

_client = genai.Client(
    api_key=settings.gemini_api_key,
    http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_SEC * 1000),
)


# ---------------------------------------------------------------- pass A


def _claim_unregistered(available: set[UUID], limit: int = REGISTER_BATCH_SIZE) -> list[UUID]:
    """Posts whose video is in GCS but which have no Gemini registration yet.

    `available` is the GCS listing, passed in rather than re-listed so one
    describe_posts() call makes exactly one list_blobs pass.
    """
    db = SessionLocal()
    try:
        stmt = select(Post.id).where(
            Post.gemini_file_name.is_(None),
            Post.vlm_json.is_(None),
            Post.visual_attempts < MAX_ATTEMPTS,
        )
        return [row.id for row in db.execute(stmt) if row.id in available][:limit]
    finally:
        db.close()


def _set_file_name(post_id: UUID, file_name: str | None) -> None:
    db = SessionLocal()
    try:
        db.execute(update(Post).where(Post.id == post_id).values(gemini_file_name=file_name))
        db.commit()
    except Exception as exc:
        logger.warning("failed to persist gemini_file_name for post=%s: %s", post_id, exc)
        db.rollback()
    finally:
        db.close()


def _register_one(post_id: UUID) -> bool:
    """One URI per call, deliberately — not a bulk register_files(uris=[...]).

    The response's `files` list order relative to the input `uris` list is not
    a documented guarantee, and this codebase already lost a day to trusting
    an unverified order guarantee on this same batch API (see _collect_result,
    2026-09-03). A single-element call has nothing to order: correctness by
    construction rather than by inference, for the price of N cheap metadata
    calls that run in parallel anyway.

    Persists immediately on success — the DB write is the thing that makes
    this resumable, so it must not wait for the rest of the pass.

    Calls the private _register_files, not the public register_files(auth=...),
    deliberately: the public wrapper's whole added value is minting an OAuth
    Bearer token, and every OAuth identity is a dead end on this endpoint.
    A Bearer token alongside the client's api_key is rejected outright
    (OVERLOADED_CREDENTIALS, "expected only one form of authentication"); user
    ADC credentials can't be re-scoped, so they fail ACCESS_TOKEN_SCOPE_
    INSUFFICIENT; and a raw service account is refused by name ("Access to
    Gemini API is restricted with service accounts. Use authorization keys
    instead"). The Gemini API key IS that authorization key — it already
    resolves to a service account (visible in GCS's own denial message), which
    is the identity that needs objectViewer on the bucket. So the api_key
    path, with no Bearer at all, is the only one that works, and the private
    method is just the public one minus the token it can't use.
    """
    # Retried in-process because a failure here costs a visual_attempts below,
    # and at REGISTER_WORKERS-wide concurrency a steady ~2% of these calls come
    # back as transient socket errors ("Server disconnected without sending a
    # response", ETIMEDOUT, EBADF) that succeed immediately on a second try.
    # Without the retry those blips spend the same budget as a genuinely
    # unreadable object, and three unlucky passes strand a post permanently.
    last: Exception | None = None
    for attempt in range(REGISTER_ATTEMPTS):
        try:
            resp = _client.files._register_files(uris=[video_uri(post_id)])
            file = (resp.files or [None])[0]
            if file is None or not file.name:
                raise ValueError("register_files returned no file")
            last = None
            break
        except Exception as exc:
            last = exc
            if attempt < REGISTER_ATTEMPTS - 1:
                time.sleep(2**attempt)
    if last is not None:
        logger.warning("registration failed for post=%s: %s", post_id, last)
        # Burns an attempt on purpose. Registration failures are otherwise
        # invisible to _finalize_videos (which only archives on vlm_json or
        # exhausted attempts), so a permanently unregisterable object — a
        # truncated upload, an unreadable blob — would be re-claimed every
        # pass forever. MAX_ATTEMPTS is what bounds that.
        _bump_attempts([post_id])
        return False

    _set_file_name(post_id, file.name)
    return True


# ---------------------------------------------------------------- pass B


def _estimate_request_bytes(post_id: UUID, file_name: str, file_uri: str) -> int:
    """Serialized size of one inlined request. SYSTEM_INSTRUCTION dominates and
    repeats per request (it lives in each request's config, not once per job),
    so it has to be counted per candidate, not amortized."""
    return len(SYSTEM_INSTRUCTION) + len(file_uri) + len(file_name) + len(str(post_id)) + 256


def _fit_to_budget(rows: list[tuple[UUID, str]], limit: int) -> list[tuple[UUID, str]]:
    """Longest prefix of rows whose cumulative request size stays under limit.

    Always yields at least one row even if that row alone exceeds the budget —
    otherwise a single oversized post would stall the pass forever, claiming
    nothing and never reaching MAX_ATTEMPTS to be finalized out of the way.
    """
    out: list[tuple[UUID, str]] = []
    total = 0
    for post_id, file_name in rows:
        if len(out) >= MAX_BATCH_POSTS:
            break
        size = _estimate_request_bytes(post_id, file_name, file_uri="")
        if out and total + size > limit:
            break
        out.append((post_id, file_name))
        total += size
    return out


def _claim_registered() -> list[tuple[UUID, str]]:
    """Registered posts still lacking a description, capped by request size.

    Filters on vlm_json, not visual_description: a handful of posts carry a
    visual_description from an older pre-vlm_json pass (legacy "PRODUCTS: ..."
    format, visual_model "gemini-3.6-flash") with no vlm_json to match —
    filtering on visual_description silently orphaned them from ever being
    reclaimed.
    """
    db = SessionLocal()
    try:
        stmt = (
            select(Post.id, Post.gemini_file_name)
            .where(
                Post.gemini_file_name.isnot(None),
                Post.vlm_json.is_(None),
                Post.visual_attempts < MAX_ATTEMPTS,
            )
            .order_by(Post.id)
        )
        rows = [(row.id, row.gemini_file_name) for row in db.execute(stmt)]
    finally:
        db.close()
    return _fit_to_budget(rows, BATCH_REQUEST_SIZE_LIMIT)


def _bump_attempts(post_ids: list[UUID]) -> None:
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
    """Drops the Gemini-side *registration*, not the GCS object — registration
    is a pointer, so the bytes under videos/non-described/ are untouched and a
    post can always be re-registered. Best-effort: failures are logged, and
    the Files API's 48h expiry is the backstop."""
    try:
        _client.files.delete(name=file_name)
    except Exception as exc:
        logger.warning("failed to delete registration=%s: %s", file_name, exc)


def _check_status(item: tuple[UUID, str]) -> tuple[UUID, types.File | None]:
    post_id, file_name = item
    try:
        return post_id, _client.files.get(name=file_name)
    except Exception as exc:
        # Almost always a registration that aged out (Files API entries expire
        # after 48h) while the post sat unfinished. Clearing the column drops
        # the post back into pass A, which re-registers it from the GCS object
        # that's still sitting there — self-healing, no manual purge.
        logger.warning("registration %s for post=%s is gone (%s) — will re-register", file_name, post_id, exc)
        _set_file_name(post_id, None)
        return post_id, None


def _await_active(candidates: list[tuple[UUID, str]]) -> list[tuple[UUID, types.File]]:
    """Polls every registered file together, round by round, on a shared
    deadline — one round-trip per file per round instead of one blocked
    worker per file for the file's whole processing time."""
    deadline = time.monotonic() + FILE_ACTIVE_TIMEOUT_SEC
    pending = candidates
    resolved: list[tuple[UUID, types.File]] = []

    while pending:
        with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
            checked = list(pool.map(_check_status, pending))

        pending = []
        for post_id, file in checked:
            if file is None:
                continue  # registration gone; _check_status already reset the column
            if file.state == types.FileState.PROCESSING:
                pending.append((post_id, file.name))
            elif file.state == types.FileState.ACTIVE:
                resolved.append((post_id, file))
            else:
                logger.warning("post=%s: registration ended in state=%s", post_id, file.state)
                _delete_uploaded_file(file.name)
                _set_file_name(post_id, None)

        if pending and time.monotonic() > deadline:
            for post_id, file_name in pending:
                logger.warning("post=%s: registration stuck in PROCESSING", post_id)
                _delete_uploaded_file(file_name)
                _set_file_name(post_id, None)
            break
        if pending:
            time.sleep(FILE_ACTIVE_POLL_SEC)

    return resolved


# ---------------------------------------------------------------- collect


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


def _collect_result(item: types.InlinedResponse, expected: dict[str, str]) -> None:
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

    # One comparison, and it catches the whole class of bug above the moment
    # it recurs rather than after a routing pass has laundered a wrong
    # description into a world assignment.
    if expected.get(post_id) != item.metadata.get("file_name"):
        logger.error(
            "post=%s response file_name=%s does not match its registration %s — dropped",
            post_id, item.metadata.get("file_name"), expected.get(post_id),
        )
        return

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


def _collect_and_cleanup(item: types.InlinedResponse, expected: dict[str, str]) -> None:
    """One item's DB write + registration cleanup, threaded — each is an
    independent post row and an independent delete (a real network round-trip),
    so there's no reason to serialize hundreds/thousands of them one at a time
    the way the register and status-poll steps already aren't."""
    _collect_result(item, expected)
    if item.metadata and "file_name" in item.metadata:
        _delete_uploaded_file(item.metadata["file_name"])
        if "post_id" in item.metadata:
            # The registration is gone, so the column must not keep pointing at
            # it: a post that failed this pass but has retries left needs to
            # fall back into pass A rather than into a dead files.get.
            _set_file_name(UUID(item.metadata["post_id"]), None)
    else:
        logger.warning("video describe response missing metadata, cannot clean up its registration")


def _finalize_videos(candidate_ids: list[UUID]) -> None:
    """Archives non-described/ -> described/ only for posts that reached a
    terminal state this pass — succeeded (has vlm_json), or exhausted
    MAX_ATTEMPTS. A post that failed but still has retries left keeps its
    video under non-described/ so the next describe_posts() call can retry it
    directly, without a re-ingest. This is what makes a partial-failure pass
    recoverable instead of a silent, permanent loss."""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Post.id, Post.vlm_json, Post.visual_attempts).where(Post.id.in_(candidate_ids))
        ).all()
        for post_id, vlm_json, attempts in rows:
            if vlm_json is not None or attempts >= MAX_ATTEMPTS:
                try:
                    mark_described(post_id)
                except Exception as exc:
                    # Archival is bookkeeping over work that is already
                    # committed — vlm_json is written and the registration is
                    # deleted by the time we get here. A transient GCS blip
                    # here used to raise straight out of describe_posts() and
                    # kill the whole multi-round crawl AFTER its batch had
                    # succeeded (2026-09-08: RemoteDisconnected on copy_blob,
                    # 5983 descriptions collected, round 1 never generated).
                    # ponytail: the video stays under non-described/, so every
                    # later pass re-registers it once and never re-describes
                    # it (pass B needs vlm_json IS NULL). Harmless at a few
                    # posts; if it accumulates, sweep GCS for listed ids that
                    # already have vlm_json and archive those.
                    logger.warning("archiving video for post=%s failed: %s", post_id, exc)
    finally:
        db.close()


def describe_posts() -> int:
    """One pass: register whatever needs registering, then batch-describe
    whatever is registered. Returns the number of posts claimed for describing.
    """
    available = list_non_described_ids()
    print(f"VLM: {len(available)} videos in GCS under non-described/")

    unregistered = _claim_unregistered(available)
    if unregistered:
        print(f"VLM: registering {len(unregistered)}...")
        with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
            registered = sum(pool.map(_register_one, unregistered))
        print(f"VLM: {registered}/{len(unregistered)} registered")

    candidates = _claim_registered()
    if not candidates:
        return 0

    candidate_ids = [post_id for post_id, _ in candidates]
    _bump_attempts(candidate_ids)
    print(f"VLM: {len(candidates)} candidates claimed, waiting for ACTIVE...")

    active = _await_active(candidates)
    print(f"VLM: {len(active)}/{len(candidates)} active, building batch request...")
    requests = [
        types.InlinedRequest(
            model=VIDEO_MODEL,
            contents=[types.Part.from_uri(file_uri=file.uri, mime_type="video/mp4")],
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, **GENERATION_CONFIG),
            metadata={"post_id": str(post_id), "file_name": file.name},
        )
        for post_id, file in active
    ]
    expected = {str(post_id): file.name for post_id, file in active}

    if requests:
        job = _client.batches.create(model=VIDEO_MODEL, src=requests)
        print(f"VLM: batch submitted ({len(requests)} requests), waiting for Gemini...")
        while not job.done:
            time.sleep(BATCH_POLL_SEC)
            try:
                job = _client.batches.get(name=job.name)
            except Exception as exc:
                # A multi-hour batch job outlives plenty of transient network blips (sleep/wake
                # cycling the network interface chief among them) — losing the whole crawl run
                # to one dropped poll is worse than a bounded retry here.
                logger.warning("batch status poll failed, retrying: %s", exc)
        print("VLM: batch done, collecting results...")

        responses = (job.dest.inlined_responses or []) if job.dest else []
        with ThreadPoolExecutor(max_workers=VIDEO_WORKERS) as pool:
            done = 0
            for _ in pool.map(_collect_and_cleanup, responses, [expected] * len(responses)):
                done += 1
                if done % 50 == 0 or done == len(responses):
                    print(f"VLM: collected {done}/{len(responses)}")

    _finalize_videos(candidate_ids)
    print(f"VLM: pass complete — {len(candidates)} claimed")
    return len(candidates)


def _self_check() -> None:
    assert _strip_nul("a\x00b") == "ab"
    assert _strip_nul({"on_screen_text": ["ok\x00", "fine"], "n": 3}) == {"on_screen_text": ["ok", "fine"], "n": 3}
    assert _strip_nul([1, "x\x00y", None]) == [1, "xy", None]

    rows = [(UUID(int=i), f"files/f{i}") for i in range(10)]
    one = _estimate_request_bytes(rows[0][0], rows[0][1], "")
    assert _fit_to_budget(rows, one * 10) == rows, "budget exactly fitting all rows should take all"
    assert _fit_to_budget(rows, one * 3 + 1) == rows[:3], "budget should stop at the last row that fits"
    assert _fit_to_budget(rows, 0) == rows[:1], "a single oversized row must still be claimed, not stall the pass"
    assert _fit_to_budget([], 999) == []

    many = [(UUID(int=i), f"files/f{i}") for i in range(MAX_BATCH_POSTS + 50)]
    assert len(_fit_to_budget(many, 10**9)) == MAX_BATCH_POSTS, "count cap must bound a pass even with budget to spare"

    print("vision self-check ok")


if __name__ == "__main__":
    _self_check()

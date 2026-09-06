"""Video byte storage — GCS.

Object name is the post id, same identity trick the old on-disk path used:
"has a video for this post" is answered by listing the bucket, not a DB
column. Videos live under videos/non-described/ until vision.py has
described them, then move to videos/described/ — see
CLAUDEphase10gcsvideos.md for the full lifecycle.

Uses the same service-account credentials as register_files (gcp_auth.py)
rather than letting the storage client fall back to ADC's own env-var
lookup — one source of truth for GCP auth in this codebase, and it doesn't
depend on GOOGLE_APPLICATION_CREDENTIALS actually being exported into the
process environment (pydantic-settings reads .env into the Settings object,
it does not export it to os.environ).
"""

from uuid import UUID

from google.cloud import storage

from src.config.settings import settings
from src.services.gcp_auth import gcp_credentials

NON_DESCRIBED_PREFIX = "videos/non-described/"
DESCRIBED_PREFIX = "videos/described/"

_client = storage.Client(credentials=gcp_credentials, project=gcp_credentials.project_id)
_bucket = _client.bucket(settings.gcs_bucket)


def _blob_name(post_id: UUID, prefix: str = NON_DESCRIBED_PREFIX) -> str:
    return f"{prefix}{post_id}.mp4"


def video_uri(post_id: UUID) -> str:
    return f"gs://{settings.gcs_bucket}/{_blob_name(post_id)}"


def store_video(post_id: UUID, data: bytes) -> None:
    _bucket.blob(_blob_name(post_id)).upload_from_string(data, content_type="video/mp4")


def list_non_described_ids() -> set[UUID]:
    """Which posts have a video sitting in GCS, not yet described. Answers the
    same question the old `video_path(id).exists()` disk stat did, just as one
    list call instead of N stats — call once per claim pass."""
    ids: set[UUID] = set()
    for blob in _client.list_blobs(_bucket, prefix=NON_DESCRIBED_PREFIX):
        name = blob.name.removeprefix(NON_DESCRIBED_PREFIX).removesuffix(".mp4")
        try:
            ids.add(UUID(name))
        except ValueError:
            continue  # not one of ours; ignore rather than fail the whole listing
    return ids


def mark_described(post_id: UUID) -> None:
    """Archive: copy non-described/{id}.mp4 -> described/{id}.mp4, then delete
    the original. GCS has no atomic move. Replaces the old delete_video() —
    same call site in vision.py's _finalize_videos, new behavior (archive, not
    discard). A post that still has retries left never reaches this call, so
    it stays in non-described/ for the next pass, same as today."""
    src = _bucket.blob(_blob_name(post_id, NON_DESCRIBED_PREFIX))
    if not src.exists():
        return
    _bucket.copy_blob(src, _bucket, _blob_name(post_id, DESCRIBED_PREFIX))
    src.delete()

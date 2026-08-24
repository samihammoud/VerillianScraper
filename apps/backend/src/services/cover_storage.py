"""Cover-image byte storage — local disk for the single-box MVP.

Storing bytes (vs. keeping only the signed URL) converts the expiry deadline
from "the entire pipeline must finish before the URL rots" to "one cheap CDN
GET must succeed" — everything downstream (enrichment, re-describing with a
better VLM later, a review UI) then has no deadline at all.

bytea in Postgres is fine at smoke-test volume and wrong at bulk volume, so
bytes live on disk and only a reference (`cover_key`) sits in the DB. The swap
to S3 later is a rewrite of this one file, not a schema or caller change.
"""

from pathlib import Path
from uuid import UUID

COVERS_DIR = Path(__file__).resolve().parents[2] / "out" / "covers"


def store_cover(post_id: UUID, data: bytes) -> str:
    """Writes cover bytes to local disk. Returns the cover_key."""
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    cover_key = f"{post_id}.jpg"
    (COVERS_DIR / cover_key).write_bytes(data)
    return cover_key


def load_cover(cover_key: str) -> bytes:
    return (COVERS_DIR / cover_key).read_bytes()

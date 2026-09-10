"""Ingest + VLM-describe one account as a style reference. Standalone.

Deliberately does NOT go through vision.describe_posts(): that claims across
the whole posts table and would steal posts from any world crawl mid-round,
and it uses the batch submit/poll path, which is overkill for one account.
This is a per-account claim + synchronous generate_content per post.

Schema/system instruction come from data/boredinary/ (note: not the usual
data/<world>/crawl/ layout — this is a one-off reference bundle, not a world),
read directly so vision_schema.ACTIVE_SCHEMA_WORLD stays pointed at whatever
world is actually crawling.

Video bytes are pulled from GCS into memory and inlined per request. Nothing
touches out/videos/ or the shared gemini_file_name registration column, so a
concurrent crawl is unaffected.

    python -m src.scripts.run_boredinary [count]
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from google import genai
from google.genai import types
from sqlalchemy import select, update

from src.config.settings import settings
from src.db.models import Post
from src.db.session import SessionLocal
from src.services.ingest import ingest_account
from src.services.video_storage import _bucket, _blob_name
from src.services.vision import VIDEO_MODEL, HTTP_TIMEOUT_SEC, _strip_nul

HANDLE = "boredinarylife"
BUNDLE = Path(__file__).resolve().parents[1].parent / "data" / "boredinary"
OUT_DIR = BUNDLE / "posts"
WORKERS = 8
MAX_INLINE_BYTES = 18 * 1024**2  # request body cap for inline video bytes

SCHEMA = json.loads((BUNDLE / "vlm_schema.json").read_text())
SYSTEM_INSTRUCTION = (BUNDLE / "vlm_system_instruction.txt").read_text()

_client = genai.Client(
    api_key=settings.gemini_api_key,
    http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_SEC * 1000),
)


def _describe(post_id: UUID) -> bool:
    blob = _bucket.blob(_blob_name(post_id))
    if not blob.exists():
        print(f"  {post_id}: no video in GCS, skipped")
        return False
    data = blob.download_as_bytes()
    if len(data) > MAX_INLINE_BYTES:
        print(f"  {post_id}: {len(data) // 1024**2}MB exceeds inline cap, skipped")
        return False

    resp = _client.models.generate_content(
        model=VIDEO_MODEL,
        contents=[types.Part.from_bytes(data=data, mime_type="video/mp4")],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0,
            media_resolution="MEDIA_RESOLUTION_LOW",
            response_mime_type="application/json",
            response_schema=SCHEMA,
        ),
    )
    obj = _strip_nul(json.loads(resp.text))

    (OUT_DIR / f"{post_id}.json").write_text(json.dumps(obj, indent=2, ensure_ascii=False))

    db = SessionLocal()
    try:
        db.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(vlm_json=obj, visual_model=VIDEO_MODEL, visual_generated_at=datetime.now(timezone.utc))
        )
        db.commit()
    finally:
        db.close()
    print(f"  {post_id}: ok")
    return True


def main(count: int = 30) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        account_id = ingest_account(db, handle=HANDLE, count=count)
        if account_id is None:
            sys.exit(f"ingest returned nothing for @{HANDLE}")
        # Scoped to this account, not the shared visual_description claim query.
        post_ids = list(
            db.scalars(select(Post.id).where(Post.account_id == account_id, Post.vlm_json.is_(None)).order_by(Post.id))
        )
    finally:
        db.close()

    print(f"@{HANDLE}: account={account_id}, {len(post_ids)} posts to describe")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        ok = sum(pool.map(_describe, post_ids))
    print(f"done: {ok}/{len(post_ids)} described -> {OUT_DIR}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)

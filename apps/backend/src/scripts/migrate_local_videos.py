"""One-way migration: out/videos/*.mp4 -> gs://<bucket>/videos/non-described/.

Phase 10 moved video storage to GCS. Videos downloaded by the pre-phase-10
pipeline are still on local disk, and their TikTok play URLs are long dead —
so this upload is the only thing standing between those posts and being
describable at all.

Idempotent and resumable, which matters at ~37GB: files already present in
GCS are skipped, so an interrupted run just picks up where it stopped. Local
files are NOT deleted — the local copy stays until you're satisfied the
upload is good.

Only uploads videos belonging to a post that can actually use one (exists in
the DB, no vlm_json yet, retries left). Orphaned .mp4s whose post row is gone
are reported and skipped rather than uploaded into a bucket that would list
them forever.

    python -m src.scripts.migrate_local_videos [--dry-run]
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import UUID

from sqlalchemy import text

from src.db.session import SessionLocal
from src.services.video_storage import list_non_described_ids, store_video

VIDEOS_DIR = "out/videos"
WORKERS = 8  # concurrent uploads; bounded by upstream bandwidth, not by GCS. 12 produced ~6% transient TLS failures.
UPLOAD_ATTEMPTS = 3  # per-file retries for those transient failures — see up()


def _local_ids() -> tuple[list[UUID], int]:
    ids, unparseable = [], 0
    for name in os.listdir(VIDEOS_DIR):
        if not name.endswith(".mp4"):
            continue
        try:
            ids.append(UUID(name[:-4]))
        except ValueError:
            unparseable += 1
    return ids, unparseable


def migrate(dry_run: bool = False) -> int:
    local, unparseable = _local_ids()
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""select id from posts
                     where id = any(:ids) and vlm_json is null and visual_attempts < 3"""),
            {"ids": local},
        ).all()
    finally:
        db.close()

    wanted = {r[0] for r in rows}
    already = list_non_described_ids()
    todo = sorted(wanted - already)

    total_bytes = sum(os.path.getsize(f"{VIDEOS_DIR}/{p}.mp4") for p in todo)
    print(f"local .mp4 files      : {len(local)}" + (f" (+{unparseable} unparseable)" if unparseable else ""))
    print(f"  belong to a claimable post: {len(wanted)}")
    print(f"  already in GCS            : {len(wanted & already)}")
    print(f"  orphaned / not claimable  : {len(local) - len(wanted)} (skipped)")
    print(f"TO UPLOAD             : {len(todo)}  ({total_bytes / 1024**3:.1f} GB)")
    if dry_run or not todo:
        return len(todo)

    def up(post_id: UUID) -> tuple[UUID, Exception | None]:
        # Sustained parallel uploads of ~10MB objects draw a steady trickle of
        # transient TLS failures (SSLEOFError "EOF occurred in violation of
        # protocol", read timeouts) — measured ~6% per attempt at 12 workers.
        # They're per-request and clear on retry, so retrying in-process beats
        # leaving them for another full pass over 3500 files.
        last: Exception | None = None
        for attempt in range(UPLOAD_ATTEMPTS):
            try:
                with open(f"{VIDEOS_DIR}/{post_id}.mp4", "rb") as fh:
                    store_video(post_id, fh.read())
                return post_id, None
            except Exception as exc:
                last = exc
                if attempt < UPLOAD_ATTEMPTS - 1:
                    time.sleep(2**attempt)
        return post_id, last

    done = failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for fut in as_completed([pool.submit(up, p) for p in todo]):
            post_id, exc = fut.result()
            if exc:
                failed += 1
                print(f"  FAILED {post_id}: {exc}")
            else:
                done += 1
            if (done + failed) % 100 == 0:
                print(f"  {done + failed}/{len(todo)} ({done} ok, {failed} failed)")

    print(f"\nuploaded {done}/{len(todo)}" + (f", {failed} failed (re-run to retry)" if failed else ""))
    print("local copies left in place — delete out/videos yourself once satisfied")
    return done


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv[1:])

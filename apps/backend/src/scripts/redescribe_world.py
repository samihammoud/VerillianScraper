"""Re-fetch videos for one world's posts so the VLM can (re-)describe them.

Two situations need this, and they're the same operation:
  - the world's VLM response schema changed, so existing descriptions have the
    wrong shape (vision_schema.py / data/<world>/crawl/vlm_schema.json)
  - a bad pass produced descriptions attached to the wrong posts

    make redescribe WORLD=romance              # fetch videos for posts with no description
    make redescribe WORLD=romance WIPE=1       # clear every description first, then fetch
    make redescribe WORLD=romance DRY=1        # report scope, touch nothing

THE SELECTION KEYS ON `vlm_json IS NULL`, i.e. "this post still needs
describing" — never on "this post currently has a description". The two differ
the moment a wipe has run or a previous attempt died partway, and keying on the
wrong one makes the script silently no-op: after --wipe-first every post has a
NULL vlm_json, so a `vlm_json IS NOT NULL` filter matches nothing and reports
success having done nothing at all. Keying on outstanding work instead makes
this idempotent and safe to re-run after any partial failure.

WHY A FETCH IS NEEDED AT ALL: a described post has no video. The pre-phase-10
pipeline deleted it on success, and TikTok's play URL is signed and dies within
hours of ingest, so the only way back is re-listing the account for a fresh
URL. That can fail permanently — post deleted, account gone private, or aged
past the fetched window — and those posts simply stay undescribed.

--wipe-first is unconditional and destructive by design. When the defect is
mis-attribution you cannot tell which rows are wrong, so keeping the ones whose
video can't be refetched means knowingly keeping descriptions attached to the
wrong video — and those feed routing and world_term_stats, where a confident
wrong answer does more damage than a missing one. _backup_vlm dumps full row
state to out/vlm_backup_<world>_<ts>.jsonl (fsynced) before anything is
touched; that file is the only undo.

Nothing here calls the VLM. Once videos are in GCS, describe_posts() claims
them normally.
"""

import json
import logging
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from src.db.session import SessionLocal
from src.services.ingest import _fetch_video
from src.services.mapper import map_account_and_posts
from src.services.tiktok_client import get_user_videos
from src.services.video_storage import list_non_described_ids

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FETCH_COUNT = 100  # matches backfill_videos/ingest_account — one generous call, no pagination
WORKERS = 5  # accounts in parallel; each is one RapidAPI call plus N video downloads
MAX_ATTEMPTS = 3  # mirrors vision.MAX_ATTEMPTS — don't fetch for posts the VLM has already given up on

# Both selections are scoped by topology.world_posts — the routing *result* —
# not posts.discovered_by_world, which is only the intent of the crawl query
# that found the account and routinely disagrees (11563 posts were discovered
# by romance queries; 5265 actually routed there).
_IN_WORLD = """
    from posts p
    join topology.world_posts wp on wp.post_id = p.id
    join topology.worlds w on w.id = wp.world_id
    where w.slug = :world
"""


def _to_wipe(world: str) -> list[UUID]:
    """Posts that currently hold a description — what --wipe-first destroys."""
    db = SessionLocal()
    try:
        return [r.id for r in db.execute(
            text(f"select p.id {_IN_WORLD} and p.vlm_json is not null"), {"world": world})]
    finally:
        db.close()


def _needs_video(world: str) -> list[tuple[UUID, str, str]]:
    """(post_id, external_id, handle) for posts still needing a description.

    visual_attempts < MAX_ATTEMPTS because a post the VLM has already failed on
    three times won't be claimed even with a video present — fetching one would
    be pure waste.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text(f"""
            select p.id, p.external_id, a.handle
            from posts p
            join accounts a on a.id = p.account_id
            join topology.world_posts wp on wp.post_id = p.id
            join topology.worlds w on w.id = wp.world_id
            where w.slug = :world and p.vlm_json is null and p.visual_attempts < :max
            order by a.handle, p.id
        """), {"world": world, "max": MAX_ATTEMPTS}).all()
        return [(r.id, r.external_id, r.handle) for r in rows]
    finally:
        db.close()


def _backup_vlm(post_ids: list[UUID], world: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = Path("out") / f"vlm_backup_{world}_{stamp}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        rows = db.execute(
            text("""select id, vlm_json, visual_description, visual_model,
                           visual_generated_at, visual_attempts, gemini_file_name
                      from posts where id = any(:ids)"""),
            {"ids": post_ids},
        ).all()
        with open(path, "w") as fh:
            for r in rows:
                fh.write(json.dumps({
                    "id": str(r.id),
                    "vlm_json": r.vlm_json,
                    "visual_description": r.visual_description,
                    "visual_model": r.visual_model,
                    "visual_generated_at": r.visual_generated_at.isoformat() if r.visual_generated_at else None,
                    "visual_attempts": r.visual_attempts,
                    "gemini_file_name": r.gemini_file_name,
                }) + "\n")
            fh.flush()
            os.fsync(fh.fileno())  # the undo file must survive a crash mid-wipe
    finally:
        db.close()
    print(f"backed up {len(rows)} rows -> {path}  ({path.stat().st_size / 1024**2:.1f} MB)")
    return path


def _clear_vlm(post_ids: list[UUID]) -> None:
    """Reset every field the VLM pass owns. visual_attempts goes to 0 as well —
    these posts already spent their budget on the pass that produced the bad
    description, and leaving it at 3 would make them permanently unclaimable.
    gemini_file_name clears so pass A re-registers against the newly uploaded
    object rather than a stale (by now expired) Files API entry."""
    db = SessionLocal()
    try:
        db.execute(
            text("""update posts set vlm_json = null, visual_description = null,
                           visual_model = null, visual_generated_at = null,
                           visual_attempts = 0, gemini_file_name = null
                     where id = any(:ids)"""),
            {"ids": post_ids},
        )
        db.commit()
    finally:
        db.close()


def _refetch_account(handle: str, rows: list[tuple[UUID, str]]) -> int:
    """Re-list one account for fresh signed URLs and download its videos into
    GCS. Returns how many landed. A post no longer in the account's current
    video list (deleted, or aged past FETCH_COUNT) is reported and skipped."""
    try:
        raw = get_user_videos(handle, count=FETCH_COUNT)
        _, posts_data = map_account_and_posts(raw)
    except Exception as exc:
        logger.warning("re-list failed for account=%s: %s — %d posts skipped", handle, exc, len(rows))
        return 0

    fresh = {p["external_id"]: p for p in posts_data}
    got = 0
    for post_id, external_id in rows:
        src = fresh.get(external_id)
        if src is None:
            logger.warning("post=%s (external_id=%s) no longer listed by %s", post_id, external_id, handle)
            continue
        if _fetch_video({"id": post_id, "media_urls": src["media_urls"]}):
            got += 1
    return got


def redescribe_world(world: str, dry_run: bool = False, wipe_first: bool = False) -> int:
    if wipe_first:
        described = _to_wipe(world)
        print(f"world={world}: {len(described)} posts currently hold a description")
        if described and not dry_run:
            _backup_vlm(described, world)
            _clear_vlm(described)
            print(f"wiped all {len(described)}")

    targets = _needs_video(world)
    if not targets:
        print(f"world={world}: nothing needs describing")
        return 0

    # Already in GCS = already describable; re-downloading would be pure waste.
    have = list_non_described_ids()
    todo = [t for t in targets if t[0] not in have]

    by_handle: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
    for post_id, external_id, handle in todo:
        by_handle[handle].append((post_id, external_id))

    print(f"world={world}: {len(targets)} posts need describing")
    print(f"  already have a video in GCS : {len(targets) - len(todo)}")
    print(f"  need a video fetched        : {len(todo)} across {len(by_handle)} accounts")
    if dry_run:
        print("dry run — nothing fetched")
        return len(todo)

    handles = list(by_handle)
    total = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for handle, got in zip(handles, pool.map(_refetch_account, handles, (by_handle[h] for h in handles))):
            total += got
            print(f"  {handle}: {got}/{len(by_handle[handle])}")

    lost = len(todo) - total
    print(f"\nfetched {total}/{len(todo)} videos into GCS")
    if lost:
        print(f"{lost} posts have no recoverable video — they stay undescribed")
    print("run `make enrich` (or describe_posts) to describe them")
    return total


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: python -m src.scripts.redescribe_world <world_slug> [--dry-run] [--wipe-first]")
    redescribe_world(args[0], dry_run="--dry-run" in args, wipe_first="--wipe-first" in args)

"""Video byte storage — local disk, mirroring cover_storage.py.

No DB column: the path is derivable from the post id, so "has a video on disk"
is a stat() call, not a row to keep in sync. A missing file just means the post
is skipped by the VLM claim query.

Unlike covers, these are large and disposable — describe_posts clears the
directory on entry so orphans from a dead run don't accumulate.
"""

import shutil
from pathlib import Path
from uuid import UUID

VIDEOS_DIR = Path(__file__).resolve().parents[2] / "out" / "videos"


def video_path(post_id: UUID) -> Path:
    return VIDEOS_DIR / f"{post_id}.mp4"


def store_video(post_id: UUID, data: bytes) -> Path:
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    path = video_path(post_id)
    path.write_bytes(data)
    return path


def clear_videos() -> None:
    shutil.rmtree(VIDEOS_DIR, ignore_errors=True)

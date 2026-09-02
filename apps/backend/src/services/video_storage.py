"""Video byte storage — local disk.

No DB column: the path is derivable from the post id, so "has a video on disk"
is a stat() call, not a row to keep in sync. A missing file just means the post
is skipped by the VLM claim query.

Unlike covers, these are large and disposable — but only once a post is truly
done with (described, or out of retries). describe_posts() deletes per-post via
delete_video() as each post reaches a terminal state, never the whole directory
at once — a blanket sweep would strand any post that failed this pass but still
had retries left, with no video to retry from and nothing to re-download it.
"""

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


def delete_video(post_id: UUID) -> None:
    video_path(post_id).unlink(missing_ok=True)

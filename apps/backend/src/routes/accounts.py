from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from fastapi import APIRouter, Depends, HTTPException
import httpx
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services.mapper import map_account_and_posts, map_comments
from src.services.persistence import get_existing_post_external_ids, store_account_and_posts
from src.services.tiktok_client import get_comment_list, get_user_videos

router = APIRouter(prefix="/accounts", tags=["accounts"])

logger = logging.getLogger(__name__)

COMMENT_FETCH_WORKERS = 5


def _fetch_comments(handle: str, video_id: str) -> dict | None:
    try:
        return map_comments(get_comment_list(handle, video_id))
    except httpx.HTTPStatusError as exc:
        logger.warning("comment fetch failed for video_id=%s: %s", video_id, exc)
        return None


#default to 10 videos c
#cursor  allows pagination for more
@router.get("/{handle}/videos")
def list_account_videos(handle: str, count: int = 10, cursor: str = "0", db: Session = Depends(get_db)) -> dict:
    try:
        raw = get_user_videos(handle, count=count, cursor=cursor)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc)) from exc

    account_data, posts_data = map_account_and_posts(raw)

    if account_data:
        existing_ids = get_existing_post_external_ids(db, account_data["platform"], account_data["external_id"])
        new_posts = [post for post in posts_data if post["external_id"] not in existing_ids]

        if new_posts:
            with ThreadPoolExecutor(max_workers=COMMENT_FETCH_WORKERS) as pool:
                futures = {pool.submit(_fetch_comments, handle, post["external_id"]): post for post in new_posts}
                for future in as_completed(futures):
                    futures[future]["comments"] = future.result()

    store_account_and_posts(db, account_data, posts_data)

    return {"account": account_data, "posts": posts_data}

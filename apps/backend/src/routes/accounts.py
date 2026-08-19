from fastapi import APIRouter, Depends, HTTPException
import httpx
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services.mapper import map_account_and_posts
from src.services.persistence import store_account_and_posts
from src.services.tiktok_client import get_user_videos

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/{handle}/videos")
def list_account_videos(handle: str, count: int = 10, cursor: str = "0", db: Session = Depends(get_db)) -> dict:
    try:
        raw = get_user_videos(handle, count=count, cursor=cursor)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc)) from exc

    account_data, posts_data = map_account_and_posts(raw)
    store_account_and_posts(db, account_data, posts_data)

    return raw

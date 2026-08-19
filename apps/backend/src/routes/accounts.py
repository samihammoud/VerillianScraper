from fastapi import APIRouter, HTTPException
import httpx

from src.services.tiktok_client import get_user_videos

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/{handle}/videos")
def list_account_videos(handle: str, count: int = 10, cursor: str = "0") -> dict:
    try:
        return get_user_videos(handle, count=count, cursor=cursor)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc)) from exc

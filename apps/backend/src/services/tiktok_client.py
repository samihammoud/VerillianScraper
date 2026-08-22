"""RapidAPI TikTok wrapper — fetch a single account's posts.

Standalone and directly testable: run this file to hit the API and
inspect the raw response, with no FastAPI server or database involved.
"""

import httpx

from src.config.settings import settings

BASE_URL = "https://tiktok-api15.p.rapidapi.com/index/Tiktok/getUserVideos"
COMMENTS_URL = "https://tiktok-api15.p.rapidapi.com/index/Tiktok/getCommentListByVideo"

TEST_ACCOUNT_HANDLE = "breakawayclipz"
TEST_VIDEO_ID = "7627708826254331153"

_HEADERS = {
    "Content-Type": "application/json",
    "x-rapidapi-host": settings.rapidapi_host,
    "x-rapidapi-key": settings.rapidapi_key,
}


def get_user_videos(unique_id: str, count: int = 10, cursor: str = "0") -> dict:
    """Fetch recent videos for a TikTok account handle via RapidAPI."""
    response = httpx.get(
        BASE_URL,
        params={"unique_id": f"@{unique_id}", "count": count, "cursor": cursor},
        headers=_HEADERS,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def get_comment_list(unique_id: str, video_id: str, count: int = 10, cursor: str = "0") -> dict:
    """Fetch top-level comments for a single video via RapidAPI."""
    video_url = f"https://www.tiktok.com/@{unique_id}/video/{video_id}"
    response = httpx.get(
        COMMENTS_URL,
        params={"url": video_url, "count": count, "cursor": cursor},
        headers=_HEADERS,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    import json

    result = get_user_videos(TEST_ACCOUNT_HANDLE, count=3)
    print(json.dumps(result, indent=2))

    comments = get_comment_list(TEST_ACCOUNT_HANDLE, TEST_VIDEO_ID, count=3)
    print(json.dumps(comments, indent=2))

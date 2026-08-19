"""RapidAPI TikTok wrapper — fetch a single account's posts.

Standalone and directly testable: run this file to hit the API and
inspect the raw response, with no FastAPI server or database involved.
"""

import httpx

from src.config.settings import settings

BASE_URL = "https://tiktok-api15.p.rapidapi.com/index/Tiktok/getUserVideos"

TEST_ACCOUNT_HANDLE = "breakawayclipz"


def get_user_videos(unique_id: str, count: int = 10, cursor: str = "0") -> dict:
    """Fetch recent videos for a TikTok account handle via RapidAPI."""
    response = httpx.get(
        BASE_URL,
        params={"unique_id": f"@{unique_id}", "count": count, "cursor": cursor},
        headers={
            "Content-Type": "application/json",
            "x-rapidapi-host": settings.rapidapi_host,
            "x-rapidapi-key": settings.rapidapi_key,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    import json

    result = get_user_videos(TEST_ACCOUNT_HANDLE, count=3)
    print(json.dumps(result, indent=2))

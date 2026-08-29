"""RapidAPI TikTok wrapper (tiktok-scraper7) — search, user posts, comments.

Standalone and directly testable: run this file to hit the API and
inspect the raw response, with no FastAPI server or database involved.

This provider returns a flattened payload (`data.videos`, `video_id`, `play`,
`digg_count`) rather than TikTok's raw web-app structs, and every endpoint
accepts a plain handle — so unlike tiktok-api23 there is no secUid lookup, and
no per-account hop to pay for before posts can be fetched.

Failures arrive as HTTP 200 with a non-zero `code` and no `data` key, so the
status line alone means nothing here; `_get` is the only place that's checked.
"""

import logging

import httpx

from src.config.settings import settings

BASE_URL = f"https://{settings.rapidapi_host}"

TEST_ACCOUNT_HANDLE = "tiktok"
DEFAULT_REGION = "us"

logger = logging.getLogger(__name__)

_HEADERS = {
    "x-rapidapi-host": settings.rapidapi_host,
    "x-rapidapi-key": settings.rapidapi_key,
}


def _get(path: str, params: dict) -> dict:
    """GET + unwrap. Returns {} on a provider-level error rather than raising.

    An unknown handle is a normal, expected outcome of crawling search results
    — it must not abort the round — and every caller already treats an empty
    payload as "nothing here". Transport errors still raise.
    """
    response = httpx.get(f"{BASE_URL}{path}", params=params, headers=_HEADERS, timeout=30.0)
    response.raise_for_status()
    body = response.json()

    if body.get("code") != 0:
        logger.warning("%s failed: code=%s msg=%s", path, body.get("code"), body.get("msg"))
        return {}
    return body


def search_videos(keywords: str, cursor: str = "0", count: int = 30, region: str = DEFAULT_REGION) -> dict:
    """Keyword video search. Results are only ever read for author handles."""
    return _get(
        "/feed/search",
        {
            "keywords": keywords,
            "region": region,
            "count": count,
            "cursor": cursor,
            "publish_time": 0,  # 0 = any time
            "sort_type": 0,  # 0 = relevance
        },
    )


def get_user_videos(unique_id: str, count: int = 10, cursor: str = "0") -> dict:
    """Fetch recent videos for a TikTok account handle."""
    return _get("/user/posts", {"unique_id": unique_id, "count": count, "cursor": cursor, "sort_type": 0})


def get_comment_list(unique_id: str, video_id: str, count: int = 10, cursor: str = "0") -> dict:
    """Fetch top-level comments for a single video.

    Keyed by canonical video URL, so this provider genuinely needs the handle
    as well as the video id.
    """
    video_url = f"https://www.tiktok.com/@{unique_id}/video/{video_id}"
    return _get("/comment/list", {"url": video_url, "count": count, "cursor": cursor})


if __name__ == "__main__":
    import json

    from src.services.mapper import map_account_and_posts, map_comments, page_cursor, search_handles

    search = search_videos("dog toys")
    handles = search_handles(search)
    print(f"search:    {len(handles)} handles, page_cursor={page_cursor(search)}")
    print(f"           {handles[:8]}")

    videos = get_user_videos(TEST_ACCOUNT_HANDLE, count=3)
    account, posts = map_account_and_posts(videos)
    print(f"\nuser/posts: {account}, page_cursor={page_cursor(videos)}")
    if posts:
        print(json.dumps({k: str(v)[:70] for k, v in posts[0].items()}, indent=2))

        comments = map_comments(get_comment_list(TEST_ACCOUNT_HANDLE, posts[0]["external_id"], count=3))
        print(f"\ncomments:  total={comments['total']}")
        print(json.dumps(comments["comments"][:1], indent=2))

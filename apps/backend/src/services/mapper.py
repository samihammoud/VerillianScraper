"""Maps raw RapidAPI TikTok responses (tiktok-scraper7) into persistence dicts.

The wire shape has changed twice now; the dicts returned here have not. Every
downstream module keys off these exact keys, so this file is the only place
that knows what the provider emits.

Search and user-posts return the *same* envelope — `data.videos` with
`data.cursor`/`data.hasMore` — which is why one mapper and one cursor helper
serve both. Only the intent differs: search rows are read for author handles
and nothing else, since ingest re-fetches each account properly.
"""

from datetime import datetime, timezone
from typing import Any


def _videos(api_response: dict[str, Any]) -> list[dict[str, Any]]:
    return (api_response.get("data") or {}).get("videos") or []


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def page_cursor(api_response: dict[str, Any]) -> tuple[bool, str]:
    """(has_more, next_cursor) — identical for search and user posts."""
    data = api_response.get("data") or {}
    return bool(data.get("hasMore")), str(data.get("cursor", "0"))


def search_handles(api_response: dict[str, Any]) -> list[str]:
    """Distinct author handles from a search page, order preserved."""
    seen: dict[str, None] = {}
    for video in _videos(api_response):
        if handle := (video.get("author") or {}).get("unique_id"):
            seen.setdefault(handle)
    return list(seen)


def map_account_and_posts(api_response: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    videos = _videos(api_response)

    if not videos:
        return {}, []

    author = videos[0]["author"]
    account_data = {
        "platform": "tiktok",
        "external_id": author["id"],
        "handle": author["unique_id"],
        # Not in this payload; /user/info has it, but that is a second billed
        # call for a field nothing reads yet.
        "follower_count": None,
    }

    def _music(video: dict[str, Any]) -> dict[str, Any]:
        info = video.get("music_info") or {}
        return {
            "music_id": info.get("id"),
            "music_title": info.get("title"),
            "music_author": info.get("author"),
            "music_original": info.get("original"),
        }

    posts_data = [
        {
            "external_id": video["video_id"],
            "caption": video.get("title"),
            "thumbnail_url": video.get("cover"),
            "media_urls": {
                "play": video.get("play"),  # no watermark; what the VLM sees
                "wmplay": video.get("wmplay"),
                "cover": video.get("cover"),
                "origin_cover": video.get("origin_cover"),
            },
            "posted_at": _ts(video.get("create_time")),
            "likes": video.get("digg_count"),
            "comments": None,
            "shares": video.get("share_count"),
            "views": video.get("play_count"),
            "visual_description": None,
            "visual_model": None,
            "visual_generated_at": None,
            "vlm_json": None,
            "comment_attempts": 0,
            "visual_attempts": 0,
            **_music(video),
        }
        for video in videos
    ]

    return account_data, posts_data


def map_comments(api_response: dict[str, Any]) -> dict[str, Any]:
    data = api_response.get("data") or {}
    comments = data.get("comments") or []

    return {
        "total": data.get("total"),
        "comments": [
            {
                "id": comment["id"],
                "text": comment.get("text"),
                "likes": comment.get("digg_count"),
                "reply_count": comment.get("reply_total"),
                "posted_at": posted.isoformat() if (posted := _ts(comment.get("create_time"))) else None,
                "author": (comment.get("user") or {}).get("unique_id"),
            }
            for comment in comments
        ],
    }

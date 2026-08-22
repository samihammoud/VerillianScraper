"""Maps raw RapidAPI TikTok responses into Account/Post persistence dicts."""

from datetime import datetime, timezone
from typing import Any


def map_account_and_posts(api_response: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    videos = api_response.get("data", {}).get("videos", [])

    if not videos:
        return {}, []

    author = videos[0]["author"]
    account_data = {
        "platform": "tiktok",
        "external_id": author["id"],
        "handle": author["unique_id"],
        "follower_count": None,
    }

    posts_data = [
        {
            "external_id": video["video_id"],
            "caption": video.get("title"),
            "thumbnail_url": video.get("cover"),
            "media_urls": {
                "play": video.get("play"),
                "wmplay": video.get("wmplay"),
                "cover": video.get("cover"),
                "origin_cover": video.get("origin_cover"),
            },
            "posted_at": datetime.fromtimestamp(video["create_time"], tz=timezone.utc)
            if video.get("create_time")
            else None,
            "likes": video.get("digg_count"),
            "comments": None,
            "shares": video.get("share_count"),
            "views": video.get("play_count"),
        }
        for video in videos
    ]

    return account_data, posts_data


def map_comments(api_response: dict[str, Any]) -> dict[str, Any]:
    data = api_response.get("data", {})
    comments = data.get("comments") or []

    return {
        "total": data.get("total"),
        "comments": [
            {
                "id": comment["id"],
                "text": comment.get("text"),
                "likes": comment.get("digg_count"),
                "reply_count": comment.get("reply_total"),
                "posted_at": datetime.fromtimestamp(comment["create_time"], tz=timezone.utc).isoformat()
                if comment.get("create_time")
                else None,
                "author": comment.get("user", {}).get("unique_id"),
            }
            for comment in comments
        ],
    }

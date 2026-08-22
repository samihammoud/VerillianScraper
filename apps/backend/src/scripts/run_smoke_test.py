"""Routing smoke test: wipe scraped/derived data first (make reset-data), then run
this — ingests a fixed 4-account roster, routes every post, and dumps a CSV a human
can read row by row against the real videos.

This is a smoke test, not an eval. There are no ground-truth labels and no accuracy
number to report. The output is a spreadsheet a human reads to answer: is the
pipeline wired correctly, and does routing look sane. No scoring, thresholds, or
automated pass/fail.
"""

import csv
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from src.db.models import Post, World
from src.db.session import SessionLocal
from src.services.blob import select_comments, token_count
from src.services.ingest import ingest_account
from src.services.routing import persist_routing, route_post
from src.services.tiktok_client import get_user_videos

TEST_ACCOUNTS = [
    "zing1t2",  # variety
    "products4review",  # product
    "healthwellnesslifestyle",  # health wellness
    "surthycooks",  # food
]
POSTS_PER_ACCOUNT = 15

OUT_DIR = Path(__file__).resolve().parents[2] / "out"

CSV_BASE_COLUMNS = [
    "handle",
    "post_external_id",
    "video_url",
    "thumbnail_url",
    "posted_at",
    "caption",
    "world",
    "cosine",
    "runner_up_world",
    "margin",
]
CSV_TAIL_COLUMNS = [
    "visual_ok",
    "products_none_visible",
    "visual_description",
    "n_comments_available",
    "n_comments_used",
    "blob_tokens",
    "blob_text",
]


def _verify_handles(handles: list[str]) -> None:
    print("Verifying handles resolve...")
    for handle in handles:
        try:
            raw = get_user_videos(handle, count=1)
        except httpx.HTTPStatusError as exc:
            print(f"FATAL: handle {handle!r} failed to resolve: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

        if not raw.get("data", {}).get("videos"):
            print(f"FATAL: handle {handle!r} resolved but returned no videos", file=sys.stderr)
            raise SystemExit(1)

        print(f"  ok: @{handle}")


def _comment_count(post: Post) -> int:
    if not post.comments:
        return 0
    return len(post.comments.get("comments", []))


def _ingest_all(db) -> None:
    print("\nIngesting...")
    for handle in TEST_ACCOUNTS:
        account_id = ingest_account(db, handle, POSTS_PER_ACCOUNT)
        if account_id is None:
            print(f"{handle}: no videos ingested")
            continue

        posts = db.execute(select(Post).where(Post.account_id == account_id)).scalars().all()
        n_posts = len(posts)
        n_with_comments = sum(1 for p in posts if p.comments is not None)
        n_described = sum(1 for p in posts if p.visual_description is not None)
        n_failed = sum(
            1
            for p in posts
            if p.thumbnail_url and p.visual_generated_at is not None and p.visual_description is None
        )
        print(
            f"{handle}: posts_fetched={n_posts} comments_fetched={n_with_comments} "
            f"thumbnails_described={n_described} thumbnails_failed={n_failed}"
        )


def _route_all(db) -> list[dict]:
    print("\nRouting...")
    worlds = db.execute(select(World).order_by(World.slug)).scalars().all()  # load once, not per-post
    world_names = {world.id: world.name for world in worlds}

    posts = db.execute(select(Post)).scalars().all()

    rows = []
    for post in posts:
        account = post.account
        comments_list = (post.comments or {}).get("comments", [])

        winning_world_id, post_vec, best_sim, blob_text, margin, runner_up_id, _runner_up_sim = route_post(
            post, worlds
        )
        engagement = (post.likes or 0) + 3 * _comment_count(post) + 5 * (post.shares or 0)
        persist_routing(db, post, account, winning_world_id, post_vec, engagement, blob_text, best_sim, margin)

        visual_ok = post.visual_description is not None
        products_none_visible = visual_ok and "none visible" in (post.visual_description or "").lower()

        row = {
            "handle": account.handle,
            "post_external_id": post.external_id,
            "video_url": f"https://www.tiktok.com/@{account.handle}/video/{post.external_id}",
            "thumbnail_url": post.thumbnail_url,
            "posted_at": post.posted_at.isoformat() if post.posted_at else None,
            "caption": post.caption,
            "world": world_names[winning_world_id],
            "cosine": best_sim,
            "runner_up_world": world_names.get(runner_up_id),
            "margin": margin,
            "visual_ok": visual_ok,
            "products_none_visible": products_none_visible,
            "visual_description": post.visual_description,
            "n_comments_available": len(comments_list),
            "n_comments_used": len(select_comments(comments_list)),
            "blob_tokens": token_count(blob_text),
            "blob_text": blob_text,
        }
        rows.append(row)

    return rows


def _write_csv(rows: list[dict]) -> Path:
    rows = sorted(rows, key=lambda r: r["margin"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"smoke_test_{timestamp}.csv"

    fieldnames = CSV_BASE_COLUMNS + CSV_TAIL_COLUMNS
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def _percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _print_summary(rows: list[dict]) -> None:
    print("\n--- Summary ---")

    print("\nPosts per world:")
    by_world: dict[str, int] = {}
    for row in rows:
        by_world[row["world"]] = by_world.get(row["world"], 0) + 1
    for world, count in sorted(by_world.items(), key=lambda kv: -kv[1]):
        print(f"  {world}: {count}")

    n = len(rows)
    n_visual_ok = sum(1 for r in rows if r["visual_ok"])
    n_products_none = sum(1 for r in rows if r["products_none_visible"])
    print(f"\nvisual_ok rate: {n_visual_ok}/{n} ({n_visual_ok / n:.1%})")
    if n_visual_ok:
        print(f"products_none_visible rate (of visual_ok): {n_products_none}/{n_visual_ok} ({n_products_none / n_visual_ok:.1%})")

    margins = [r["margin"] for r in rows]
    print(
        f"\nmargin percentiles: p10={_percentile(margins, 0.10):.4f} "
        f"p50={_percentile(margins, 0.50):.4f} p90={_percentile(margins, 0.90):.4f}"
    )

    print("\n5 lowest-margin rows:")
    for row in sorted(rows, key=lambda r: r["margin"])[:5]:
        print(
            f"  margin={row['margin']:.4f} {row['handle']} | {(row['caption'] or '')[:50]!r} "
            f"-> {row['world']} (runner-up: {row['runner_up_world']})"
        )


def main() -> None:
    _verify_handles(TEST_ACCOUNTS)

    db = SessionLocal()
    try:
        _ingest_all(db)
        rows = _route_all(db)
    finally:
        db.close()

    path = _write_csv(rows)
    print(f"\nWrote {len(rows)} rows to {path}")

    _print_summary(rows)


if __name__ == "__main__":
    main()

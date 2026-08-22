"""Routes every post in the Account Store to a world, populating topology.world_posts.

No on_conflict_do_nothing here — re-running this against already-routed posts
will insert duplicate world_posts rows, matching the current scope (no dedup yet).
If blob assembly or the budgets change, re-route the whole table rather than
letting old and new rows accumulate side by side.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from src.db.models import Account, Post, World
from src.db.session import SessionLocal
from src.services.routing import persist_routing, route_post
from src.services.vision import MODEL as VISION_MODEL
from src.services.vision import describe_thumbnail


def _comment_count(post: Post) -> int:
    if not post.comments:
        return 0
    total = post.comments.get("total")
    return total if total is not None else len(post.comments.get("comments", []))


def _ensure_visual_description(db, post: Post) -> None:
    """Generate + store the visual description once per post, reused on re-routes."""
    if post.visual_generated_at is not None or not post.thumbnail_url:
        return

    post.visual_description = describe_thumbnail(post.thumbnail_url)
    post.visual_model = VISION_MODEL
    post.visual_generated_at = datetime.now(timezone.utc)
    db.commit()


def run_routing(db) -> None:
    worlds = db.execute(select(World)).scalars().all()  # load once, not per-post
    posts = db.execute(select(Post)).scalars().all()  # or a batch — full table is fine at MVP volume

    for post in posts:
        _ensure_visual_description(db, post)

        account = db.get(Account, post.account_id)
        engagement = (post.likes or 0) + 3 * _comment_count(post) + 5 * (post.shares or 0)

        winning_world_id, post_vec, best_sim, blob_text, margin = route_post(post, worlds)
        persist_routing(db, post, account, winning_world_id, post_vec, engagement, blob_text, best_sim, margin)

        print(
            f"{account.handle} | {(post.caption or '')[:40]!r} "
            f"-> world={winning_world_id} cos={best_sim:.3f} margin={margin:.3f}"
        )


if __name__ == "__main__":
    db = SessionLocal()
    try:
        run_routing(db)
    finally:
        db.close()

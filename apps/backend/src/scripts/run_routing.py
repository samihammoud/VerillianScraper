"""Stage 3 of the bulk pipeline: batch-embed and route posts to worlds.

Bulk-shaped, unlike the old version:
  - Keyset pagination (WHERE id > :last ORDER BY id LIMIT N) instead of
    .all()'ing the whole posts table into memory.
  - One embed_batch() call per page instead of one embed() call per post.
  - One vectorized matmul scores a whole page against all 8 worlds at once,
    instead of a Python loop of cosines per post.
  - One commit per batch, not per post.
  - No account join at all — persist_routing only needs account_id, which is
    already a plain column on Post.
  - Upsert on world_posts (unique on post_id), so re-running this is safe —
    a re-route updates the existing row instead of duplicating it.
"""

from sqlalchemy import select

from src.db.models import Post, World
from src.db.session import SessionLocal
from src.services.routing import normalized_world_matrix, persist_routing_batch, route_posts_batch

BATCH_SIZE = 500


def run_routing(db) -> None:
    worlds = db.execute(select(World).order_by(World.slug)).scalars().all()  # load once, not per-post
    world_matrix, world_ids = normalized_world_matrix(worlds)

    last_id = None
    total_routed = 0

    while True:
        stmt = select(Post).order_by(Post.id).limit(BATCH_SIZE)
        if last_id is not None:
            stmt = stmt.where(Post.id > last_id)

        posts = db.execute(stmt).scalars().all()
        if not posts:
            break

        results = route_posts_batch(posts, world_matrix, world_ids)
        persist_routing_batch(db, results)

        total_routed += len(posts)
        print(f"routed batch of {len(posts)} (total {total_routed})")

        last_id = posts[-1].id

    print(f"done — {total_routed} posts routed")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        run_routing(db)
    finally:
        db.close()

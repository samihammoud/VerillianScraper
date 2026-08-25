from fastapi import APIRouter
from sklearn.manifold import TSNE
from sqlalchemy import select
import numpy as np

from src.db.models import Post, World, WorldPost
from src.db.session import SessionLocal

router = APIRouter(prefix="/api", tags=["topology"])


@router.get("/topology")
def topology() -> dict:
    """8 worlds + routed posts, projected into one shared 3D space.

    t-SNE, not PCA: on these 1536-dim embeddings the top components
    capture only ~20% of variance, so a linear projection puts points at
    misleading distances. t-SNE optimizes to preserve neighbor structure —
    the thing this chart is actually claiming to show — at the cost of x/y/z
    having no standalone numeric meaning (hence no axis labels/ticks).
    One endpoint, not two, so worlds and posts share the same embedding
    (fit jointly, same run) instead of two incomparable projections.
    3 components (not 2): the UI renders this as a rotatable scene, and a
    literal third axis of neighbor structure is more honest than a flat
    projection with a fake extruded z.

    `similarity` is each post's real cosine similarity to its own world's
    reference_embedding — the actual routing score, not a display fabrication.
    The UI uses it to control how tightly a post's marker sits inside its
    world's basin (a confident route converges further than a marginal one).
    """
    db = SessionLocal()
    try:
        worlds = db.execute(select(World)).scalars().all()
        world_posts = db.execute(select(WorldPost)).scalars().all()
        posts_by_id = {p.id: p for p in db.execute(select(Post)).scalars().all()}
        worlds_by_id = {w.id: w for w in worlds}

        vectors = [w.reference_embedding for w in worlds] + [wp.embedding for wp in world_posts]
        if len(vectors) < 5:
            return {"worlds": [], "posts": []}

        matrix = np.array(vectors, dtype=float)
        perplexity = min(30, max(2, len(vectors) // 3))
        coords = TSNE(n_components=3, perplexity=perplexity, init="pca", random_state=42).fit_transform(matrix)

        n = len(worlds)
        posts = []
        for i, wp in enumerate(world_posts):
            post = posts_by_id.get(wp.post_id)
            world = worlds_by_id.get(wp.world_id)
            post_vec = np.array(wp.embedding, dtype=float)
            world_vec = np.array(world.reference_embedding, dtype=float) if world else None
            similarity = (
                float(np.dot(post_vec, world_vec) / (np.linalg.norm(post_vec) * np.linalg.norm(world_vec)))
                if world is not None
                else 0.0
            )
            posts.append(
                {
                    "id": str(wp.id),
                    "world_id": str(wp.world_id),
                    "x": float(coords[n + i, 0]),
                    "y": float(coords[n + i, 1]),
                    "z": float(coords[n + i, 2]),
                    "similarity": similarity,
                    "caption": (post.caption or "")[:120] if post else "",
                }
            )

        return {
            "worlds": [
                {
                    "id": str(w.id),
                    "slug": w.slug,
                    "name": w.name,
                    "x": float(coords[i, 0]),
                    "y": float(coords[i, 1]),
                    "z": float(coords[i, 2]),
                }
                for i, w in enumerate(worlds)
            ],
            "posts": posts,
        }
    finally:
        db.close()

"""Routes Posts to Worlds by cosine similarity, and persists the result.

Visual description only (see blob.py) is embedded — caption/comments are
dropped to keep the routing vector purely topical. No weighted fusion, no
per-modality vectors — matching the scope of this phase.

Two shapes are exposed:
  - route_post / persist_routing — one post at a time. Used by the fixed-roster
    smoke test, where reproducibility matters more than throughput.
  - route_posts_batch / persist_routing_batch — a whole page at once, used by
    the bulk routing driver (run_routing.py): one batched embedding call
    (OpenAI accepts up to 2048 inputs/request) and one vectorized matmul
    against the 8 world references, instead of one embed() call and one
    Python-loop cosine per post.
"""

import uuid

import numpy as np
from sqlalchemy.dialects.postgresql import insert

from src.db.models import Post, World, WorldPost
from src.services.blob import assemble_blob
from src.services.embeddings import embed, embed_batch
from src.services.linalg import l2_normalize


def route_post(post: Post, worlds: list[World]) -> tuple[uuid.UUID, list[float], float, str, float, uuid.UUID | None, float | None]:
    # 1. Assemble the blob — visual description only, budgeted
    text_blob = assemble_blob(post.visual_description)

    # 2. Embed — one call
    post_vec = np.array(embed(text_blob))

    # 3. Cosine similarity against every world
    sims = []
    for world in worlds:
        world_vec = np.array(world.reference_embedding)
        cos_sim = np.dot(post_vec, world_vec) / (np.linalg.norm(post_vec) * np.linalg.norm(world_vec))
        sims.append((world.id, float(cos_sim)))

    # 4. Argmax wins; margin + runner-up are free since both are already computed —
    #    kept for the abstain threshold, chosen later once there's labeled data.
    #    Only the winner and runner-up are returned, not the full per-world spread.
    sims.sort(key=lambda pair: pair[1], reverse=True)
    winning_world_id, best_sim = sims[0]
    runner_up_world_id, runner_up_sim = sims[1] if len(sims) > 1 else (None, None)
    margin = best_sim - (runner_up_sim or 0.0)

    return winning_world_id, post_vec.tolist(), best_sim, text_blob, margin, runner_up_world_id, runner_up_sim


def _upsert_stmt(rows: list[dict]):
    stmt = insert(WorldPost).values(rows)
    return stmt.on_conflict_do_update(
        index_elements=["post_id"],
        set_={
            "world_id": stmt.excluded.world_id,
            "embedding": stmt.excluded.embedding,
            "posted_at": stmt.excluded.posted_at,
            "blob_text": stmt.excluded.blob_text,
            "cosine": stmt.excluded.cosine,
            "margin": stmt.excluded.margin,
        },
    )


def persist_routing(
    db, post: Post, winning_world_id: uuid.UUID, post_vec: list[float], blob_text: str, cosine: float, margin: float
) -> None:
    """Upserts one routing result. account_id comes straight off `post` — no
    account lookup/join needed, it's already a plain column on Post."""
    db.execute(
        _upsert_stmt(
            [
                {
                    "post_id": post.id,
                    "account_id": post.account_id,
                    "world_id": winning_world_id,
                    "embedding": post_vec,
                    "posted_at": post.posted_at,
                    "blob_text": blob_text,
                    "cosine": cosine,
                    "margin": margin,
                }
            ]
        )
    )
    db.commit()


def normalized_world_matrix(worlds: list[World]) -> tuple[np.ndarray, list[uuid.UUID]]:
    """Stacks + L2-normalizes the world reference embeddings once, for vectorized scoring."""
    matrix = np.array([world.reference_embedding for world in worlds])  # (8, 1536)
    return l2_normalize(matrix), [world.id for world in worlds]


def route_posts_batch(posts: list[Post], world_matrix: np.ndarray, world_ids: list[uuid.UUID]) -> list[dict]:
    """Batch-embeds a page of posts and scores all of them against all worlds
    in one matmul. Returns one result dict per post — posts with no visual
    description yet (blob would be empty; OpenAI rejects empty input) are
    skipped and left for a later routing pass once they're described."""
    routable = [post for post in posts if post.visual_description and post.visual_description.strip()]
    if not routable:
        return []

    blobs = [assemble_blob(post.visual_description) for post in routable]
    posts = routable

    vectors = np.array(embed_batch(blobs))  # (N, 1536)
    normalized_vectors = l2_normalize(vectors)

    scores = normalized_vectors @ world_matrix.T  # (N, 8) — every post's cosine vs. every world, one matmul

    results = []
    for i, post in enumerate(posts):
        order = np.argsort(scores[i])[::-1]
        winner_idx = order[0]
        runner_idx = order[1] if len(order) > 1 else None

        best_sim = float(scores[i, winner_idx])
        runner_up_sim = float(scores[i, runner_idx]) if runner_idx is not None else None
        margin = best_sim - (runner_up_sim or 0.0)

        results.append(
            {
                "post": post,
                "winning_world_id": world_ids[winner_idx],
                "post_vec": vectors[i].tolist(),
                "blob_text": blobs[i],
                "cosine": best_sim,
                "margin": margin,
                "runner_up_world_id": world_ids[runner_idx] if runner_idx is not None else None,
                "runner_up_cosine": runner_up_sim,
            }
        )

    return results


def persist_routing_batch(db, results: list[dict]) -> None:
    """Upserts a whole batch of routing results in one statement + one commit."""
    if not results:
        return

    rows = [
        {
            "post_id": r["post"].id,
            "account_id": r["post"].account_id,
            "world_id": r["winning_world_id"],
            "embedding": r["post_vec"],
            "posted_at": r["post"].posted_at,
            "blob_text": r["blob_text"],
            "cosine": r["cosine"],
            "margin": r["margin"],
        }
        for r in results
    ]
    db.execute(_upsert_stmt(rows))
    db.commit()

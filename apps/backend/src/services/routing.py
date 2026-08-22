"""Routes a Post to a World by cosine similarity, and persists the result.

Early fusion only: visual description + caption + top comments go into a single
text blob (see blob.py), one embedding call per post. No weighted fusion, no
per-modality vectors, no dedup — matching the scope of this phase.
"""

import uuid

import numpy as np

from src.db.models import Account, Post, World, WorldPost
from src.services.blob import assemble_blob
from src.services.embeddings import embed


def route_post(
    post: Post, worlds: list[World]
) -> tuple[uuid.UUID, list[float], float, str, float, list[tuple[uuid.UUID, float]]]:
    # 1. Assemble the blob — visual description + caption + top comments, budgeted
    comments = (post.comments or {}).get("comments", [])
    text_blob = assemble_blob(post.caption, comments, post.visual_description)

    # 2. Embed — one call
    post_vec = np.array(embed(text_blob))

    # 3. Cosine similarity against every world
    sims = []
    for world in worlds:
        world_vec = np.array(world.reference_embedding)
        cos_sim = np.dot(post_vec, world_vec) / (np.linalg.norm(post_vec) * np.linalg.norm(world_vec))
        sims.append((world.id, float(cos_sim)))

    # 4. Argmax wins; margin vs. runner-up is free since all 8 are already computed —
    #    kept for the abstain threshold, chosen later once there's labeled data.
    #    Full sorted list is returned too, e.g. for a per-world CSV column.
    sims.sort(key=lambda pair: pair[1], reverse=True)
    winning_world_id, best_sim = sims[0]
    runner_up_sim = sims[1][1] if len(sims) > 1 else 0.0
    margin = best_sim - runner_up_sim

    return winning_world_id, post_vec.tolist(), best_sim, text_blob, margin, sims


def persist_routing(
    db,
    post: Post,
    account: Account,
    winning_world_id: uuid.UUID,
    post_vec: list[float],
    engagement: float,
    blob_text: str,
    cosine: float,
    margin: float,
) -> None:
    db.add(
        WorldPost(
            post_id=post.id,
            account_id=account.id,
            world_id=winning_world_id,
            embedding=post_vec,
            engagement=engagement,  # raw snapshot, e.g. likes + 3*comments + 5*shares — NOT normalized here
            posted_at=post.posted_at,
            blob_text=blob_text,
            cosine=cosine,
            margin=margin,
        )
    )
    db.commit()

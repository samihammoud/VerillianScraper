"""Stage 4b: cluster an account's peak posts by embedding similarity, and
label each cluster from the VLM description already stored on each post.

Reuses topology.world_posts.embedding (unique on post_id, written by stage 3)
rather than re-embedding — clustering is a join, not a new API call. Peaks
with no row there simply aren't clustered; the caller counts and reports them
(it means `make route` needs running for those posts).

No scipy/sklearn: connected components over a cosine-similarity threshold is
enough at this scale — an account's peak set is tens of posts, not thousands
— and numpy is already a dependency.
"""

from collections import Counter

import numpy as np

from src.services.linalg import l2_normalize

DEFAULT_SIMILARITY_THRESHOLD = 0.70  # a starting guess — inspect the printed
# off-diagonal distribution against real data before trusting this number.


def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity for a small (N, 1536) matrix."""
    normalized = l2_normalize(embeddings)
    return normalized @ normalized.T


def off_diagonal_distribution(sim_matrix: np.ndarray) -> dict:
    """Summary stats over the off-diagonal similarities — print this before
    picking a cluster threshold rather than shipping a guessed number."""
    n = sim_matrix.shape[0]
    if n < 2:
        return {}
    values = sim_matrix[~np.eye(n, dtype=bool)]
    return {
        "min": float(values.min()),
        "p25": float(np.percentile(values, 25)),
        "median": float(np.median(values)),
        "p75": float(np.percentile(values, 75)),
        "max": float(values.max()),
    }


def cluster_by_threshold(sim_matrix: np.ndarray, threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> list[int]:
    """Connected components over the (sim_matrix >= threshold) graph. Returns
    a cluster id per row, same order as the input matrix."""
    n = sim_matrix.shape[0]
    cluster_ids = [-1] * n
    next_id = 0

    for start in range(n):
        if cluster_ids[start] != -1:
            continue
        cluster_ids[start] = next_id
        stack = [start]
        while stack:
            node = stack.pop()
            for neighbor in range(n):
                if cluster_ids[neighbor] == -1 and sim_matrix[node, neighbor] >= threshold:
                    cluster_ids[neighbor] = next_id
                    stack.append(neighbor)
        next_id += 1

    return cluster_ids


def label_cluster(parsed_fields: list[dict[str, str]]) -> dict:
    """PRODUCTS: kept verbatim per post — the specific form-factor phrasing is
    the useful part, not something to merge or normalize. CATEGORY CUES: split
    on commas and counted across the cluster, since that vocabulary is written
    to be comparable in a way PRODUCTS: isn't.

    Takes already-parsed fields (see vision.parse_visual_description) rather
    than raw visual_description strings — callers that need both a per-post
    view and a per-cluster label parse each post's text exactly once.
    """
    products = []
    cue_counts: Counter[str] = Counter()

    for fields in parsed_fields:
        if fields.get("PRODUCTS"):
            products.append(fields["PRODUCTS"])
        for cue in fields.get("CATEGORY CUES", "").split(","):
            cue = cue.strip()
            if cue:
                cue_counts[cue] += 1

    return {"products": products, "top_category_cues": cue_counts.most_common()}

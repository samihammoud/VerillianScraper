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


def product_names(vlm_json: dict | None) -> list[str]:
    """Renders each product as 'brand name' or bare name, verbatim per post —
    the specific form-factor phrasing is the useful part, not something to
    merge or normalize. Shared by label_cluster and per-row CSV fields so
    there's one place that knows the vlm_json product shape."""
    names = []
    for product in (vlm_json or {}).get("products") or []:
        name = (product or {}).get("name")
        if not name:
            continue
        brand = product.get("brand")
        names.append(f"{brand} {name}" if brand else name)
    return names


def topics(vlm_json: dict | None) -> list[str]:
    return [t.strip() for t in (vlm_json or {}).get("topics") or [] if t and t.strip()]


def label_cluster(vlm_jsons: list[dict]) -> dict:
    """products: kept verbatim per post (see product_names). topics: counted
    across the cluster, since that vocabulary is written to be comparable in
    a way product names aren't.

    Takes each post's raw vlm_json (vision_schema.py) directly — the VLM's
    structured output already has products/topics as first-class fields, no
    line-format parsing needed.
    """
    products = []
    topic_counts: Counter[str] = Counter()

    for obj in vlm_jsons:
        products.extend(product_names(obj))
        for topic in topics(obj):
            topic_counts[topic] += 1

    return {"products": products, "top_topics": topic_counts.most_common()}

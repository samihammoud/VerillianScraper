"""Sweeps clustering strategies for layer 1 (premise/situation clustering,
see CLAUDEphase9romanceoverview.md) to find one that produces a cluster count
targeted at THIS corpus's actual size, not the doc's ~10k-post reference.

First run (2026-09-03, world=romance): 5265 candidate posts, 5019
representatives after transcript-dedupe. The doc's "roughly 60-150 clusters
over ~10k posts" scales to roughly 30-75 for a corpus this size — that's the
target used below, not the doc's raw numbers.

`cluster_by_threshold` (connected components / single-link) was tried first
and ruled out: every threshold from 0.74-0.90 was either a couple of giant
blobs (a few borderline-similar bridge posts chain-merge unrelated situations
into one 100-200 post cluster) or near-total singleton fragmentation (84%+
singletons by 0.76, zero clusters passing the support floor by 0.86) — no
threshold in between produced a real spread of mid-sized clusters. That's
single-link clustering's classic chaining failure, not a bad threshold guess.

HDBSCAN (sklearn.cluster.HDBSCAN — already an installed dependency, used
elsewhere for /api/topology's TSNE projection; no new package needed) doesn't
chain the same way: it extracts clusters by density stability rather than one
global distance cutoff, so a few bridge points can't collapse two real
clusters into one. But run directly on raw 1536-dim embeddings it hit a
different, well-known failure: distance concentration in high dimensions
(every point is roughly equidistant from every other) made it see one
undifferentiated blob — min_cluster_size=4 produced a 4817-of-5019-point
cluster plus 194 noise points and nothing else. Standard fix, used by every
embed-then-HDBSCAN pipeline (BERTopic etc.): reduce dimensionality first.
PCA here rather than UMAP — already installed via scikit-learn, and a linear
reduction is enough to break the concentration problem without adding a
second new dependency to evaluate.

Embeds once (with a local .npy cache keyed by world+post-id set, since a
tuning sweep re-running the same embed call on every invocation is pure
waste) and re-clusters/re-reduces that same matrix under every parameter
setting.

Usage: python -m src.analysis.topology.phase9.tune_premise_threshold romance
"""

import hashlib
import sys
from pathlib import Path

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sqlalchemy import select

from src.db.models import Post, World, WorldPost
from src.db.session import SessionLocal
from src.services.embeddings import embed_batch
from src.services.linalg import l2_normalize
from src.analysis.topology.overview import _dedupe_map, _premise_text, world_by_slug

# Target scaled off the doc's "roughly 60-150 clusters over ~10k posts" —
# proportional to whatever corpus size this actually runs against, not a
# fixed number, since the doc's reference size and this world's real size
# rarely match.
TARGET_RATIO = (60 / 10_000, 150 / 10_000)

MIN_CLUSTER_SIZE_SWEEP = [5, 8, 10, 15, 20, 30]
PCA_DIMS_SWEEP = [20, 50, 100]  # raw 1536 dims degenerates HDBSCAN to one blob (see module docstring)

CACHE_DIR = Path(__file__).resolve().parents[4] / "out" / "phase9_cache"


def _candidate_posts(db, world: World) -> list[dict]:
    rows = db.execute(
        select(Post.id, Post.account_id, Post.views, Post.vlm_json)
        .join(WorldPost, WorldPost.post_id == Post.id)
        .where(WorldPost.world_id == world.id)
    ).all()
    posts = []
    for post_id, account_id, views, vlm_json in rows:
        text = _premise_text(vlm_json)
        if not text or not views:
            continue
        posts.append(
            {
                "post_id": post_id,
                "account_id": account_id,
                "text": text,
                "transcript": (vlm_json or {}).get("transcript"),
            }
        )
    return posts


def _cached_embed(world_slug: str, rep_ids: list, texts: list[str]) -> np.ndarray:
    """.npy cache keyed by a hash of the exact rep_id set — a tuning sweep
    re-run against the same routed/described data should never re-pay the
    embedding cost. Any change to the underlying posts (re-route, more VLM
    descriptions) changes the id set and naturally invalidates the cache."""
    key = hashlib.sha256(",".join(sorted(str(r) for r in rep_ids)).encode()).hexdigest()[:16]
    cache_path = CACHE_DIR / f"{world_slug}_premise_{key}.npy"
    if cache_path.exists():
        print(f"embedding cache hit: {cache_path}")
        return np.load(cache_path)

    print("embedding premise texts (one-time cost, cached for future sweeps)...")
    vectors = np.array(embed_batch(texts))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, vectors)
    return vectors


def _cluster_stats(labels: np.ndarray, account_ids: list, min_posts: int, min_accounts: int) -> dict:
    sizes: dict[int, int] = {}
    accounts_by_cluster: dict[int, set] = {}
    for label, account_id in zip(labels, account_ids):
        if label == -1:  # HDBSCAN noise label — not a cluster
            continue
        sizes[label] = sizes.get(label, 0) + 1
        accounts_by_cluster.setdefault(label, set()).add(account_id)

    passing = [sizes[c] for c in sizes if sizes[c] >= min_posts and len(accounts_by_cluster[c]) >= min_accounts]
    n_noise = int((labels == -1).sum())
    return {
        "n_clusters_total": len(sizes),
        "n_clusters_passing_floor": len(passing),
        "n_noise": n_noise,
        "largest_5": sorted(sizes.values(), reverse=True)[:5],
    }


def tune(world_slug: str) -> None:
    db = SessionLocal()
    try:
        world = world_by_slug(db, world_slug)
        posts = _candidate_posts(db, world)
        print(f"{len(posts)} candidate posts (have premise-or-fallback text + views)")

        dedupe_map = _dedupe_map(posts)
        by_post_id = {p["post_id"]: p for p in posts}
        rep_ids = sorted({rid for rid in dedupe_map.values()}, key=str)
        reps = [by_post_id[rid] for rid in rep_ids]
        n = len(reps)
        print(f"{n} representatives after transcript-dedupe (from {len(posts)} raw)")

        lo, hi = int(n * TARGET_RATIO[0]), int(n * TARGET_RATIO[1])
        print(f"target for this corpus size: {lo}-{hi} clusters (scaled from the doc's 60-150 / ~10k posts)\n")

        vectors = _cached_embed(world_slug, rep_ids, [p["text"] for p in reps])
        normalized = l2_normalize(vectors)  # euclidean on unit vectors ranks identically to cosine distance
        account_ids = [p["account_id"] for p in reps]

        for pca_dims in PCA_DIMS_SWEEP:
            reduced = PCA(n_components=pca_dims, random_state=0).fit_transform(normalized)
            print(f"\n-- PCA to {pca_dims} dims --")
            print(f"{'min_cluster_size':>18}  {'total':>7}  {'passing':>7}  {'noise':>7}  largest 5")
            for min_cluster_size in MIN_CLUSTER_SIZE_SWEEP:
                labels = HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean").fit_predict(reduced)
                stats = _cluster_stats(labels, account_ids, min_posts=8, min_accounts=5)
                flag = " <-- in target range" if lo <= stats["n_clusters_passing_floor"] <= hi else ""
                print(
                    f"{min_cluster_size:>18}  {stats['n_clusters_total']:>7}  {stats['n_clusters_passing_floor']:>7}  "
                    f"{stats['n_noise']:>7}  {stats['largest_5']}{flag}"
                )
    finally:
        db.close()


if __name__ == "__main__":
    world_slug = sys.argv[1] if len(sys.argv) > 1 else "romance"
    tune(world_slug)

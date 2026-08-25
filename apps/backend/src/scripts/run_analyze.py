"""Stage 4: peak analysis for one account.

account -> post history -> find the spikes -> cluster the spikes -> label the clusters

Pure compute over Postgres — reads what stages 1-3 already wrote (posts,
topology.world_posts) and calls no external API. No RapidAPI, no Gemini, no
OpenAI.

Usage: make analyze HANDLE=somehandle
   or: python -m src.scripts.run_analyze somehandle
"""

import sys
from collections import defaultdict

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Account, Post, WorldPost
from src.db.session import SessionLocal
from src.services.clustering import (
    DEFAULT_SIMILARITY_THRESHOLD,
    cluster_by_threshold,
    cosine_similarity_matrix,
    label_cluster,
    off_diagonal_distribution,
)
from src.services.csv_export import write_timestamped_csv
from src.services.peaks import Peak, compute_metrics, find_peaks
from src.services.vision import parse_visual_description

CSV_COLUMNS = [
    "cluster_id",
    "cluster_size",
    "cluster_products",
    "cluster_top_category_cues",
    "post_external_id",
    "video_url",
    "posted_at",
    "views",
    "metric_log1p_views",
    "modified_z",
    "products",
    "category_cues",
    "caption",
]


def _resolve_account(db: Session, handle: str) -> Account:
    # accounts.handle has no unique constraint and handles churn — filter on
    # the platform explicitly and fail loudly rather than silently picking one.
    accounts = (
        db.execute(select(Account).where(Account.handle == handle, Account.platform == "tiktok")).scalars().all()
    )
    if not accounts:
        print(f"FATAL: no tiktok account found for handle {handle!r}", file=sys.stderr)
        raise SystemExit(1)
    if len(accounts) > 1:
        print(f"FATAL: {len(accounts)} tiktok accounts found for handle {handle!r} — ambiguous", file=sys.stderr)
        raise SystemExit(1)
    return accounts[0]


def _cluster_peaks(clustered: list[Peak], embedding_by_post_id: dict, parsed_by_post_id: dict) -> tuple[dict, dict]:
    """Clusters peaks with a routing embedding and labels each cluster.

    Returns (cluster_id_by_post_id, cluster_labels). Builds cluster membership
    in one pass alongside the id assignment, rather than assigning ids first
    and then re-scanning `clustered` once per distinct cluster id to find members.
    """
    if not clustered:
        return {}, {}

    if len(clustered) == 1:
        members_by_cluster = {0: [clustered[0].post]}
    else:
        embeddings = np.array([embedding_by_post_id[peak.post.id] for peak in clustered])
        sim_matrix = cosine_similarity_matrix(embeddings)
        print(f"off-diagonal cosine distribution: {off_diagonal_distribution(sim_matrix)}")

        cluster_ids = cluster_by_threshold(sim_matrix, DEFAULT_SIMILARITY_THRESHOLD)
        members_by_cluster = defaultdict(list)
        for peak, cluster_id in zip(clustered, cluster_ids):
            members_by_cluster[cluster_id].append(peak.post)

    cluster_id_by_post_id = {
        post.id: cluster_id for cluster_id, members in members_by_cluster.items() for post in members
    }
    cluster_labels = {}
    for cluster_id, members in members_by_cluster.items():
        label = label_cluster([parsed_by_post_id[post.id] for post in members])
        label["size"] = len(members)
        cluster_labels[cluster_id] = label

    return cluster_id_by_post_id, cluster_labels


def _build_rows(
    handle: str,
    peaks: list[Peak],
    parsed_by_post_id: dict,
    cluster_id_by_post_id: dict,
    cluster_labels: dict,
) -> list[dict]:
    rows = []
    for post, metric, modified_z in peaks:
        cluster_id = cluster_id_by_post_id.get(post.id, "unclustered")
        label = cluster_labels.get(cluster_id, {})
        own_fields = parsed_by_post_id[post.id]

        rows.append(
            {
                "cluster_id": cluster_id,
                "cluster_size": label.get("size", 1),
                "cluster_products": " | ".join(label.get("products", [])),
                "cluster_top_category_cues": " | ".join(
                    f"{cue} ({count})" for cue, count in label.get("top_category_cues", [])
                ),
                "post_external_id": post.external_id,
                "video_url": f"https://www.tiktok.com/@{handle}/video/{post.external_id}",
                "posted_at": post.posted_at.isoformat() if post.posted_at else None,
                "views": post.views,
                "metric_log1p_views": round(metric, 4),
                "modified_z": round(modified_z, 4) if modified_z is not None else None,
                "products": own_fields.get("PRODUCTS"),
                "category_cues": own_fields.get("CATEGORY CUES"),
                "caption": post.caption,
            }
        )

    return rows


def _write_csv(handle: str, rows: list[dict]):
    rows = sorted(rows, key=lambda r: (str(r["cluster_id"]), -(r["views"] or 0)))
    return write_timestamped_csv(f"peaks_{handle}", CSV_COLUMNS, rows)


def run_analyze(handle: str):
    db = SessionLocal()
    try:
        account = _resolve_account(db, handle)
        posts = db.execute(select(Post).where(Post.account_id == account.id)).scalars().all()

        posts_with_metrics = compute_metrics(posts)
        peaks, info = find_peaks(posts_with_metrics)

        print(f"{handle}: {info['n_total']} posts with usable views, {info['n_peaks']} peaks ({info['method']})")
        if info["cutoff_views"] is not None:
            print(f"peak = above {info['cutoff_views']:,} views")

        if not peaks:
            print("no peaks found")
            return _write_csv(handle, [])

        # Parsed once per peak and reused everywhere the VLM fields are needed
        # (cluster labeling, per-row CSV fields, the none-visible rate below)
        # instead of re-parsing the same string in more than one place.
        parsed_by_post_id = {peak.post.id: parse_visual_description(peak.post.visual_description) for peak in peaks}

        n_none_visible = sum(1 for fields in parsed_by_post_id.values() if fields.get("PRODUCTS") == "none visible")
        print(f"PRODUCTS: none visible rate among peaks: {n_none_visible}/{len(peaks)}")

        peak_ids = [peak.post.id for peak in peaks]
        world_posts = db.execute(select(WorldPost).where(WorldPost.post_id.in_(peak_ids))).scalars().all()
        embedding_by_post_id = {wp.post_id: wp.embedding for wp in world_posts}

        clustered = [peak for peak in peaks if peak.post.id in embedding_by_post_id]
        n_unclustered = len(peaks) - len(clustered)
        print(f"{len(clustered)}/{len(peaks)} peaks have a routing embedding ({n_unclustered} not routed — run `make route`)")

        cluster_id_by_post_id, cluster_labels = _cluster_peaks(clustered, embedding_by_post_id, parsed_by_post_id)

        rows = _build_rows(handle, peaks, parsed_by_post_id, cluster_id_by_post_id, cluster_labels)
        path = _write_csv(handle, rows)
        print(f"wrote {len(rows)} rows to {path}")

        print("\nClusters:")
        for cluster_id, label in sorted(cluster_labels.items(), key=lambda kv: -kv[1]["size"]):
            print(f"  cluster {cluster_id} ({label['size']} posts): {label['products']}")
            print(f"    top category cues: {label['top_category_cues']}")

        return path
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("usage: run_analyze.py handle", file=sys.stderr)
        raise SystemExit(1)

    run_analyze(sys.argv[1].strip())

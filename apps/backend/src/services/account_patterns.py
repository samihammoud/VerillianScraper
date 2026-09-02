"""World-scoped view over stage 4 (peaks.py + clustering.py): for every
account with at least one post routed into a world, find that account's view
peaks and cluster them by embedding similarity, same as run_analyze.py's CLI
path. A "pattern" is a cluster of >=2 peaks — a single isolated peak has no
semantic overlap to report, so accounts with no qualifying cluster are
dropped rather than shown empty-handed.

Pure compute over already-stored data (posts, topology.world_posts) — no
persistence, no API calls, cheap enough to run per request at this scale (an
account's peak set is tens of posts).
"""

from collections import defaultdict

import numpy as np
from sqlalchemy import select

from src.db.models import Account, Post, WorldPost
from src.services.clustering import DEFAULT_SIMILARITY_THRESHOLD, cluster_by_threshold, cosine_similarity_matrix, label_cluster
from src.services.peaks import compute_metrics, find_peaks


def _account_video_url(handle: str, external_id: str) -> str:
    return f"https://www.tiktok.com/@{handle}/video/{external_id}"


def account_patterns(db, world) -> list[dict]:
    account_rows = db.execute(
        select(Account.id, Account.handle)
        .join(WorldPost, WorldPost.account_id == Account.id)
        .where(WorldPost.world_id == world.id)
        .distinct()
    ).all()

    out = []
    for account_id, handle in account_rows:
        posts = db.execute(select(Post).where(Post.account_id == account_id)).scalars().all()
        peaks, _info = find_peaks(compute_metrics(posts))
        if len(peaks) < 2:
            continue

        peak_ids = [peak.post.id for peak in peaks]
        embedding_by_post_id = {
            wp.post_id: wp.embedding
            for wp in db.execute(select(WorldPost).where(WorldPost.post_id.in_(peak_ids))).scalars()
        }
        clustered = [peak for peak in peaks if peak.post.id in embedding_by_post_id]
        if len(clustered) < 2:
            continue

        embeddings = np.array([embedding_by_post_id[peak.post.id] for peak in clustered])
        cluster_ids = cluster_by_threshold(cosine_similarity_matrix(embeddings), DEFAULT_SIMILARITY_THRESHOLD)

        members_by_cluster = defaultdict(list)
        for peak, cluster_id in zip(clustered, cluster_ids):
            members_by_cluster[cluster_id].append(peak)

        patterns = []
        for members in members_by_cluster.values():
            if len(members) < 2:
                continue
            label = label_cluster([m.post.vlm_json for m in members])
            patterns.append(
                {
                    "size": len(members),
                    "products": label["products"],
                    "top_topics": label["top_topics"],
                    "posts": [
                        {
                            "video_url": _account_video_url(handle, m.post.external_id),
                            "views": m.post.views,
                            "caption": m.post.caption,
                        }
                        for m in sorted(members, key=lambda m: -(m.post.views or 0))
                    ],
                }
            )

        if patterns:
            patterns.sort(key=lambda p: -p["size"])
            out.append({"handle": handle, "n_peaks": len(peaks), "patterns": patterns})

    out.sort(key=lambda a: -max(p["size"] for p in a["patterns"]))
    return out

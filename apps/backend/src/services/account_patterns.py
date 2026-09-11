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

from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import select

from src.db.models import Account, Annotation, Post, WorldPost
from src.services.clustering import DEFAULT_SIMILARITY_THRESHOLD, cluster_by_threshold, cosine_similarity_matrix, label_cluster
from src.services.peaks import compute_metrics, find_peaks


def _account_video_url(handle: str, external_id: str) -> str:
    return f"https://www.tiktok.com/@{handle}/video/{external_id}"


def _modal(vlm_jsons: list[dict], field: str) -> str | None:
    """Most common non-filler value of a single-string vlm_json field."""
    counts = Counter(
        value.strip()
        for obj in vlm_jsons
        if (value := ((obj or {}).get(field) or "").strip()) and value not in ("not_applicable", "unclear")
    )
    return counts.most_common(1)[0][0] if counts else None


def _about(members: list, label: dict) -> str:
    """One line saying what this peak cluster is regarding. Assembled from the
    fields every vlm_schema.json shares (format/setting/topics) plus the
    top-performing member's own summary — no LLM call, no new storage."""
    vlm_jsons = [m.post.vlm_json for m in members]
    parts = [_modal(vlm_jsons, "format"), _modal(vlm_jsons, "setting")]
    shape = " · ".join(filter(None, parts))
    topics = ", ".join(term for term, _ in label["top_topics"][:3] if term not in parts)
    summary = ((members[0].post.vlm_json or {}).get("summary") or "").strip()
    return " — ".join(filter(None, [shape, topics, summary]))


def account_patterns(db, world) -> list[dict]:
    account_rows = db.execute(
        select(Account.id, Account.handle)
        .join(WorldPost, WorldPost.account_id == Account.id)
        .where(WorldPost.world_id == world.id)
        .distinct()
    ).all()

    annotations = {a.key: a for a in db.execute(select(Annotation)).scalars()}

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
            members = sorted(members, key=lambda m: -(m.post.views or 0))
            label = label_cluster([m.post.vlm_json for m in members])
            anchor = min(str(m.post.id) for m in members)
            annotation = annotations.get(anchor)
            patterns.append(
                {
                    "key": anchor,
                    "note": annotation.note if annotation else "",
                    "favorite": bool(annotation and annotation.favorite),
                    "reviewed": bool(annotation and annotation.reviewed),
                    "hidden": bool(annotation and annotation.hidden),
                    "sort_order": annotation.sort_order if annotation else 0,
                    "about": _about(members, label),
                    "size": len(members),
                    "products": label["products"],
                    "top_topics": label["top_topics"],
                    "posts": [
                        {
                            "video_url": _account_video_url(handle, m.post.external_id),
                            "views": m.post.views,
                            "caption": m.post.caption,
                        }
                        for m in members
                    ],
                }
            )

        if patterns:
            patterns.sort(key=lambda p: (not p["favorite"], p["sort_order"], -p["size"]))
            out.append({"handle": handle, "n_peaks": len(peaks), "patterns": patterns})

    # Accounts holding a favourited pattern float to the top, then by strongest
    # visible pattern — so starring is what reorders the account list.
    out.sort(
        key=lambda a: (
            not any(p["favorite"] for p in a["patterns"]),
            -max((p["size"] for p in a["patterns"] if not p["hidden"]), default=0),
        )
    )
    return out

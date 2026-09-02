"""Phase 7 stage 1-2: per-world term rollup — the thing that answers "what is
winning in this world".

Unit of analysis is the term, not the post (see the handoff doc): embeddings
are used one level down from routing, to decide two term strings are the same
term, not to decide which posts belong together. rollup() is the one command
that (re)builds topology.world_term_stats for a world; canonicalize() is the
embed+cluster half of it, split out because phase 1 (exact-normalization-only)
intentionally runs without it.
"""

from collections import defaultdict

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from src.db.models import Post, PostTerm, TermEmbedding, World, WorldPost, WorldTermStat
from src.services.clustering import cluster_by_threshold, cosine_similarity_matrix
from src.services.embeddings import embed_batch
from src.services.terms import CLUSTERED_FACETS, RAW_FACETS, extract_scrape_terms, extract_terms, normalize

CLUSTER_THRESHOLD = 0.86  # tuned once in phase 2 by eyeballing merges against `variants`
MIN_POSTS = 5
MIN_ACCOUNTS = 3
MEDIAN_LIFT_TOLERANCE = 1.0  # log-space; see rollup()'s assert for what a violation means


def world_by_slug(db, world_slug: str) -> World:
    world = db.execute(select(World).where(World.slug == world_slug)).scalar_one_or_none()
    if world is None:
        raise ValueError(f"no world with slug {world_slug!r}")
    return world


def _extract_and_store(db, world: World) -> list[tuple]:
    """Steps 1-2: pull this world's posts, extract_terms (VLM-derived) +
    extract_scrape_terms (TikTok's own music_info metadata) per post, replace
    this world's post_terms wholesale."""
    posts = db.execute(
        select(
            Post.id, Post.account_id, Post.views, Post.vlm_json, Post.music_original, Post.music_author
        )
        .join(WorldPost, WorldPost.post_id == Post.id)
        .where(WorldPost.world_id == world.id)
    ).all()

    db.execute(delete(PostTerm).where(PostTerm.world_id == world.id))

    rows = []
    for post_id, account_id, views, vlm_json, music_original, music_author in posts:
        metric = float(np.log1p(views)) if views else None
        post_terms = extract_terms(vlm_json) + extract_scrape_terms(music_original, music_author)
        for term in post_terms:
            norm_term = term.raw_term if term.facet in RAW_FACETS else normalize(term.raw_term)
            rows.append(
                {
                    "post_id": post_id,
                    "world_id": world.id,
                    "account_id": account_id,
                    "facet": term.facet,
                    "raw_term": term.raw_term,
                    "norm_term": norm_term,
                    "canon_term": norm_term,  # phase-1 default; canonicalize() overwrites for clustered facets
                    "prominence": term.prominence,
                    "metric": metric,
                    "low_conf": term.low_conf,
                }
            )

    if rows:
        db.execute(insert(PostTerm).values(rows))
    db.commit()
    return posts


def _canonicalize_facet(db, world: World, facet: str, norm_term_counts: dict[str, int]) -> None:
    """Stage B for one (world, facet): embed the distinct norm_terms (cache
    hit for anything seen before, across any world), single-link agglomerative
    cluster at CLUSTER_THRESHOLD, canonical label = highest-post-count member.
    """
    norm_terms = sorted(norm_term_counts)
    if not norm_terms:
        return

    cached = {
        row.norm_term: row.embedding
        for row in db.execute(select(TermEmbedding).where(TermEmbedding.norm_term.in_(norm_terms))).scalars()
    }
    missing = [t for t in norm_terms if t not in cached]
    if missing:
        vectors = embed_batch(missing)
        db.execute(
            insert(TermEmbedding)
            .values([{"norm_term": t, "facet": facet, "embedding": v} for t, v in zip(missing, vectors)])
            .on_conflict_do_nothing(index_elements=["norm_term"])
        )
        db.commit()
        cached.update(zip(missing, vectors))

    matrix = np.array([cached[t] for t in norm_terms])
    if len(norm_terms) == 1:
        cluster_ids = [0]
    else:
        cluster_ids = cluster_by_threshold(cosine_similarity_matrix(matrix), CLUSTER_THRESHOLD)

    members_by_cluster: dict[int, list[str]] = defaultdict(list)
    for term, cid in zip(norm_terms, cluster_ids):
        members_by_cluster[cid].append(term)

    for members in members_by_cluster.values():
        canon = max(members, key=lambda t: norm_term_counts[t])
        if len(members) == 1:
            continue  # canon_term already == norm_term from _extract_and_store
        db.execute(
            PostTerm.__table__.update()
            .where(PostTerm.world_id == world.id, PostTerm.facet == facet, PostTerm.norm_term.in_(members))
            .values(canon_term=canon)
        )
    db.commit()


def canonicalize(db, world: World) -> None:
    """Stage B over every clustered facet for this world. No-op for
    brand (normalize-only) and cta/audio_kind (raw enums) — their canon_term
    was already set correctly in _extract_and_store."""
    rows = db.execute(
        select(PostTerm.facet, PostTerm.norm_term, PostTerm.post_id)
        .where(PostTerm.world_id == world.id, PostTerm.facet.in_(CLUSTERED_FACETS))
    ).all()

    counts_by_facet: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for facet, norm_term, _post_id in rows:
        counts_by_facet[facet][norm_term] += 1

    for facet, norm_term_counts in counts_by_facet.items():
        _canonicalize_facet(db, world, facet, norm_term_counts)


def account_medians(db, world: World) -> dict:
    """Per-account baseline: median log1p(views) over each account's own
    posts in this world (same eligible population as world_median — must
    have vlm_json and views). Used to compute account_lift, which asks 'does
    this term outperform for the accounts that actually post it', instead of
    'does it outperform the whole world' — a term can look like a winner on
    world lift purely because one viral account happens to feature it a lot."""
    rows = db.execute(
        select(Post.account_id, Post.views, Post.vlm_json)
        .join(WorldPost, WorldPost.post_id == Post.id)
        .where(WorldPost.world_id == world.id)
    ).all()
    by_account: dict = defaultdict(list)
    for account_id, views, vlm_json in rows:
        if vlm_json and views:
            by_account[account_id].append(float(np.log1p(views)))
    return {account_id: float(np.median(metrics)) for account_id, metrics in by_account.items()}


def _aggregate(db, world: World, world_median: float, account_median: dict) -> list[dict]:
    """Step 6: group post_terms by (facet, canon_term), apply the support
    floor, compute lift. Incidental products are excluded entirely from the
    default product ranking — a lamp that happens to be in frame is not what
    is winning."""
    rows = db.execute(
        select(
            PostTerm.facet,
            PostTerm.canon_term,
            PostTerm.post_id,
            PostTerm.account_id,
            PostTerm.prominence,
            PostTerm.metric,
            PostTerm.raw_term,
        ).where(PostTerm.world_id == world.id, PostTerm.low_conf.is_(False), PostTerm.metric.isnot(None))
    ).all()

    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"post_ids": set(), "account_ids": set(), "n_hero": 0, "metrics": [], "account_deltas": [], "variants": set()}
    )
    for facet, canon_term, post_id, account_id, prominence, metric, raw_term in rows:
        if facet == "product" and prominence == "incidental":
            continue
        g = groups[(facet, canon_term)]
        g["post_ids"].add(post_id)
        g["account_ids"].add(account_id)
        if prominence == "hero":
            g["n_hero"] += 1
        g["metrics"].append(metric)
        if account_id in account_median:
            g["account_deltas"].append(metric - account_median[account_id])
        g["variants"].add(raw_term)

    stats = []
    for (facet, canon_term), g in groups.items():
        n_posts = len(g["post_ids"])
        n_accounts = len(g["account_ids"])
        if n_posts < MIN_POSTS or n_accounts < MIN_ACCOUNTS:
            continue
        median_m = float(np.median(g["metrics"]))
        lift = median_m - world_median
        account_lift = float(np.median(g["account_deltas"])) if g["account_deltas"] else 0.0
        stats.append(
            {
                "world_id": world.id,
                "facet": facet,
                "canon_term": canon_term,
                "n_posts": n_posts,
                "n_accounts": n_accounts,
                "n_hero": g["n_hero"],
                "median_m": median_m,
                "lift": lift,
                "view_ratio": float(np.exp(lift)),
                "account_lift": account_lift,
                "account_view_ratio": float(np.exp(account_lift)),
                "variants": sorted(g["variants"]),
            }
        )
    return stats


def world_median(db, world: World) -> float:
    """Median m=log1p(views) restricted to posts that HAVE vlm_json (the same
    population terms are drawn from) — a baseline over the full post set
    (including posts never VLM-described) is a different claim and biases
    every lift. Shared by rollup() and the /overview endpoint's coverage
    block so both report the same number."""
    rows = db.execute(
        select(Post.views, Post.vlm_json)
        .join(WorldPost, WorldPost.post_id == Post.id)
        .where(WorldPost.world_id == world.id)
    ).all()
    eligible = [float(np.log1p(views)) for views, vlm_json in rows if vlm_json and views]
    return float(np.median(eligible)) if eligible else 0.0


def rollup(db, world_slug: str) -> list[dict]:
    """The one entry point. Returns the stats rows it wrote, for callers
    (run_overview.py) that want to print a summary without a second query."""
    world = world_by_slug(db, world_slug)

    _extract_and_store(db, world)
    canonicalize(db, world)

    median = world_median(db, world)
    by_account = account_medians(db, world)
    stats = _aggregate(db, world, median, by_account)

    if stats:
        median_lift = float(np.median([s["lift"] for s in stats]))
        print(f"  median lift across {len(stats)} ranked terms: {median_lift:+.4f}")
        assert abs(median_lift) < MEDIAN_LIFT_TOLERANCE, (
            f"median lift {median_lift:+.4f} is far from zero — baseline is likely computed "
            "over a different post set than the terms (see rollup()'s world_median comment)"
        )

    db.execute(delete(WorldTermStat).where(WorldTermStat.world_id == world.id))
    if stats:
        db.execute(insert(WorldTermStat).values(stats))
    db.commit()

    return stats

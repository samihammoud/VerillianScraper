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
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from src.db.models import Post, PostTerm, TermEmbedding, World, WorldPost, WorldTermStat
from src.services.clustering import cluster_by_threshold, cosine_similarity_matrix
from src.services.embeddings import embed_batch
from src.services.linalg import l2_normalize
from src.services.terms import CLUSTERED_FACETS, RAW_FACETS, extract_scrape_terms, extract_terms, normalize

CLUSTER_THRESHOLD = 0.86  # tuned once in phase 2 by eyeballing merges against `variants`
MIN_POSTS = 5
MIN_ACCOUNTS = 3

# Phase 9 layer 1 — premise/situation clustering (see CLAUDEphase9romanceoverview.md).
# Sentences, not noun phrases, so CLUSTER_THRESHOLD (tuned for short terms) does not
# apply here — this starts as a rough guess and needs eyeballing per the doc's own
# verification step (read 20 premises from a cluster, confirm same situation) before
# it's trustworthy. Lower than CLUSTER_THRESHOLD on purpose: full sentences about the
# same situation paraphrase more than noun phrases do, so a 0.86 bar would fragment
# into near-singletons.
PREMISE_DEDUPE_THRESHOLD = 0.97  # reposted/re-voiced clips, per the doc — collapse before counting
PREMISE_MIN_POSTS = 8
PREMISE_MIN_ACCOUNTS = 5  # raised vs. MIN_ACCOUNTS: farmed romance content needs a harder floor

# Swept twice in src/services/phase9/tune_premise_threshold.py against the real romance corpus
# (2026-09-03). cluster_by_threshold (single-link/connected-components) was tried first and
# ruled out — every threshold from 0.74-0.90 was either a couple of giant blobs (bridge posts
# chain-merging distinct situations into one 100-200 post cluster) or near-total singleton
# fragmentation, with no usable middle ground. HDBSCAN directly on raw 1536-dim embeddings
# degenerated to one blob too (distance concentration in high dimensions) — PCA reduction fixes
# that. First sweep (before _premise_text excluded relationship.stage=="not_applicable" posts)
# picked PCA-50/min_cluster_size=8; re-swept after that fix changed the candidate pool's density
# landscape enough to need reselecting — PCA-100/min_cluster_size=8 was the best of the second
# sweep (10 clusters, sizes [458, 262, 56, 32, 31, ...], no single dominant blob). Both sweeps
# plateaued around 10-15 clusters across every config tried, well under the doc's 30-75 target
# for this corpus size — treated as a property of the data (premise sentences form a fairly
# continuous space of situations, not many tight islands) rather than a tuning miss at this
# point; see the sweep script's docstring for the full comparison.
PREMISE_PCA_DIMS = 100
PREMISE_MIN_CLUSTER_SIZE = 8

# HDBSCAN sweeps whatever it can't place into the nearest component rather than
# calling it noise, which produces a residual blob whose medoid names nothing.
# Real case, 2026-09-04: a 154-post hook_cluster labeled "Hey real quick!" — a
# string exactly ONE post in the world actually had — holding Hindi dialogue,
# boxing-match announcements, "Aura" and "3". Measured mean pairwise cosine
# 0.168 against 0.397-0.587 for every real cluster, so members are pruned
# against the medoid before the support floor below is applied, and a blob
# collapses under PREMISE_MIN_POSTS on its own instead of needing a second
# reject rule. Swept on the romance corpus: 0.35 cuts that blob 154 -> 6 (dead)
# while costing real clusters 2-6% of their members; 0.45 starts killing
# legitimate clusters outright.
MEMBER_COSINE_FLOOR = 0.35
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

    # Scoped to these post_ids, not to world_id: PostTerm's PK is (post_id, facet,
    # raw_term) with no world_id in it, and routing is re-runnable — a post can
    # switch worlds between runs (WorldPost upserts by post_id). Deleting only
    # `WHERE world_id = this world` leaves a migrated post's old-world rows in
    # place, colliding with the fresh insert on that PK the moment it lands here.
    post_ids = [post_id for post_id, *_ in posts]
    if post_ids:
        db.execute(delete(PostTerm).where(PostTerm.post_id.in_(post_ids)))

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


def _premise_text(vlm_json: dict | None) -> str | None:
    """Embed `premise` alone when present; fall back to summary+topics only
    for posts described under the pre-relationship-layer schema (no
    `relationship` key at all — see CLAUDE.md's overview section on
    ACTIVE_SCHEMA_WORLD), never for a post the VLM explicitly judged isn't
    about a relationship (`relationship.stage == "not_applicable"`, a real
    signal, not a missing one). Without this distinction, non-relationship
    content routed into the romance world (Rust gameplay, disaster
    animations — real incident, 2026-09-03) clustered on summary/topic
    similarity and surfaced in the Situations panel as if it were a romance
    finding. Never the transcript: verbatim dialogue drags the vector toward
    wording, not situation, and two videos about the same premise with
    different scripts must land in the same cluster."""
    if not vlm_json:
        return None
    if premise := vlm_json.get("premise"):
        return premise.strip()
    relationship = vlm_json.get("relationship")
    if relationship is not None and relationship.get("stage") == "not_applicable":
        return None
    summary = vlm_json.get("summary") or ""
    topics = " ".join(vlm_json.get("topics") or [])
    combined = f"{summary} {topics}".strip()
    return combined or None


def _dedupe_map(posts: list[dict]) -> dict:
    """post_id -> representative post_id, in two stages.

    Stage 1 collapses exact-text duplicates first. This matters beyond being
    a free win on premise/hook/punchline (an exact repeat is trivially the
    same situation): `setting_cluster` (phase 11) embeds a short, highly
    repeated controlled-vocabulary field, unlike premise's near-unique
    sentences — without this, two distinct HDBSCAN clusters can each pick the
    identical string as their medoid and collide on WorldTermStat's PK
    (world_id, facet, canon_term). Collapsing to one representative per exact
    string before clustering makes that structurally impossible: each string
    then belongs to at most one cluster.

    Stage 2 is the original near-duplicate pass over transcripts (reposted/
    re-voiced clips — same situation, different wording), run only over the
    stage-1 representatives. Posts with no transcript aren't deduped further
    here and stay singleton groups."""
    text_rep: dict[str, str] = {}
    stage1 = {p["post_id"]: text_rep.setdefault(p["text"], p["post_id"]) for p in posts}

    reps = [p for p in posts if stage1[p["post_id"]] == p["post_id"]]
    with_transcript = [p for p in reps if p.get("transcript")]
    stage2 = {p["post_id"]: p["post_id"] for p in reps}
    if len(with_transcript) >= 2:
        vectors = np.array(embed_batch([p["transcript"] for p in with_transcript]))
        sim = cosine_similarity_matrix(vectors)
        cluster_ids = cluster_by_threshold(sim, threshold=PREMISE_DEDUPE_THRESHOLD)

        rep_by_cluster: dict[int, str] = {}
        for post, cid in zip(with_transcript, cluster_ids):
            rep = rep_by_cluster.setdefault(cid, post["post_id"])
            stage2[post["post_id"]] = rep

    return {pid: stage2[stage1[pid]] for pid in stage1}


def _setting_text(vlm_json: dict | None) -> str | None:
    if not vlm_json:
        return None
    setting = vlm_json.get("setting")
    return setting.strip() if setting else None


def _hook_text(vlm_json: dict | None) -> str | None:
    if not vlm_json:
        return None
    hook = vlm_json.get("hook")
    return hook.strip() if hook else None


def _punchline_text(vlm_json: dict | None) -> str | None:
    if not vlm_json:
        return None
    punchline = vlm_json.get("punchline")
    return punchline.strip() if punchline else None


def _text_field_clusters(db, world: World, world_median: float, facet: str, text_fn) -> tuple[list[dict], list[dict]]:
    """Generic embed+cluster+rank over one free-text vlm_json field — shared
    by premise (layer 1), hook, and punchline (layer 3) clustering, since all
    three are the same pipeline over a different field: pull candidate posts,
    dedupe reposts, PCA+HDBSCAN, label by medoid, rank by lift. Returns
    (world_term_stats rows, post_terms rows) for the given facet — same
    shape the rest of the pipeline writes, per the doc's own note that this
    needs no new schema through step 3. canon_term is the medoid sentence,
    not a normalized phrase."""
    rows = db.execute(
        select(Post.id, Post.account_id, Post.views, Post.vlm_json)
        .join(WorldPost, WorldPost.post_id == Post.id)
        .where(WorldPost.world_id == world.id)
    ).all()

    posts = []
    for post_id, account_id, views, vlm_json in rows:
        text = text_fn(vlm_json)
        if not text or not views:
            continue
        posts.append(
            {
                "post_id": post_id,
                "account_id": account_id,
                "text": text,
                "transcript": (vlm_json or {}).get("transcript"),
                "metric": float(np.log1p(views)),
            }
        )
    if len(posts) < 2:
        return [], []

    dedupe_map = _dedupe_map(posts)
    by_post_id = {p["post_id"]: p for p in posts}
    rep_ids = sorted({rid for rid in dedupe_map.values()}, key=str)
    reps = [by_post_id[rid] for rid in rep_ids]
    rep_index = {rid: i for i, rid in enumerate(rep_ids)}

    vectors = np.array(embed_batch([p["text"] for p in reps]))
    normalized = l2_normalize(vectors)
    sim = cosine_similarity_matrix(vectors)  # kept at full dimensionality — medoid/variant
    # selection is a small per-cluster lookup, not the clustering step itself, so it doesn't
    # inherit the high-dimensional density problem PCA below is working around.
    reduced = PCA(n_components=min(PREMISE_PCA_DIMS, len(reps) - 1), random_state=0).fit_transform(normalized)
    cluster_ids = HDBSCAN(min_cluster_size=PREMISE_MIN_CLUSTER_SIZE, metric="euclidean").fit_predict(reduced)
    cluster_by_rep_id = dict(zip(rep_ids, cluster_ids))

    members_by_cluster: dict[int, list[dict]] = defaultdict(list)
    for rep, cid in zip(reps, cluster_ids):
        if cid == -1:  # HDBSCAN noise label — not a cluster, leave unclustered rather than force-merge
            continue
        members_by_cluster[cid].append(rep)

    term_stats = []
    canon_by_cluster: dict[int, str] = {}
    kept_by_cluster: dict[int, set] = {}
    for cid, members in members_by_cluster.items():
        idxs = [rep_index[m["post_id"]] for m in members]
        sub_sim = sim[np.ix_(idxs, idxs)]
        medoid_local = int(np.argmax(sub_sim.sum(axis=1)))

        # Drop members that aren't actually close to the medoid, then let the
        # support floor below judge what's left — see MEMBER_COSINE_FLOOR. The
        # medoid scores 1.0 against itself, so `keep` is never empty.
        keep = [i for i in range(len(members)) if sub_sim[medoid_local, i] >= MEMBER_COSINE_FLOOR]
        members = [members[i] for i in keep]
        sub_sim = sub_sim[np.ix_(keep, keep)]
        medoid_local = keep.index(medoid_local)

        account_ids = {m["account_id"] for m in members}
        if len(members) < PREMISE_MIN_POSTS or len(account_ids) < PREMISE_MIN_ACCOUNTS:
            continue

        median_m = float(np.median([m["metric"] for m in members]))
        lift = median_m - world_median
        medoid_text = members[medoid_local]["text"]
        order = np.argsort(-sub_sim[medoid_local])
        variants = []
        for j in order:
            candidate = members[j]["text"]
            if candidate != medoid_text and candidate not in variants:
                variants.append(candidate)
            if len(variants) >= 5:
                break

        canon_by_cluster[cid] = medoid_text
        kept_by_cluster[cid] = {m["post_id"] for m in members}
        term_stats.append(
            {
                "world_id": world.id,
                "facet": facet,
                "canon_term": medoid_text,
                "n_posts": len(members),
                "n_accounts": len(account_ids),
                "n_hero": 0,
                "median_m": median_m,
                "lift": lift,
                "view_ratio": float(np.exp(lift)),
                "account_lift": 0.0,  # not computed for embedding-cluster facets — see doc's layer 1 vs. account-floor note
                "account_view_ratio": 1.0,
                "variants": variants,
            }
        )

    # Tag every original post (including deduped-out reposts) for drill-down,
    # not just the representatives counted above — a viewer browsing a
    # cluster should see every matching video, only the *count* excludes reposts.
    post_term_rows = []
    for p in posts:
        rep_id = dedupe_map[p["post_id"]]
        cid = cluster_by_rep_id.get(rep_id)
        if cid is None or cid not in canon_by_cluster or rep_id not in kept_by_cluster[cid]:
            continue
        post_term_rows.append(
            {
                "post_id": p["post_id"],
                "world_id": world.id,
                "account_id": p["account_id"],
                "facet": facet,
                "raw_term": p["text"][:2000],  # PostTerm.raw_term is part of the PK — guard pathological length
                "norm_term": canon_by_cluster[cid],
                "canon_term": canon_by_cluster[cid],
                "prominence": None,
                "metric": p["metric"],
                "low_conf": False,
            }
        )
    return term_stats, post_term_rows


def _premise_clusters(db, world: World, world_median: float) -> tuple[list[dict], list[dict]]:
    """Phase 9 layer 1 — situation space. See _text_field_clusters."""
    return _text_field_clusters(db, world, world_median, "premise_cluster", _premise_text)


def _setting_clusters(db, world: World, world_median: float) -> tuple[list[dict], list[dict]]:
    """Phase 11 analysis v1 (CLAUDEphase11romance-ai-world) — situation space's
    other half. Unlike premise, `setting` is already a normal CLUSTERED_FACET
    short noun-phrase (see terms.py/_aggregate) with its own generic 0.86-
    threshold rollup; this is a second, sentence-level pass over the same raw
    field, reusing premise's exact thresholds (8 posts/5 accounts, 0.97
    transcript dedupe) per the doc's spec rather than new constants. Runs for
    every world through the generic rollup() loop below, same as premise/hook/
    punchline — harmless elsewhere, since `setting` exists in every schema."""
    return _text_field_clusters(db, world, world_median, "setting_cluster", _setting_text)


def _hook_clusters(db, world: World, world_median: float) -> tuple[list[dict], list[dict]]:
    """Phase 9 layer 3 — hook space. Embeds `hook` verbatim (the doc is explicit
    that hook phrasings transfer across situations, so this is deliberately a
    separate embedding space from premise, not a sub-grouping within it):
    a reusable library of opening moves, ranked by lift."""
    return _text_field_clusters(db, world, world_median, "hook_cluster", _hook_text)


def _punchline_clusters(db, world: World, world_median: float) -> tuple[list[dict], list[dict]]:
    """Phase 9 layer 3 — punchline space. The doc wants these hand-labeled by
    rhetorical move (reframe, deflate, deadpan agreement...) rather than
    medoid-text — not done here (no automated way to classify a rhetorical
    move), so canon_term is still the medoid punchline sentence, same
    mechanism as premise/hook. Good enough to rank and drill into; the
    rhetorical-move taxonomy is a manual follow-up on top of this."""
    return _text_field_clusters(db, world, world_median, "punchline_cluster", _punchline_text)


def rollup(db, world_slug: str) -> list[dict]:
    """The one entry point. Returns the stats rows it wrote, for callers
    (run_overview.py) that want to print a summary without a second query."""
    world = world_by_slug(db, world_slug)

    _extract_and_store(db, world)
    canonicalize(db, world)

    median = world_median(db, world)
    by_account = account_medians(db, world)
    stats = _aggregate(db, world, median, by_account)

    for label, cluster_fn in [
        ("premise_cluster", _premise_clusters),
        ("setting_cluster", _setting_clusters),
        ("hook_cluster", _hook_clusters),
        ("punchline_cluster", _punchline_clusters),
    ]:
        cluster_stats, cluster_post_terms = cluster_fn(db, world, median)
        if cluster_post_terms:
            db.execute(insert(PostTerm).values(cluster_post_terms))
            db.commit()
        print(f"  {label}: {len(cluster_post_terms)} posts, {len(cluster_stats)} clusters above the support floor")
        stats = stats + cluster_stats

    if stats:
        median_lift = float(np.median([s["lift"] for s in stats]))
        print(f"  median lift across {len(stats)} ranked terms: {median_lift:+.4f}")
        assert abs(median_lift) < MEDIAN_LIFT_TOLERANCE, (
            f"median lift {median_lift:+.4f} is far from zero — baseline is likely computed "
            "over a different post set than the terms (see rollup()'s world_median comment)"
        )

    # Defensive dedupe on WorldTermStat's own PK (world_id, facet, canon_term) right
    # before the write that enforces it. Two independent facet passes can't collide
    # (facet is part of the key), but within one embedding-cluster facet a genuine
    # collision has been observed on the real romance corpus — HDBSCAN's cluster
    # assignment shifts between runs as the underlying post/embedding set changes,
    # and a rare case produced two clusters whose medoids landed on the same exact
    # text. Keep the row with more support (n_posts) as the more representative one.
    deduped: dict[tuple, dict] = {}
    for s in stats:
        key = (s["facet"], s["canon_term"])
        if key not in deduped or s["n_posts"] > deduped[key]["n_posts"]:
            deduped[key] = s
    if len(deduped) != len(stats):
        print(f"  dropped {len(stats) - len(deduped)} duplicate (facet, canon_term) row(s) before write")
    stats = list(deduped.values())

    db.execute(delete(WorldTermStat).where(WorldTermStat.world_id == world.id))
    if stats:
        db.execute(insert(WorldTermStat).values(stats))
    db.commit()

    return stats

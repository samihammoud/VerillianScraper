"""Phase 9 build order step 5: per-cluster profile + mismatch report (see
CLAUDEphase9romanceoverview.md's "The cross-examination that matters").

For each already-computed cluster (premise_cluster by default — the doc
frames this as cross-examining layer 1's output against the enum facets),
computes: the distribution of every enum facet over its member posts, median
duration, median dialogue-turn count, and — the part that actually answers a
question — the view_ratio of each `relationship_register` value *within*
that cluster (not against the world). Flags `register_mismatch` when the
modal register (what the corpus does most) differs from the highest-lift
register (what actually performs best) — the doc's "proven situation
executed in a register you happen to be better at" case.

Deliberately NOT implemented: the doc's "Fit" check (does a cluster's
winning profile already match `pairing=romantic_couple, register in
{cute_affectionate, funny}, resolution in {punchline, reassurance},
perspective=both, duration<15s, 4-8 turns`). Those thresholds describe one
specific account's own format — there's no way to compute "fit" without that
account's target profile as an input, so this only builds the objective half
(the distributions and the mismatch flag), not the subjective half (is this
cluster shoot-tomorrow-able for you specifically).

Writes topology.cluster_profiles, deleted and reinserted wholesale per
(world, facet) — a cache table like world_term_stats, not incrementally
updated. Depends on post_terms already containing rows for `facet` (i.e.
`make overview` already ran) — this is a second pass over that output, not
part of rollup() itself.

Usage: python -m src.analysis.topology.phase9.cluster_profiles romance
    or: python -m src.analysis.topology.phase9.cluster_profiles romance hook_cluster
"""

import sys
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from src.db.models import ClusterProfile, Post, PostTerm
from src.db.session import SessionLocal
from src.analysis.topology.overview import world_by_slug

# The enum facets worth profiling per cluster — mirrors terms.py's RAW_FACETS
# for the romance schema layer, read straight from vlm_json rather than
# re-joining post_terms once per facet.
ENUM_FIELDS = {
    "relationship_stage": ("relationship", "stage"),
    "relationship_conflict": ("relationship", "conflict"),
    "relationship_register": ("relationship", "register"),
    "relationship_resolution": ("relationship", "resolution"),
    "relationship_perspective": ("relationship", "perspective"),
    "characters_pairing": ("characters", "pairing"),
    "synthetic_presenter": ("synthetic", "presenter"),
    "pacing_energy_arc": ("pacing", "energy_arc"),
    "pacing_humor_sincerity": ("pacing", "humor_sincerity"),
    "pacing_speaker_dominance": ("pacing", "speaker_dominance"),
    "pacing_density": ("pacing", "density"),
}
MIN_REGISTER_SUPPORT = 3  # a register value with 1-2 posts shouldn't win "highest lift" on noise


def compute_profiles(db, world_slug: str, facet: str = "premise_cluster") -> list[dict]:
    world = world_by_slug(db, world_slug)

    rows = db.execute(
        select(PostTerm.canon_term, Post.views, Post.vlm_json)
        .join(Post, Post.id == PostTerm.post_id)
        .where(PostTerm.world_id == world.id, PostTerm.facet == facet)
    ).all()
    if not rows:
        print(f"no post_terms rows for facet={facet!r} — run `make overview {world_slug}` first")
        return []

    by_cluster: dict[str, list[dict]] = defaultdict(list)
    for canon_term, views, vlm_json in rows:
        by_cluster[canon_term].append({"views": views, "vlm_json": vlm_json or {}})

    profiles = []
    for canon_term, members in by_cluster.items():
        durations = [m["vlm_json"].get("estimated_duration_sec") for m in members if m["vlm_json"].get("estimated_duration_sec") is not None]
        turn_counts = [len(m["vlm_json"].get("dialogue") or []) for m in members]
        turn_counts = [t for t in turn_counts if t > 0] or None

        enum_distribution: dict[str, dict[str, int]] = {}
        for field_name, path in ENUM_FIELDS.items():
            counts = Counter()
            for m in members:
                node = m["vlm_json"]
                for key in path:
                    node = (node or {}).get(key)
                if node:
                    counts[node] += 1
            if counts:
                enum_distribution[field_name] = dict(counts)

        cluster_baseline = float(np.median([np.log1p(m["views"]) for m in members if m["views"]])) if any(m["views"] for m in members) else None

        enum_view_ratio: dict[str, dict[str, float]] = {}
        modal_register = highest_lift_register = None
        if cluster_baseline is not None:
            by_register: dict[str, list[float]] = defaultdict(list)
            for m in members:
                register = (m["vlm_json"].get("relationship") or {}).get("register")
                if register and m["views"]:
                    by_register[register].append(float(np.log1p(m["views"])))

            register_ratios = {}
            for register, metrics in by_register.items():
                if len(metrics) < MIN_REGISTER_SUPPORT:
                    continue
                register_ratios[register] = float(np.exp(float(np.median(metrics)) - cluster_baseline))
            if register_ratios:
                enum_view_ratio["relationship_register"] = register_ratios
                highest_lift_register = max(register_ratios, key=register_ratios.get)

            register_counts = enum_distribution.get("relationship_register")
            if register_counts:
                modal_register = max(register_counts, key=register_counts.get)

        profiles.append(
            {
                "world_id": world.id,
                "facet": facet,
                "canon_term": canon_term,
                "n_posts": len(members),
                "median_duration_sec": float(np.median(durations)) if durations else None,
                "median_dialogue_turns": float(np.median(turn_counts)) if turn_counts else None,
                "enum_distribution": enum_distribution,
                "enum_view_ratio": enum_view_ratio,
                "modal_register": modal_register,
                "highest_lift_register": highest_lift_register,
                "register_mismatch": bool(modal_register and highest_lift_register and modal_register != highest_lift_register),
            }
        )

    db.execute(delete(ClusterProfile).where(ClusterProfile.world_id == world.id, ClusterProfile.facet == facet))
    if profiles:
        db.execute(insert(ClusterProfile).values(profiles))
    db.commit()
    return profiles


def _print_report(profiles: list[dict]) -> None:
    mismatches = [p for p in profiles if p["register_mismatch"]]
    print(f"{len(profiles)} cluster profiles, {len(mismatches)} with a register mismatch\n")
    for p in sorted(profiles, key=lambda p: -p["n_posts"]):
        flag = " <-- MISMATCH" if p["register_mismatch"] else ""
        print(f"n={p['n_posts']:<5} modal={p['modal_register']!s:<20} best={p['highest_lift_register']!s:<20}{flag}")
        print(f"  {p['canon_term'][:100]}")
        if p["enum_view_ratio"].get("relationship_register"):
            ratios = ", ".join(f"{k}={v:.2f}x" for k, v in sorted(p["enum_view_ratio"]["relationship_register"].items(), key=lambda kv: -kv[1]))
            print(f"  register view_ratio within cluster: {ratios}")
        print()


if __name__ == "__main__":
    world_slug = sys.argv[1] if len(sys.argv) > 1 else "romance"
    facet = sys.argv[2] if len(sys.argv) > 2 else "premise_cluster"
    db = SessionLocal()
    try:
        result = compute_profiles(db, world_slug, facet)
        _print_report(result)
    finally:
        db.close()

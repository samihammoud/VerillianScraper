"""Phase 9 stratification (see the doc's "Stratify, don't just filter"
section). Recomputes lift for a facet's terms WITHIN a stratum instead of
against the whole world — e.g. if AI-presented romance content is
systematically discounted by the algorithm or the audience, the global
ranking measures a format some accounts can't run at all, and the
within-stratum ranking is the one that actually applies to them. Same idea
for duration buckets (a situation that only works at 40s isn't usable in a
<15s format, however well it performs globally).

A stratum, not a plain filter, because the *baseline* changes too: lift is
recomputed against the stratum's own median, not the world's — "does this
term beat what this stratum normally gets," matching how account_lift in
overview.py already works one level down (per-account instead of per-world).

Usage: python -m src.services.phase9.stratify romance premise_cluster synthetic.presenter ai_generated_person
   or: python -m src.services.phase9.stratify romance premise_cluster estimated_duration_sec:lt15
"""

import sys
from collections import defaultdict

import numpy as np
from sqlalchemy import select

from src.db.models import Post, PostTerm
from src.db.session import SessionLocal
from src.services.overview import MIN_ACCOUNTS, MIN_POSTS, world_by_slug


def _stratum_predicate(stratum_field: str, stratum_value: str):
    """Dotted-path equality (e.g. "synthetic.presenter" == "ai_generated_person"),
    or "estimated_duration_sec:lt15" / ":ge15" for the doc's duration buckets —
    covers the two stratification axes the doc actually asks for without
    needing a general query language for what's meant to be a quick, one-off
    analysis tool, not a permanent filter UI."""
    if ":" in stratum_field:
        field, op = stratum_field.split(":", 1)

        def duration_pred(vlm_json):
            value = (vlm_json or {}).get(field)
            if value is None:
                return False
            threshold = float(stratum_value)
            return value < threshold if op == "lt" else value >= threshold

        return duration_pred

    path = stratum_field.split(".")

    def dotted_pred(vlm_json):
        node = vlm_json or {}
        for p in path:
            node = (node or {}).get(p)
            if node is None:
                return False
        return node == stratum_value

    return dotted_pred


def run(world_slug: str, facet: str, stratum_field: str, stratum_value: str) -> None:
    db = SessionLocal()
    try:
        world = world_by_slug(db, world_slug)
        rows = db.execute(
            select(PostTerm.canon_term, PostTerm.account_id, PostTerm.metric, Post.vlm_json)
            .join(Post, Post.id == PostTerm.post_id)
            .where(PostTerm.world_id == world.id, PostTerm.facet == facet, PostTerm.metric.isnot(None))
        ).all()
        if not rows:
            print(f"no post_terms rows for facet={facet!r} — run `make overview {world_slug}` first")
            return

        predicate = _stratum_predicate(stratum_field, stratum_value)
        stratum_rows = [(ct, aid, m) for ct, aid, m, vj in rows if predicate(vj)]
        if not stratum_rows:
            print(f"no posts matched {stratum_field}={stratum_value!r} for facet={facet!r}")
            return
        stratum_baseline = float(np.median([m for _, _, m in stratum_rows]))

        groups: dict[str, dict] = defaultdict(lambda: {"metrics": [], "accounts": set()})
        for canon_term, account_id, metric in stratum_rows:
            g = groups[canon_term]
            g["metrics"].append(metric)
            g["accounts"].add(account_id)

        print(f"stratum: {stratum_field}={stratum_value}  ({len(stratum_rows)} posts, baseline median={stratum_baseline:.3f})\n")
        print(f"{'lift':>7}  {'n_posts':>7} {'n_acct':>6}  canon_term")
        results = []
        for canon_term, g in groups.items():
            if len(g["metrics"]) < MIN_POSTS or len(g["accounts"]) < MIN_ACCOUNTS:
                continue
            lift = float(np.median(g["metrics"])) - stratum_baseline
            results.append((lift, len(g["metrics"]), len(g["accounts"]), canon_term))
        for lift, n_posts, n_accounts, canon_term in sorted(results, reverse=True):
            print(f"{lift:>+7.2f}  {n_posts:>7} {n_accounts:>6}  {canon_term[:80]}")
        if not results:
            print(f"(no terms cleared the support floor: n_posts>={MIN_POSTS}, n_accounts>={MIN_ACCOUNTS})")
    finally:
        db.close()


if __name__ == "__main__":
    world_slug = sys.argv[1] if len(sys.argv) > 1 else "romance"
    facet = sys.argv[2] if len(sys.argv) > 2 else "premise_cluster"
    stratum_field = sys.argv[3] if len(sys.argv) > 3 else "synthetic.presenter"
    stratum_value = sys.argv[4] if len(sys.argv) > 4 else "ai_generated_person"
    run(world_slug, facet, stratum_field, stratum_value)

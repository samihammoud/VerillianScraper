"""Phase 9 build order step 3: recency split -> four-quadrant view (see
CLAUDEphase9romanceoverview.md's "Stratify, don't just filter" section).

Splits each cluster's member posts by posted_at into a recent window and
everything older, computes volume + lift within each window using the
world's single global median as the baseline for both (not recomputed per
window — the doc doesn't ask for a separate baseline per window, and this
keeps "lift" meaning the same thing everywhere else in the pipeline), and
classifies each cluster into one of four quadrants:

              low volume       high volume
  high lift   emerging         proven
  low lift    dead             saturated

"Emerging" is the doc's own answer to "the only quadrant that answers the
actual question" — the queue of things worth trying next.

VOLUME_HIGH_THRESHOLD is a rough split, not tuned against this corpus the way
PREMISE_MIN_CLUSTER_SIZE was — eyeball the actual recent_n distribution
printed here before trusting the quadrant label on a borderline cluster.

Usage: python -m src.analysis.topology.phase9.recency_quadrant romance
    or: python -m src.analysis.topology.phase9.recency_quadrant romance hook_cluster
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select

from src.db.models import Post, PostTerm
from src.db.session import SessionLocal
from src.analysis.topology.overview import world_by_slug, world_median

RECENCY_DAYS = 30
VOLUME_HIGH_THRESHOLD = 15  # posts in the recent window alone


def quadrant(recent_n: int, recent_lift: float) -> str:
    high_lift = recent_lift > 0
    high_volume = recent_n >= VOLUME_HIGH_THRESHOLD
    if high_lift:
        return "proven" if high_volume else "emerging"
    return "saturated" if high_volume else "dead"


def run(world_slug: str, facet: str = "premise_cluster") -> None:
    db = SessionLocal()
    try:
        world = world_by_slug(db, world_slug)
        baseline = world_median(db, world)
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)

        rows = db.execute(
            select(PostTerm.canon_term, Post.posted_at, Post.views)
            .join(Post, Post.id == PostTerm.post_id)
            .where(PostTerm.world_id == world.id, PostTerm.facet == facet, Post.views.isnot(None))
        ).all()
        if not rows:
            print(f"no post_terms rows for facet={facet!r} — run `make overview {world_slug}` first")
            return

        by_cluster: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"recent": [], "older": []})
        for canon_term, posted_at, views in rows:
            metric = float(np.log1p(views))
            window = "recent" if posted_at and posted_at.replace(tzinfo=timezone.utc) >= cutoff else "older"
            by_cluster[canon_term][window].append(metric)

        print(f"baseline (world median log1p(views)): {baseline:.3f}, recency cutoff: {RECENCY_DAYS} days\n")
        print(f"{'quadrant':<10} {'recent_n':>8} {'recent_lift':>12} {'older_n':>8} {'older_lift':>11}  canon_term")
        rows_out = []
        for canon_term, windows in by_cluster.items():
            recent_n, older_n = len(windows["recent"]), len(windows["older"])
            recent_lift = float(np.median(windows["recent"])) - baseline if recent_n else float("-inf")
            older_lift = float(np.median(windows["older"])) - baseline if older_n else float("-inf")
            rows_out.append((quadrant(recent_n, recent_lift), recent_n, recent_lift, older_n, older_lift, canon_term))

        for q, recent_n, recent_lift, older_n, older_lift, canon_term in sorted(rows_out, key=lambda r: -r[2]):
            older_str = f"{older_lift:>+11.2f}" if older_n else f"{'--':>11}"
            print(f"{q:<10} {recent_n:>8} {recent_lift:>+12.2f} {older_n:>8} {older_str}  {canon_term[:80]}")
    finally:
        db.close()


if __name__ == "__main__":
    world_slug = sys.argv[1] if len(sys.argv) > 1 else "romance"
    facet = sys.argv[2] if len(sys.argv) > 2 else "premise_cluster"
    run(world_slug, facet)

"""Support-floor checks for GET /worlds/{slug}/overview.

The thing that breaks silently: volume_ratio's denominator. It's the median
n_posts over the whole facet, and min_posts used to be applied before it was
computed — so raising the floor quietly rescaled every ratio in the app instead
of just hiding rows. Needs the local Postgres up, with one world's overview built.
"""

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from src.db.models import World, WorldTermStat
from src.db.session import SessionLocal
from src.main import app


def _busiest_facet() -> tuple[str, str]:
    """Whichever (world, facet) has the most ranked terms — the assertions are
    invariants, so they must not hardcode a corpus that `make overview` rebuilds."""
    db = SessionLocal()
    try:
        row = db.execute(
            select(World.slug, WorldTermStat.facet, func.count())
            .join(WorldTermStat, WorldTermStat.world_id == World.id)
            .group_by(World.slug, WorldTermStat.facet)
            .order_by(func.count().desc())
            .limit(1)
        ).first()
        assert row, "no world_term_stats rows — run `make overview WORLD=...` first"
        return row[0], row[1]
    finally:
        db.close()


def test_floors_filter_without_rescaling():
    slug, facet = _busiest_facet()
    client = TestClient(app)

    # Through the real client, not world_overview() directly: called as a plain
    # function its Query(...) defaults arrive unresolved, and favorites_only's
    # default object is truthy — the filters under test would silently run
    # against the starred-only slice.
    def get(**params):
        r = client.get(f"/worlds/{slug}/overview", params={"facet": facet, "limit": 200, **params})
        assert r.status_code == 200, r.text
        return r.json()

    loose = get(min_posts=3, min_accounts=3)
    assert loose["total"] > 0, f"{slug}/{facet} has no terms at the loosest floor"

    prev = loose["total"]
    for posts, accounts in [(4, 4), (6, 4), (10, 6), (12, 8)]:
        page = get(min_posts=posts, min_accounts=accounts)
        assert page["total"] <= prev, f"raising the floor to {posts}/{accounts} admitted more terms"
        prev = page["total"]
        for t in page["terms"]:
            assert t["n_posts"] >= posts, f"{t['canon_term']!r} has {t['n_posts']} posts, floor was {posts}"
            assert t["n_accounts"] >= accounts, f"{t['canon_term']!r} has {t['n_accounts']} accounts"

    # A term's volume_ratio must mean the same thing at every floor: same
    # denominator, so the only effect of a floor is which rows come back.
    ratios = {}
    for posts, accounts in [(3, 3), (4, 4), (6, 4), (10, 6)]:
        for t in get(min_posts=posts, min_accounts=accounts)["terms"]:
            ratios.setdefault(t["canon_term"], set()).add(round(t["volume_ratio"], 9))
    drifted = {term: vs for term, vs in ratios.items() if len(vs) > 1}
    assert not drifted, f"volume_ratio moved with the active floor: {dict(list(drifted.items())[:3])}"

    # max_posts composes with both floors rather than overriding them.
    banded = get(min_posts=3, min_accounts=5, max_posts=5)
    for t in banded["terms"]:
        assert 3 <= t["n_posts"] <= 5 and t["n_accounts"] >= 5, t


if __name__ == "__main__":
    test_floors_filter_without_rescaling()
    print("overview support floors ok")

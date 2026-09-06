"""Self-check for _text_field_clusters' member prune (MEMBER_COSINE_FLOOR).

Run: PYTHONPATH=. .venv/bin/python -m src.services.test_member_floor

Pure numpy — no DB, no OpenAI. Rebuilds the prune-then-support-floor block
against a synthetic similarity matrix shaped like the real 2026-09-04 defect:
one tight cluster plus a residual blob whose members are near-orthogonal to
their own medoid.
"""

import numpy as np

from src.services.overview import MEMBER_COSINE_FLOOR, PREMISE_MIN_ACCOUNTS, PREMISE_MIN_POSTS


def _survives(sub_sim: np.ndarray, account_ids: list[str]) -> int | None:
    """Mirrors overview._text_field_clusters' prune + support floor. Returns the
    surviving member count, or None if the cluster is rejected."""
    medoid = int(np.argmax(sub_sim.sum(axis=1)))
    keep = [i for i in range(len(account_ids)) if sub_sim[medoid, i] >= MEMBER_COSINE_FLOOR]
    accounts = {account_ids[i] for i in keep}
    if len(keep) < PREMISE_MIN_POSTS or len(accounts) < PREMISE_MIN_ACCOUNTS:
        return None
    return len(keep)


def _sim(n: int, off_diagonal: float) -> np.ndarray:
    m = np.full((n, n), off_diagonal)
    np.fill_diagonal(m, 1.0)
    return m


def demo() -> None:
    accounts = lambda n: [f"acct{i}" for i in range(n)]  # noqa: E731 — one account per post, floors are about posts here

    # A real cluster: every member well above the floor, nothing pruned.
    assert _survives(_sim(20, 0.55), accounts(20)) == 20

    # The blob: members near-orthogonal to the medoid, all pruned but the
    # medoid itself, so the support floor kills it with no extra reject rule.
    assert _survives(_sim(154, 0.17), accounts(154)) is None

    # A cluster that is half real, half swept-in noise keeps only the real half.
    n = 40
    m = _sim(n, 0.10)
    m[np.ix_(range(12), range(12))] = 0.60  # the tight core
    np.fill_diagonal(m, 1.0)
    assert _survives(m, accounts(n)) == 12

    # Enough posts but too few accounts is still rejected — the prune must not
    # bypass PREMISE_MIN_ACCOUNTS.
    assert _survives(_sim(20, 0.55), ["only"] * 20) is None

    # Exactly at the floor is kept, just below is not.
    assert _survives(_sim(20, MEMBER_COSINE_FLOOR), accounts(20)) == 20
    assert _survives(_sim(20, MEMBER_COSINE_FLOOR - 0.01), accounts(20)) is None

    print("ok")


if __name__ == "__main__":
    demo()

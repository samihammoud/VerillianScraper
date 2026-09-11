"""Phase 16 stage 2: validate an LLM merge CSV against its candidates file and load it.

The CSV names canon_terms (what the LLM saw); the table is keyed on norm_term,
because a cluster's label is its biggest member and drifts as posts arrive
while norm_term is a pure function of the string. The expansion from one to
the other uses the norm_terms captured in the candidates file at export time,
never live post_terms — a `make overview` in between must not shift the mapping.

Usage: python -m src.scripts.load_term_merges \
           data/term_merges/discount-shopping__topic.candidates.json \
           data/term_merges/discount-shopping__topic.merges.csv
"""

import csv
import json
import pathlib
import sys

from sqlalchemy import text

from src.db.session import SessionLocal


class MergeError(Exception):
    """Validation failure — nothing is loaded."""


def resolve(rows: list[dict], world: str, facet: str, terms: set[str]) -> dict[str, str]:
    """Validate merge rows against the candidate term set and follow chains.

    Returns {from_term: final to_term}. Raises MergeError listing every
    offending row rather than loading a partially-sane mapping.
    """
    bad: list[str] = []
    direct: dict[str, str] = {}

    for i, r in enumerate(rows, start=2):  # row 1 is the header
        f, t = r["from_term"], r["to_term"]
        if r["world"] != world or r["facet"] != facet:
            bad.append(f"line {i}: out of scope ({r['world']}/{r['facet']}, expected {world}/{facet})")
            continue
        if f not in terms:
            bad.append(f"line {i}: from_term not in candidates: {f!r}")
        if t not in terms:
            bad.append(f"line {i}: to_term not in candidates: {t!r}")
        if f == t:
            bad.append(f"line {i}: from_term == to_term: {f!r}")
        elif f in direct:
            bad.append(f"line {i}: duplicate from_term: {f!r}")
        else:
            direct[f] = t

    if bad:
        raise MergeError("\n".join(bad))

    final: dict[str, str] = {}
    for f in direct:
        seen = [f]
        t = direct[f]
        while t in direct:
            if t in seen:
                raise MergeError(f"cycle: {' -> '.join(seen + [t])}")
            seen.append(t)
            t = direct[t]
        final[f] = t
    return final


def main() -> None:
    cand_path, merge_path = (pathlib.Path(p) for p in sys.argv[1:3])
    candidates = json.loads(cand_path.read_text())
    world, facet = candidates[0]["world"], candidates[0]["facet"]
    norms = {c["canon_term"]: c["norm_terms"] or [] for c in candidates}

    with merge_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    try:
        final = resolve(rows, world, facet, set(norms))
    except MergeError as e:
        print(f"refusing to load:\n{e}")
        sys.exit(1)

    # Every norm_term of the to_term and of each from_term maps to the to_term.
    mapping: dict[str, str] = {}
    for f, t in final.items():
        for n in norms[f] + norms[t]:
            mapping[n] = t

    db = SessionLocal()
    try:
        world_id = db.execute(text("SELECT id FROM topology.worlds WHERE slug = :s"), {"s": world}).scalar_one()
        db.execute(
            text("""
                INSERT INTO topology.term_merges (world_id, facet, norm_term, canon_term)
                VALUES (:world_id, :facet, :norm_term, :canon_term)
                ON CONFLICT (world_id, facet, norm_term)
                DO UPDATE SET canon_term = EXCLUDED.canon_term
            """),
            [{"world_id": world_id, "facet": facet, "norm_term": n, "canon_term": t} for n, t in mapping.items()],
        )
        db.commit()
        scope = db.execute(
            text("""SELECT w.slug, m.facet, count(*) FROM topology.term_merges m
                    JOIN topology.worlds w ON w.id = m.world_id GROUP BY 1, 2""")
        ).all()
    finally:
        db.close()

    print(f"{world}/{facet}: {len(rows)} merge rows -> {len(final)} mappings -> {len(mapping)} norm_terms upserted")
    print(f"term_merges scope: {scope}")


def demo() -> None:
    terms = {"a", "b", "c", "d"}
    row = lambda f, t, w="w", fc="topic": {"world": w, "facet": fc, "from_term": f, "to_term": t}

    assert resolve([row("a", "b")], "w", "topic", terms) == {"a": "b"}
    # chain A->B, B->C collapses to C
    assert resolve([row("a", "b"), row("b", "c")], "w", "topic", terms) == {"a": "c", "b": "c"}

    for bad, why in [
        ([row("a", "b", w="other")], "out of scope world"),
        ([row("a", "b", fc="product")], "out of scope facet"),
        ([row("zzz", "b")], "unknown from_term"),
        ([row("a", "zzz")], "unknown to_term"),
        ([row("a", "b"), row("a", "c")], "duplicate from_term"),
        ([row("a", "a")], "from == to"),
        ([row("a", "b"), row("b", "a")], "cycle"),
    ]:
        try:
            resolve(bad, "w", "topic", terms)
            raise AssertionError(f"should have refused: {why}")
        except MergeError:
            pass
    print("ok")


if __name__ == "__main__":
    demo() if sys.argv[1:2] == ["--demo"] else main()

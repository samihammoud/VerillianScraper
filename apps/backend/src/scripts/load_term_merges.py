"""Phase 16 stage 2: validate an LLM override CSV against its candidates file and load it.

The CSV names canon_terms (what the LLM saw); the table is keyed on norm_term,
because a cluster's label is its biggest member and drifts as posts arrive
while norm_term is a pure function of the string. The expansion from one to
the other uses the norm_terms captured in the candidates file at export time,
never live post_terms — a `make overview` in between must not shift the mapping.

Upsert, not delete-and-replace: an earlier pass's decisions are the baseline
this one refines. A term absorbed by an earlier merge no longer appears in the
candidates file at all, so a later pass cannot express "un-merge that" — it
would have to be undone by hand.

Usage: python -m src.scripts.load_term_merges \
           data/term_merges/discount-shopping__topic.candidates.json \
           data/term_merges/discount-shopping__topic.overrides.csv
"""

import csv
import json
import pathlib
import sys
from collections import Counter

from sqlalchemy import text

from src.db.session import SessionLocal

ACTIONS = {"keep", "merge", "child_of", "drop"}
NEEDS_TARGET = {"merge", "child_of"}


class MergeError(Exception):
    """Validation failure — nothing is loaded."""


def resolve(rows: list[dict], world: str, facet: str, terms: set[str]) -> dict[str, tuple[str, str | None]]:
    """Validate override rows against the candidate term set and follow merge chains.

    Returns {term: (action, final target)} for every non-keep row. Raises
    MergeError listing every offending row rather than loading a half-sane
    mapping.
    """
    bad: list[str] = []
    decided: dict[str, tuple[str, str | None]] = {}

    for i, r in enumerate(rows, start=2):  # row 1 is the header
        term, action, target = r["term"], r["action"], (r["target"] or None)
        if r["world"] != world or r["facet"] != facet:
            bad.append(f"line {i}: out of scope ({r['world']}/{r['facet']}, expected {world}/{facet})")
            continue
        if action not in ACTIONS:
            bad.append(f"line {i}: unknown action {action!r}")
            continue
        if action in NEEDS_TARGET and not target:
            bad.append(f"line {i}: {action} needs a target: {term!r}")
        if action not in NEEDS_TARGET and target:
            bad.append(f"line {i}: {action} must not have a target: {term!r} -> {target!r}")
        if term not in terms:
            bad.append(f"line {i}: term not in candidates: {term!r}")
        if target and target not in terms:
            bad.append(f"line {i}: target not in candidates: {target!r}")
        if target == term:
            bad.append(f"line {i}: term == target: {term!r}")
        elif term in decided:
            bad.append(f"line {i}: duplicate term: {term!r}")
        elif action != "keep":
            decided[term] = (action, target)

    if bad:
        raise MergeError("\n".join(bad))

    merges = {t: tgt for t, (a, tgt) in decided.items() if a == "merge"}
    final: dict[str, tuple[str, str | None]] = {}
    for term, (action, target) in decided.items():
        if action == "drop":
            final[term] = (action, None)
            continue
        if action == "child_of":
            # Never chained: a parent must be a standalone ranked term, so pointing at
            # anything the LLM also decided against is a refusal, not something to fix up.
            if target in decided:
                raise MergeError(f"child_of target is itself {decided[target][0]}: {term!r} -> {target!r}")
            final[term] = (action, target)
            continue
        seen = [term]
        while target in merges:
            if target in seen:
                raise MergeError(f"cycle: {' -> '.join(seen + [target])}")
            seen.append(target)
            target = merges[target]
        if target in decided:  # resolved to something dropped or nested
            raise MergeError(f"merge target is {decided[target][0]}: {term!r} -> {target!r}")
        final[term] = (action, target)
    return final


def main() -> None:
    cand_path, override_path = (pathlib.Path(p) for p in sys.argv[1:3])
    candidates = json.loads(cand_path.read_text())
    world, facet = candidates[0]["world"], candidates[0]["facet"]
    norms = {c["canon_term"]: c["norm_terms"] or [] for c in candidates}

    with override_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    try:
        final = resolve(rows, world, facet, set(norms))
    except MergeError as e:
        print(f"refusing to load:\n{e}")
        sys.exit(1)

    # merge: every norm_term of the target AND of each merged term maps to the target.
    # child_of/drop: only the term's own norm_terms — the parent is untouched.
    out: dict[str, tuple[str, str | None]] = {}
    for term, (action, target) in final.items():
        keys = norms[term] + (norms[target] if action == "merge" else [])
        for n in keys:
            out[n] = (action, target)

    db = SessionLocal()
    try:
        world_id = db.execute(text("SELECT id FROM topology.worlds WHERE slug = :s"), {"s": world}).scalar_one()
        db.execute(
            text("""
                INSERT INTO topology.term_overrides (world_id, facet, norm_term, action, target_term)
                VALUES (:world_id, :facet, :norm_term, :action, :target_term)
                ON CONFLICT (world_id, facet, norm_term)
                DO UPDATE SET action = EXCLUDED.action, target_term = EXCLUDED.target_term
            """),
            [
                {"world_id": world_id, "facet": facet, "norm_term": n, "action": a, "target_term": t}
                for n, (a, t) in out.items()
            ],
        )
        db.commit()
        scope = db.execute(
            text("""SELECT w.slug, o.facet, o.action, count(*) FROM topology.term_overrides o
                    JOIN topology.worlds w ON w.id = o.world_id GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""")
        ).all()
    finally:
        db.close()

    print(f"{world}/{facet}: {len(rows)} rows -> {dict(Counter(a for a, _ in final.values()))}")
    print(f"  {len(out)} norm_terms upserted")
    for row in scope:
        print(f"  term_overrides: {row[0]}/{row[1]} {row[2]}={row[3]}")


def demo() -> None:
    terms = {"a", "b", "c", "d"}
    row = lambda t, act, tgt="", w="w", f="topic": {"world": w, "facet": f, "term": t, "action": act, "target": tgt}

    assert resolve([row("a", "merge", "b")], "w", "topic", terms) == {"a": ("merge", "b")}
    assert resolve([row("a", "keep")], "w", "topic", terms) == {}  # keep is absence
    assert resolve([row("a", "drop")], "w", "topic", terms) == {"a": ("drop", None)}
    assert resolve([row("a", "child_of", "b")], "w", "topic", terms) == {"a": ("child_of", "b")}
    # merge chain A->B, B->C collapses to C
    assert resolve([row("a", "merge", "b"), row("b", "merge", "c")], "w", "topic", terms) == {
        "a": ("merge", "c"),
        "b": ("merge", "c"),
    }

    for bad, why in [
        ([row("a", "merge", "b", w="other")], "out of scope world"),
        ([row("a", "merge", "b", f="product")], "out of scope facet"),
        ([row("a", "fold", "b")], "unknown action"),
        ([row("a", "merge")], "merge without target"),
        ([row("a", "child_of")], "child_of without target"),
        ([row("a", "drop", "b")], "drop with target"),
        ([row("a", "keep", "b")], "keep with target"),
        ([row("zzz", "merge", "b")], "unknown term"),
        ([row("a", "merge", "zzz")], "unknown target"),
        ([row("a", "merge", "b"), row("a", "merge", "c")], "duplicate term"),
        ([row("a", "merge", "a")], "term == target"),
        ([row("a", "merge", "b"), row("b", "merge", "a")], "cycle"),
        ([row("a", "child_of", "b"), row("b", "merge", "c")], "child_of pointing at a merged term"),
        ([row("a", "child_of", "b"), row("b", "drop")], "child_of pointing at a dropped term"),
        ([row("a", "merge", "b"), row("b", "drop")], "merge resolving to a dropped term"),
    ]:
        try:
            resolve(bad, "w", "topic", terms)
            raise AssertionError(f"should have refused: {why}")
        except MergeError:
            pass
    print("ok")


if __name__ == "__main__":
    demo() if sys.argv[1:2] == ["--demo"] else main()

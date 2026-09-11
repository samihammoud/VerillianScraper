"""Phase 16 stage 0: export the ranked terms of one (world, facet) as LLM override candidates.

The norm_terms behind each canon_term are written into the file so the later
load step maps from the file, not from live post_terms — a `make overview`
between export and load can't shift the mapping.

co_occurring is what lets the LLM tell a characteristic from a hero topic: a
term whose posts nearly all carry some bigger topic too is part of that topic,
not a topic of its own.

Usage: python -m src.scripts.export_merge_candidates --world discount-shopping --facet topic
"""

import argparse
import json
import pathlib

from sqlalchemy import text

from src.db.session import SessionLocal

OUT_DIR = pathlib.Path("data/term_merges")
CO_OCCURRING_TOP_N = 5

SQL = text("""
SELECT w.slug AS world, s.facet, s.canon_term, s.n_posts, s.n_accounts,
       (SELECT array_agg(DISTINCT pt.norm_term ORDER BY pt.norm_term)
          FROM topology.post_terms pt
         WHERE pt.world_id = s.world_id AND pt.facet = s.facet
           AND pt.canon_term = s.canon_term) AS norm_terms,
       s.variants
  FROM topology.world_term_stats s
  JOIN topology.worlds w ON w.id = s.world_id
 WHERE w.slug = :world AND s.facet = :facet
   AND s.n_posts >= 6 AND s.n_accounts >= 4
 ORDER BY s.n_posts DESC
""")

# DISTINCT (post_id, canon_term) first — post_terms has one row per raw_term, so a
# post naming the same canon_term twice would otherwise count twice in the overlap.
CO_SQL = text("""
WITH pt AS (
    SELECT DISTINCT post_id, canon_term
      FROM topology.post_terms
     WHERE world_id = (SELECT id FROM topology.worlds WHERE slug = :world)
       AND facet = :facet
), co AS (
    SELECT a.canon_term AS term, b.canon_term AS other, count(*) AS shared_posts
      FROM pt a JOIN pt b ON b.post_id = a.post_id AND b.canon_term <> a.canon_term
     GROUP BY 1, 2
)
SELECT term, other, shared_posts
  FROM (SELECT *, row_number() OVER (PARTITION BY term ORDER BY shared_posts DESC, other) AS rn FROM co) r
 WHERE rn <= :top_n
 ORDER BY term, rn
""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--facet", required=True)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = [dict(r) for r in db.execute(SQL, {"world": args.world, "facet": args.facet}).mappings()]
        co = db.execute(
            CO_SQL, {"world": args.world, "facet": args.facet, "top_n": CO_OCCURRING_TOP_N}
        ).all()
    finally:
        db.close()

    by_term: dict[str, list[dict]] = {}
    for term, other, shared in co:
        by_term.setdefault(term, []).append({"term": other, "shared_posts": shared})
    for r in rows:
        r["co_occurring"] = [
            {**c, "share": round(c["shared_posts"] / r["n_posts"], 3)} for c in by_term.get(r["canon_term"], [])
        ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.world}__{args.facet}.candidates.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"{args.world}/{args.facet}: {len(rows)} terms -> {out}")


if __name__ == "__main__":
    main()

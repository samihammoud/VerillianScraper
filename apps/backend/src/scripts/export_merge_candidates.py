"""Phase 16 stage 0: export the ranked terms of one (world, facet) as LLM merge candidates.

The norm_terms behind each canon_term are written into the file so the later
load step maps from the file, not from live post_terms — a `make overview`
between export and load can't shift the mapping.

Usage: python -m src.scripts.export_merge_candidates --world discount_shopping --facet topic
"""

import argparse
import json
import pathlib

from sqlalchemy import text

from src.db.session import SessionLocal

OUT_DIR = pathlib.Path("data/term_merges")

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True)
    ap.add_argument("--facet", required=True)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = [dict(r) for r in db.execute(SQL, {"world": args.world, "facet": args.facet}).mappings()]
    finally:
        db.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.world}__{args.facet}.candidates.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"{args.world}/{args.facet}: {len(rows)} terms -> {out}")


if __name__ == "__main__":
    main()

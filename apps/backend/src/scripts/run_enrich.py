"""CLI entry point for stage 2 (bulk enrich): comments + VLM descriptions,
backfilled over whatever posts currently lack them. Advisory-locked — safe to
schedule on a cron even if a previous run is still going.

Usage: make enrich
   or: python -m src.scripts.run_enrich
"""

from src.services.enrich import run_enrich

if __name__ == "__main__":
    run_enrich()

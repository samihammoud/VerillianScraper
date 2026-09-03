"""CLI entry point for stage 2 (bulk enrich): comments only, backfilled over
whatever posts currently lack them. VLM descriptions are no longer part of
this stage — they run inside the crawl loop itself (crawl.run_round calls
vision.describe_posts per round) since a post's visual description is needed
to generate the next round's queries. Advisory-locked — safe to schedule on a
cron even if a previous run is still going.

Usage: make enrich
   or: python -m src.scripts.run_enrich
"""

from src.services.enrich import run_enrich

if __name__ == "__main__":
    run_enrich()

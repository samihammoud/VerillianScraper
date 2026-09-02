"""Loads a world's hand-authored crawl inputs from data/<slug>/crawl/.

Text files rather than Python literals because these are what gets iterated
on between runs — a bad seed query set is fixed by reading round 0's yield table
and editing a line, not by editing a module. Nested under crawl/ (rather than
directly in data/<slug>/) so everything one crawl round consumes for a world
is visibly one bundle, not loose files sitting next to whatever else a world's
folder later grows.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"  # -> apps/backend/data


def load_seed_queries(slug: str) -> list[str]:
    lines = (l.strip() for l in (DATA_DIR / slug / "crawl" / "seed_queries.txt").read_text().splitlines())
    return [l for l in lines if l and not l.startswith("#")]


def load_query_prompt(slug: str) -> str:
    """Falls back to data/_default/crawl/ so the eight product worlds keep the
    original prompt without needing a file each."""
    path = DATA_DIR / slug / "crawl" / "query_prompt.txt"
    if not path.exists():
        path = DATA_DIR / "_default" / "crawl" / "query_prompt.txt"
    return "\n".join(l for l in path.read_text().splitlines() if not l.startswith("#")).strip()


def _self_check() -> None:
    seeds = load_seed_queries("romance")
    assert len(seeds) == 10
    assert all(seeds)  # no blank/comment lines leaked through

    prompt = load_query_prompt("romance")
    filled = prompt.format(QUERIES_PER_ROUND=5, N_EXPLOIT=2, N_EXPLORE=3)
    assert filled  # a prompt that lost its placeholders would still be non-empty but must still format cleanly
    assert "{" not in filled and "}" not in filled  # every placeholder actually got filled

    # a world with no query_prompt.txt falls back to data/_default/
    default_prompt = load_query_prompt("pets").format(QUERIES_PER_ROUND=5, N_EXPLOIT=2, N_EXPLORE=3)
    assert default_prompt
    assert "{" not in default_prompt and "}" not in default_prompt

    print("crawl_config self-check ok")


if __name__ == "__main__":
    _self_check()

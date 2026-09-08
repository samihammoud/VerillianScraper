"""Loads a world's hand-authored crawl inputs from data/<slug>/crawl/.

A text file rather than a Python literal because the query prompt is what
gets iterated on between runs. Nested under crawl/ (rather than directly in
data/<slug>/) so everything one crawl round consumes for a world is visibly
one bundle, not loose files sitting next to whatever else a world's folder
later grows.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"  # -> apps/backend/data


def _read(path: Path) -> str:
    return "\n".join(l for l in path.read_text().splitlines() if not l.startswith("#")).strip()


def load_query_prompt(slug: str) -> str:
    """Falls back to data/_default/crawl/ so the eight product worlds keep the
    original prompt without needing a file each."""
    path = DATA_DIR / slug / "crawl" / "query_prompt.txt"
    if not path.exists():
        path = DATA_DIR / "_default" / "crawl" / "query_prompt.txt"
    return _read(path)


def load_world(slug: str) -> str:
    """The world's reference-embedding description (seed_worlds). Only worlds
    added from phase 13 on have one — the original eight keep their literal in
    seed_worlds.WORLDS."""
    return _read(DATA_DIR / slug / "crawl" / "world.txt")


def load_seed_queries(slug: str) -> list[str]:
    """Round-0 queries, one per line; also the sub-niche list query_gen counts
    coverage against. Empty list for a world with no file."""
    path = DATA_DIR / slug / "crawl" / "seed_queries.txt"
    if not path.exists():
        return []
    return [l for l in _read(path).splitlines() if l.strip()]


def _self_check() -> None:
    prompt = load_query_prompt("romance")
    filled = prompt.format(QUERIES_PER_ROUND=5, N_EXPLOIT=2, N_EXPLORE=3)
    assert filled  # a prompt that lost its placeholders would still be non-empty but must still format cleanly
    assert "{" not in filled and "}" not in filled  # every placeholder actually got filled

    # a world with no query_prompt.txt falls back to data/_default/
    default_prompt = load_query_prompt("pets").format(QUERIES_PER_ROUND=5, N_EXPLOIT=2, N_EXPLORE=3)
    assert default_prompt
    assert "{" not in default_prompt and "}" not in default_prompt

    assert load_world("discount-shopping").startswith("Content about")
    assert len(load_seed_queries("discount-shopping")) == 10
    assert load_seed_queries("pets") == []  # no file -> empty, not a crash

    print("crawl_config self-check ok")


if __name__ == "__main__":
    _self_check()

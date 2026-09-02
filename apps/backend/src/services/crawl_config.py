"""Loads a world's hand-authored crawl inputs from data/<slug>/.

Text files rather than Python literals because these three are what gets iterated
on between runs — a bad seed query set is fixed by reading round 0's yield table
and editing a line, not by editing a module.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"  # -> apps/backend/data


def load_seed_queries(slug: str) -> list[str]:
    lines = (l.strip() for l in (DATA_DIR / slug / "seed_queries.txt").read_text().splitlines())
    return [l for l in lines if l and not l.startswith("#")]


def load_query_prompt(slug: str) -> str:
    """Falls back to data/_default/ so the eight product worlds keep the original
    prompt without needing a file each."""
    path = DATA_DIR / slug / "query_prompt.txt"
    if not path.exists():
        path = DATA_DIR / "_default" / "query_prompt.txt"
    return "\n".join(l for l in path.read_text().splitlines() if not l.startswith("#")).strip()


def load_world(slug: str) -> dict:
    """Blocks separated by blank lines: name, description, snippets."""
    blocks = [b.strip() for b in (DATA_DIR / slug / "world.txt").read_text().split("\n\n") if b.strip()]
    if len(blocks) != 3:
        raise ValueError(f"{slug}/world.txt: expected 3 blank-line blocks, got {len(blocks)}")
    name, description, snippets = blocks
    return {
        "slug": slug,
        "name": name,
        "description": " ".join(description.split()),  # unwrap the hand-wrapped paragraph
        "example_snippets": snippets.splitlines(),
    }


def _self_check() -> None:
    world = load_world("romance")
    assert set(world.keys()) == {"slug", "name", "example_snippets", "description"}
    assert world["slug"] == "romance"
    assert world["name"] == "Romance & Relationships"
    assert "\n" not in world["description"]  # hand-wrapped paragraph must unwrap to one line
    assert len(world["example_snippets"]) == 5

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

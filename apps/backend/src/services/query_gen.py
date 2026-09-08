"""Generates the next round's search queries from what the last round returned.

One text call per round, no video. The prompt is assembled in Python from two
SELECTs — the world row and the full query ledger — plus a Counter over this
round's vlm_json. The ledger is deliberately passed in whole, zero-yield rows
included: "that vein is dead" is the single most useful thing the generator can
be told, and it is only visible as a handles_found=0 row.

The model rewords past queries despite being told not to, so normalized-text
dedupe against existing rows is enforced here rather than trusted to the
prompt.
"""

import json
import logging
import re
from collections import Counter

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.db.models import CrawlPromptLog, CrawlQuery, Post, World
from src.services.crawl_config import load_query_prompt, load_seed_queries

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"
QUERIES_PER_ROUND = 10  # matches crawl.QUERIES_PER_ROUND
N_EXPLOIT = 6  # specific: go deeper on an observed term
N_EXPLORE = 4  # general: reach for an adjacent, uncovered corner
TOP_TERMS = 8

_client = genai.Client(api_key=settings.gemini_api_key)

_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["query_text", "intent", "rationale"],
        "propertyOrdering": ["query_text", "intent", "rationale"],
        "properties": {
            "query_text": {"type": "string",
                           "description": "A TikTok search query, 1-5 words, lowercase."},
            "intent": {"type": "string", "enum": ["exploit", "explore"]},
            "rationale": {"type": "string",
                          "description": "One short sentence: why this query, citing the evidence above."},
        },
    },
}

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _evidence(db: Session, world_slug: str) -> str:
    """Counter over the extracted vlm_json — what this world's videos actually
    contained, cumulative across every past round (not just the last one) —
    a term that stopped appearing is itself informative, so there's no
    round filter, only a world filter via Post.discovered_by_world (stamped
    at ingest time, see ingest.py)."""
    rows = db.execute(
        select(Post.vlm_json).where(Post.vlm_json.is_not(None), Post.discovered_by_world == world_slug)
    ).scalars().all()

    formats: Counter = Counter()
    products: Counter = Counter()
    topics: Counter = Counter()
    for obj in rows:
        if not isinstance(obj, dict):
            continue
        if fmt := obj.get("format"):
            formats[fmt] += 1
        for product in obj.get("products") or []:
            if name := (product or {}).get("name"):
                products[name] += 1
        for topic in obj.get("topics") or []:
            topics[topic] += 1

    def render(label: str, counter: Counter) -> str:
        items = ", ".join(f"{term} ({n})" for term, n in counter.most_common(TOP_TERMS))
        return f"  {label}: {items or 'none'}"

    return "\n".join(
        [
            "OBSERVED SO FAR IN THIS WORLD:",
            render("formats ", formats),
            render("products", products),
            render("topics  ", topics),
        ]
    )


# Words shared across seed lines or too generic to identify a sub-niche; the
# rest of each seed line is what a query gets substring-matched against.
_GENERIC = {"find", "finds", "haul", "hauls", "pickup", "pickups", "group",
            "out", "free", "sale", "date", "and", "the"}


def _subniche_terms(seed_line: str) -> list[str]:
    return [w for w in seed_line.lower().split() if w not in _GENERIC]


def _coverage(seeds: list[str], ledger) -> str:
    """Queries run and new handles found per seed sub-niche. Plain substring
    match against the seed line's distinctive words — deliberately not a
    classifier; it only has to be good enough to show which niche the round
    forgot. A query can count toward more than one niche."""
    lines = ["SUB-NICHE COVERAGE (queries run, new handles):"]
    for seed in seeds:
        terms = _subniche_terms(seed)
        hits = [q for q in ledger if any(t in q.query_text.lower() for t in terms)]
        handles = sum(q.new_handles or 0 for q in hits)
        lines.append(f"  {seed:<32} {len(hits):>3} queries  {handles:>5} new handles")
    return "\n".join(lines)


def _build_prompt(db: Session, world_slug: str, round_no: int) -> str:
    world = db.execute(select(World).where(World.slug == world_slug)).scalar_one()

    ledger = db.execute(
        select(CrawlQuery)
        .where(CrawlQuery.world_slug == world_slug)
        .order_by(CrawlQuery.round_no, CrawlQuery.id)
    ).scalars().all()
    lines = [f"  {q.round_no}  {q.query_text:<30} {q.handles_found:>5} {q.new_handles:>5}" for q in ledger]

    seeds = load_seed_queries(world_slug)
    return "\n".join(
        [
            f"WORLD: {world.slug} - {world.description}",
            "",
            "PAST QUERIES (round, query, handles, new handles):",
            *lines,
            "",
            _evidence(db, world_slug),
            *(["", _coverage(seeds, ledger)] if seeds else []),
        ]
    )


def generate(db: Session, world_slug: str, round_no: int) -> list[CrawlQuery]:
    """One text call; insert the surviving queries as `round_no`, status pending."""
    prompt = _build_prompt(db, world_slug, round_no)
    instruction = load_query_prompt(world_slug).format(
        QUERIES_PER_ROUND=QUERIES_PER_ROUND, N_EXPLOIT=N_EXPLOIT, N_EXPLORE=N_EXPLORE
    )
    db.merge(
        CrawlPromptLog(
            world_slug=world_slug, round_no=round_no,
            system_instruction=instruction, prompt=prompt, model=MODEL,
        )
    )
    db.commit()

    try:
        response = _client.models.generate_content(
            model=MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=1,  # the whole job is variety; 0 makes rounds converge
                system_instruction=instruction,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            ),
        )
        candidates = json.loads(response.text)
    except Exception as exc:
        logger.warning("query generation failed for round %s: %s", round_no, exc)
        return []

    existing = {
        _normalize(text)
        for text in db.execute(
            select(CrawlQuery.query_text).where(CrawlQuery.world_slug == world_slug)
        ).scalars().all()
    }

    inserted = []
    for candidate in candidates:
        text = (candidate or {}).get("query_text", "").strip()
        key = _normalize(text)
        if not key or key in existing:
            continue
        existing.add(key)
        row = CrawlQuery(
            world_slug=world_slug,
            round_no=round_no,
            query_text=text,
            intent=candidate.get("intent", "explore"),
            rationale=candidate.get("rationale"),
            status="pending",
        )
        db.add(row)
        inserted.append(row)

    db.commit()
    return inserted


def _self_check() -> None:
    from types import SimpleNamespace

    seeds = load_seed_queries("discount-shopping")
    assert len(seeds) == 10
    ledger = [
        SimpleNamespace(query_text="vintage thrift find", new_handles=12),
        SimpleNamespace(query_text="thrifting designer denim", new_handles=30),
        SimpleNamespace(query_text="dollar tree diy", new_handles=5),
        SimpleNamespace(query_text="dumpster diving bakery", new_handles=7),
    ]
    rows = {l.strip().split("  ")[0]: l for l in _coverage(seeds, ledger).splitlines()[1:]}
    assert "2 queries     42 new handles" in rows["vintage thrift find"]  # "thrifting" counts
    assert "1 queries      5 new handles" in rows["dollar store haul"]  # "dollar" alone is enough
    assert "1 queries      7 new handles" in rows["dumpster diving find"]
    assert "0 queries      0 new handles" in rows["extreme couponing haul"]  # the gap the clause exists to surface
    print("query_gen self-check ok")


if __name__ == "__main__":
    _self_check()

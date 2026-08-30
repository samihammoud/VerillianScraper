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
from src.db.models import CrawlQuery, Post, World

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"
QUERIES_PER_ROUND = 5  # testing-volume cap; matches crawl.QUERIES_PER_ROUND
N_EXPLOIT = 2  # specific: go deeper on an observed term
N_EXPLORE = 3  # general: reach for an adjacent, uncovered corner
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

INSTRUCTION = f"""\
You choose the next {QUERIES_PER_ROUND} TikTok search queries for a crawler that is
trying to find as many distinct creator accounts inside one topic world as possible.

Return exactly {QUERIES_PER_ROUND} queries: {N_EXPLOIT} with intent "exploit" and {N_EXPLORE} with intent "explore".

- exploit: go deeper on terms that actually appeared in the videos this round.
  Ground these in the observed product, format and topic terms listed below —
  not in your own assumptions about the world.
- explore: reach for an adjacent corner of the world that no past query covers.
- Never reword a past query. A query that means the same thing as one already in
  the list is wasted, even with different wording.
- A past query with 0 handles found is a dead vein. Do not go near it again.
- Queries are what a person types into TikTok search: short, lowercase, no
  punctuation, no boolean operators, no hashtags.
"""


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _evidence(db: Session) -> str:
    """Counter over the extracted vlm_json — what the videos actually contained.

    ponytail: world-blind, unlike the ledger above. Nothing links a post back to
    the query that found it, and routing (which is what decides a post's world)
    is deliberately a separate command that has not run yet when this executes —
    so at generation time a post genuinely has no world to filter on. Harmless
    while one world is crawled at a time; the moment two are, this section
    starts describing the wrong corpus. Upgrade path is posts.found_by_query_id,
    not a wider join.
    """
    rows = db.execute(select(Post.vlm_json).where(Post.vlm_json.is_not(None))).scalars().all()

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
            "THIS ROUND'S VIDEOS CONTAINED:",
            render("formats ", formats),
            render("products", products),
            render("topics  ", topics),
        ]
    )


def _build_prompt(db: Session, world_slug: str, round_no: int) -> str:
    world = db.execute(select(World).where(World.slug == world_slug)).scalar_one()

    ledger = db.execute(
        select(CrawlQuery)
        .where(CrawlQuery.world_slug == world_slug)
        .order_by(CrawlQuery.round_no, CrawlQuery.id)
    ).scalars().all()
    lines = [f"  {q.round_no}  {q.query_text:<30} {q.handles_found:>5} {q.new_handles:>5}" for q in ledger]

    return "\n".join(
        [
            f"WORLD: {world.slug} - {world.description}",
            "",
            "PAST QUERIES (round, query, handles, new handles):",
            *lines,
            "",
            _evidence(db),
        ]
    )


def generate(db: Session, world_slug: str, round_no: int) -> list[CrawlQuery]:
    """One text call; insert the surviving queries as `round_no`, status pending."""
    prompt = _build_prompt(db, world_slug, round_no)

    try:
        response = _client.models.generate_content(
            model=MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=1,  # the whole job is variety; 0 makes rounds converge
                system_instruction=INSTRUCTION,
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

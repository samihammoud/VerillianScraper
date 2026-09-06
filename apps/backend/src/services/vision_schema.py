"""The VLM contract: what Gemini is asked to extract from one video.

Free vocabulary in a fixed shape. Emerging terms surface because the free-text
fields are unconstrained; the strings still cluster because every one of them is
pinned to a lowercase noun phrase of a stated word count.

SCHEMA v2 adds the romance/dialogue layer: `premise`, `dialogue`, `relationship`,
`characters`, `synthetic`, `punchline`. v3 adds `pacing` (energy_arc,
humor_sincerity, speaker_dominance, expression_beats, density) — how the video
moves, judged only, not measured (cut counts/fps/duration are file-derived and
not asked for here). Two constraints shaped v2, still true for v3:

1. `describe_posts()` is WORLD-BLIND. It claims every undescribed post in the
   database, and at claim time routing has not run, so the post's world is not
   even known. This schema therefore applies to product videos too — every
   romance-specific field is nullable and every enum carries `not_applicable`.
   A dog-toy video must be able to answer all of them cleanly.

2. The analytical axes are ENUMS, not free text, on purpose. In terms.py the
   RAW_FACETS (cta, audio_kind) skip normalize() and skip embed+cluster entirely.
   An enum facet is therefore rankable with zero canonicalization risk — no
   single-link chaining, no threshold to tune, no `variants` to audit. Free text
   is reserved for the fields where the specific wording IS the payload
   (premise, dialogue, hook, punchline).

`flatten_for_blob` is deliberately NOT extended with the new fields. It builds
the string routing embeds, and changing it would change routing behaviour for
every world, including the already-routed pets corpus. summary + setting + topics
already carry the relationship signal.

extract_terms in terms.py is the mirror of flatten_for_blob — same input,
different projection. When SCHEMA_VERSION changes here, check there too.

RESPONSE_SCHEMA and SYSTEM_INSTRUCTION live under data/<world>/crawl/vlm_schema.json
and vlm_system_instruction.txt, alongside that world's query_prompt.txt — it's the
one part of this contract
most likely to get hand-edited field-by-field (a new enum value, a tweaked
description), and every string in it is prompt text Gemini reads, not
documentation. describe_posts() stays world-blind — this is not a per-post
choice, routing hasn't run yet at claim time. ACTIVE_SCHEMA_WORLD below is the
one deliberate, whole-process choice of which world's crawl bundle this run is
tailored for; change it and restart to switch. Two schemas exist today:
  - romance: the dialogue/relationship layer (premise, dialogue, relationship,
    characters, synthetic) for the romance world's two-person skit/dialogue
    content.
  - pets: the leaner product-discovery shape (no dialogue/relationship
    fields) for pets and the other product-centric worlds.
"""

import json

from src.services.crawl_config import DATA_DIR

SCHEMA_VERSION = 3

ACTIVE_SCHEMA_WORLD = "ai-romance-subworld"  # the one thing to change to retarget this whole process at a different world's crawl


def load_response_schema(world_slug: str) -> dict:
    return json.loads((DATA_DIR / world_slug / "crawl" / "vlm_schema.json").read_text())


def load_system_instruction(world_slug: str) -> str:
    return (DATA_DIR / world_slug / "crawl" / "vlm_system_instruction.txt").read_text()


RESPONSE_SCHEMA = load_response_schema(ACTIVE_SCHEMA_WORLD)

SYSTEM_INSTRUCTION = load_system_instruction(ACTIVE_SCHEMA_WORLD)

GENERATION_CONFIG = {
    "temperature": 0,
    "media_resolution": "MEDIA_RESOLUTION_LOW",  # the Batch API rejects the bare "low" shorthand; needs the full enum name
    "response_mime_type": "application/json",
    "response_schema": RESPONSE_SCHEMA,
}


def _schema_check() -> None:
    """Every `required` name must exist in `properties`, at every level — a typo
    here is a schema Gemini rejects for the whole batch, hours in."""

    def walk(node: dict, path: str = "") -> None:
        if node.get("type") == "object":
            props = node.get("properties", {})
            for name in node.get("required", []):
                assert name in props, f"{path}: required '{name}' not in properties"
            assert set(node.get("propertyOrdering", props)) == set(props), f"{path}: ordering mismatch"
            for name, child in props.items():
                walk(child, f"{path}.{name}")
        elif node.get("type") == "array":
            walk(node.get("items", {}), f"{path}[]")

    walk(RESPONSE_SCHEMA, "root")
    print("schema self-check ok")


def flatten_for_blob(obj: dict) -> str:
    """Render the structured extraction as the prose visual_description that
    routing embeds. Tolerant of missing/null fields — a partial extraction must
    still produce a usable blob rather than raising into the write path."""
    obj = obj or {}
    lines = []

    if summary := obj.get("summary"):
        lines.append(str(summary).strip())

    if setting := obj.get("setting"):
        lines.append(f"Setting: {setting}.")

    if topics := obj.get("topics"):
        lines.append(f"Topics: {', '.join(str(t) for t in topics)}.")

    products = obj.get("products") or []
    if products:
        rendered = []
        for product in products:
            name = (product or {}).get("name")
            if not name:
                continue
            brand = product.get("brand")
            rendered.append(f"{brand} {name}" if brand else str(name))
        if rendered:
            lines.append(f"Products: {', '.join(rendered)}.")

    return "\n".join(lines)


def _self_check() -> None:
    full = {
        "summary": "A dog eats from a slow feeder bowl.",
        "setting": "kitchen floor",
        "topics": ["puppy training", "fast eating"],
        "products": [
            {"name": "ceramic slow feeder bowl", "brand": "Neater", "prominence": "hero", "first_seen_sec": 1},
            {"name": "silicone lick mat", "brand": None, "prominence": "incidental", "first_seen_sec": 4},
        ],
        "transcript": None,
    }
    out = flatten_for_blob(full)
    assert "Neater ceramic slow feeder bowl" in out, out
    assert "silicone lick mat" in out, out
    assert "puppy training, fast eating" in out, out

    # Empty products + null transcript: no "Products:" line, no crash, still prose.
    sparse = flatten_for_blob({"summary": "A cat sleeps.", "products": [], "transcript": None, "topics": []})
    assert sparse == "A cat sleeps.", repr(sparse)

    assert flatten_for_blob({}) == ""
    assert flatten_for_blob(None) == ""

    # A products entry with no usable name must not emit a dangling "Products:".
    assert "Products" not in flatten_for_blob({"summary": "x", "products": [{"brand": "Acme"}]})

    _schema_check()
    print("vision_schema self-check ok")


if __name__ == "__main__":
    _self_check()

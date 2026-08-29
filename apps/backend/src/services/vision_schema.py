"""The VLM output contract: free vocabulary, fixed shape.

Emerging terms have to be able to surface (a closed enum would silently
collapse "silicone lick mat" into whatever bucket existed last quarter), but
the strings still need to cluster — so every free-text field is constrained to
a lowercase noun phrase of a stated word count rather than to a fixed list.

The `description` strings are prompt text the model reads, not documentation.
They carry the shape constraints and must go in verbatim.

flatten_for_blob lives here, beside the schema, for the same reason
parse_visual_description sits beside PROMPT: the renderer and the shape it
renders have to change together, and it is the only thing standing between the
VLM output and routing.
"""

SCHEMA_VERSION = 1

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["summary", "hook", "transcript", "on_screen_text", "format",
                 "format_traits", "setting", "topics", "products", "entities",
                 "audio", "creator_on_camera", "cta", "confidence"],
    "propertyOrdering": ["summary", "hook", "transcript", "on_screen_text", "format",
                         "format_traits", "setting", "topics", "products", "entities",
                         "audio", "creator_on_camera", "cta", "confidence"],
    "properties": {
        "summary": {"type": "string",
                    "description": "One sentence: what happens in the video."},
        "hook": {"type": "string", "nullable": True,
                 "description": "Verbatim spoken line and/or on-screen text in the first 3 seconds."},
        "transcript": {"type": "string", "nullable": True,
                       "description": "Verbatim spoken words in order. null if no speech."},
        "on_screen_text": {"type": "array", "items": {"type": "string"},
                           "description": "Every distinct text overlay, verbatim, in order."},
        "format": {"type": "string",
                   "description": "2-4 words, lowercase noun phrase, for how the video is "
                                  "constructed - not what it is about. Examples: 'voiceover "
                                  "product demo', 'green screen reaction', 'silent before after'."},
        "format_traits": {"type": "array", "items": {"type": "string"},
                          "description": "0-3 further 1-3 word lowercase traits. Examples: 'fast cuts', "
                                         "'trending audio', 'text only', 'pov framing', 'asmr'."},
        "setting": {"type": "string",
                    "description": "2-4 words, lowercase noun phrase. Examples: 'kitchen counter', "
                                   "'dog park', 'vet waiting room'."},
        "topics": {"type": "array", "items": {"type": "string"},
                   "description": "2-5 subjects of the video, each a 2-4 word lowercase noun phrase."},
        "products": {
            "type": "array",
            "description": "Physical products shown or named. Empty if none.",
            "items": {
                "type": "object",
                "required": ["name", "brand", "prominence", "first_seen_sec"],
                "propertyOrdering": ["name", "brand", "prominence", "first_seen_sec"],
                "properties": {
                    "name": {"type": "string",
                             "description": "2-4 words, lowercase noun phrase for the object itself, "
                                            "no brand. Examples: 'ceramic slow feeder bowl', "
                                            "'silicone lick mat'."},
                    "brand": {"type": "string", "nullable": True,
                              "description": "Only if legible on screen or spoken aloud. Never inferred."},
                    "prominence": {"type": "string", "enum": ["hero", "incidental"]},
                    "first_seen_sec": {"type": "number"},
                },
            },
        },
        "entities": {
            "type": "object",
            "required": ["people", "brands", "places", "organizations"],
            "propertyOrdering": ["people", "brands", "places", "organizations"],
            "properties": {
                "people": {"type": "array", "items": {"type": "string"},
                           "description": "Named people mentioned or shown, verbatim. Not the creator "
                                          "unless named."},
                "brands": {"type": "array", "items": {"type": "string"},
                           "description": "Brands named aloud or legible on screen, verbatim."},
                "places": {"type": "array", "items": {"type": "string"},
                           "description": "Cities, countries, regions, or venues named or clearly shown "
                                          "by signage, verbatim."},
                "organizations": {"type": "array", "items": {"type": "string"},
                                  "description": "Companies, clinics, shelters, retailers named, verbatim."},
            },
        },
        "audio": {
            "type": "object",
            "required": ["kind", "spoken_language", "speaker_count"],
            "propertyOrdering": ["kind", "spoken_language", "speaker_count"],
            "properties": {
                "kind": {"type": "string",
                         "enum": ["direct_speech", "voiceover", "music_only", "ambient", "silent"]},
                "spoken_language": {"type": "string", "nullable": True,
                                    "description": "BCP-47 code of the speech, e.g. 'en', 'es'. null if no speech."},
                "speaker_count": {"type": "integer"},
            },
        },
        "creator_on_camera": {"type": "boolean"},
        "cta": {"type": "string", "enum": ["comment", "link_in_bio", "follow", "shop_now", "none"]},
        "confidence": {"type": "string", "enum": ["low", "med", "high"]},
    },
}

SYSTEM_INSTRUCTION = """\
You analyze short-form vertical videos and return structured JSON.

- Report only what is seen in the video or heard in its audio. Never infer or
  draw on outside knowledge about brands, people, or places.
- If a field is not determinable, use null, or an empty array.
- brand and entities are verbatim only: legible on screen or spoken aloud. A
  logo you recognize but cannot read is not legible.
- products are physical goods only. Not services, apps, locations, or software.
- prominence is "hero" if the video is about the product, "incidental" if it is
  merely present in frame.
- format, format_traits, setting, topics and product names are free text, but
  always lowercase noun phrases of the stated word count. No sentences, no
  articles, no punctuation. Describe, do not editorialize: "voiceover product
  demo", not "engaging demo". Reuse the plainest wording you would use for any
  similar video.
- transcript and on_screen_text are verbatim. Do not summarize, correct, or
  translate.
"""

GENERATION_CONFIG = {
    "temperature": 0,
    # "low" in the spec; the SDK's enum name for it. ~100 tokens per second of
    # video vs ~300 at the default.
    "media_resolution": "MEDIA_RESOLUTION_LOW",
    "response_mime_type": "application/json",
    "response_schema": RESPONSE_SCHEMA,
    "system_instruction": SYSTEM_INSTRUCTION,
}


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

    print("vision_schema self-check ok")


if __name__ == "__main__":
    _self_check()

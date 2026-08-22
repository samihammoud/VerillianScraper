"""Describes a post's thumbnail with a VLM, for the visual modality routing signal.

The thumbnail/cover URL from the scrape is treated as the frame — no video
download, no ffmpeg. Descriptions are written for retrieval (product/setting/
action/category cues), not for a person, and temperature 0 for reproducibility.
"""

import httpx
from google import genai
from google.genai import types

from src.config.settings import settings

MODEL = "gemini-3.6-flash"  # gemini-2.5-flash (per spec) is deprecated for new API keys
MAX_ATTEMPTS = 3  # initial attempt + 2 retries

_client = genai.Client(api_key=settings.gemini_api_key)

PROMPT = """You are describing a single still frame from a short-form social video (the
creator-chosen cover image) so it can be matched against consumer product
categories. Write for a retrieval system, not for a person.

Output exactly these five lines and nothing else. No preamble, no markdown.

PRODUCTS: Every commercial product or object visible. Be specific about form
factor and material — "amber glass dropper bottle", "capsule blister pack",
"powdered drink tub with scoop", "wrist-worn tracker with LED display", "cast
iron skillet", "raised plush dog bed". If none, write "none visible".

TEXT: All on-screen text, verbatim — overlays, packaging labels, burned-in
captions, UI elements. Preserve exact wording and spelling. If none, write "none".

SETTING: Where this is, in 3-6 words. e.g. "bathroom vanity counter", "gym floor",
"kitchen stovetop", "nursery".

ACTION: What is being done with or to the product, in 3-8 words. e.g. "applying
serum to cheek", "pouring powder into shaker bottle". If nothing is being done,
write "product displayed, no action".

CATEGORY CUES: 3-6 comma-separated noun phrases a shopper would use to describe
this product area. e.g. "skincare routine, anti-aging serum, topical treatment".

Rules:
- Describe only what is visibly present. Never infer a brand, ingredient, price,
or health claim that is not written in the frame.
- Do not describe people's appearance, clothing, body, or attractiveness — unless
the clothing or accessory IS the product being featured.
- No hedging. Do not write "appears to be" or "possibly". If uncertain about
identity, describe the visible physical form instead of guessing a name.
- Total output under 120 words."""


def describe_thumbnail(thumbnail_url: str) -> str | None:
    """Return a VLM description of the thumbnail, or None if it couldn't be generated."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            image_response = httpx.get(thumbnail_url, timeout=30.0)
            image_response.raise_for_status()
            image_bytes = image_response.content
            response = _client.models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    PROMPT,
                ],
                config=types.GenerateContentConfig(temperature=0),
            )
            return response.text.strip()
        except Exception:
            if attempt == MAX_ATTEMPTS - 1:
                return None
    return None

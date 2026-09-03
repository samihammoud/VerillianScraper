"""Assembles the text blob that gets embedded for routing.

Visual description only, budget-truncated against the same tokenizer the
embedding model (text-embedding-3-small) uses, so the budget maps to real
cost. Caption/comments are no longer part of the routing blob — kept purely
topical to the VLM's video description.
"""

import tiktoken

_ENCODING = tiktoken.encoding_for_model("text-embedding-3-small")

VISUAL_TOKEN_BUDGET = 150


def _truncate(text: str, max_tokens: int) -> str:
    tokens = _ENCODING.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _ENCODING.decode(tokens[:max_tokens])


def assemble_blob(visual_description: str | None) -> str:
    """Build the budget-truncated blob that gets embedded for routing.

    Visual description only — caption/comments dropped per the call to embed
    just the VLM's video description, keeping routing signal purely topical.
    """
    if not visual_description or not visual_description.strip():
        return ""
    return _truncate(visual_description.strip(), VISUAL_TOKEN_BUDGET)

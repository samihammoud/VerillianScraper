"""Assembles the single text blob that gets embedded for routing.

One blob, one embed() call, one vector — no weighted fusion, no per-modality
vectors. Section token budgets (not weights) are what balance visual/caption/
comment signal against each other. Counted against the same tokenizer the
embedding model (text-embedding-3-small) uses, so budgets map to real cost.

Comment filtering matters more here than under weighted fusion: off-topic
comment tokens pull the single vector off the product signal and there's no
way to down-weight them afterward.
"""

import re

import tiktoken

_ENCODING = tiktoken.encoding_for_model("text-embedding-3-small")

VISUAL_TOKEN_BUDGET = 150
CAPTION_TOKEN_BUDGET = 40
COMMENTS_TOKEN_BUDGET = 150
PER_COMMENT_TOKEN_BUDGET = 20
TOP_N_COMMENTS = 8
MIN_COMMENT_CHARS = 15  # drops "1st", "lol", bare reactions
MIN_ALPHA_RATIO = 0.5  # drops emoji walls

_ALPHA_RE = re.compile(r"[A-Za-z]")


def token_count(text: str) -> int:
    return len(_ENCODING.encode(text))


def _truncate(text: str, max_tokens: int) -> str:
    tokens = _ENCODING.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _ENCODING.decode(tokens[:max_tokens])


def _is_low_signal(text: str) -> bool:
    """Too short, or mostly non-alphabetic (emoji walls, bare reactions)."""
    stripped = text.strip()
    if len(stripped) < MIN_COMMENT_CHARS:
        return True
    alpha_chars = len(_ALPHA_RE.findall(stripped))
    return alpha_chars < len(stripped) * MIN_ALPHA_RATIO


def select_comments(comments: list[dict]) -> list[str]:
    """Top N by engagement, filtered, truncated, budget-accumulated.

    Uses `continue` rather than `break` on hitting the per-comment budget: one
    early comment that doesn't fit shouldn't stop shorter survivors behind it
    from being considered.
    """
    top = sorted(comments, key=lambda c: c.get("likes") or 0, reverse=True)[:TOP_N_COMMENTS]
    survivors = [c.get("text") or "" for c in top if not _is_low_signal(c.get("text") or "")]

    kept: list[str] = []
    used_tokens = 0
    for text in survivors:
        truncated = _truncate(text, PER_COMMENT_TOKEN_BUDGET)
        tokens = token_count(truncated)
        if used_tokens + tokens > COMMENTS_TOKEN_BUDGET:
            continue
        kept.append(truncated)
        used_tokens += tokens

    return kept


def assemble_blob(caption: str | None, comments: list[dict], visual_description: str | None) -> str:
    """Build the labeled, budget-truncated blob that gets embedded for routing.

    Sections are omitted entirely when empty — never emit a bare "Comments:"
    label with nothing after it. Labels are kept semantically inert (e.g. not
    "Product shown:") so they don't nudge every blob toward one world.
    """
    lines = []

    if visual_description and visual_description.strip():
        visual = _truncate(visual_description.strip(), VISUAL_TOKEN_BUDGET)
        lines.append(f"Visual: {visual}")

    if caption and caption.strip():
        cap = _truncate(caption.strip(), CAPTION_TOKEN_BUDGET)
        lines.append(f"Caption: {cap}")

    kept_comments = select_comments(comments)
    if kept_comments:
        lines.append(f"Comments: {' | '.join(kept_comments)}")

    return "\n".join(lines)

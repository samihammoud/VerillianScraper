"""Phase 7 stage 0: turn one post's vlm_json into (facet, term) rows.

extract_terms is the mirror of vision_schema.flatten_for_blob — same input,
different projection. When SCHEMA_VERSION changes there, check here too.

normalize() is Stage A of canonicalization (see overview.py for Stage B,
the embed+cluster pass): lowercase, strip punctuation/articles, collapse
whitespace, singularize the head noun. Pure function, no I/O — this alone
collapses most of the tail ("Slow Feeder Bowl" / "slow feeder bowls" /
"slow-feeder bowl" all land on the same string) before anything needs an
embedding call.
"""

import re
from dataclasses import dataclass

# Which stage-B treatment each facet gets in overview.py:
#  - CLUSTERED: normalize, then embed + cluster into a canon_term
#  - NORMALIZE_ONLY: normalize, but canon_term stays == norm_term — brands
#    must stay verbatim distinct ("Pingu" and "Penguin" are not the same brand)
#  - RAW: closed enums; canon_term == raw_term untouched, no normalize either
CLUSTERED_FACETS = {"product", "format", "format_trait", "topic", "setting"}
NORMALIZE_ONLY_FACETS = {"brand", "music_author"}
RAW_FACETS = {"cta", "audio_kind", "audio_source"}
ALL_FACETS = CLUSTERED_FACETS | NORMALIZE_ONLY_FACETS | RAW_FACETS


@dataclass(frozen=True)
class Term:
    facet: str
    raw_term: str
    prominence: str | None = None  # product facet only
    low_conf: bool = False


_ARTICLES = {"a", "an", "the"}
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _singularize(word: str) -> str:
    """ponytail: naive suffix stripping, no irregular plurals (mice, leaves).
    Good enough for the noun phrases the VLM prompt constrains it to; revisit
    if phase 1's printed list shows it merging things it shouldn't."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize(text: str) -> str:
    text = text.strip().lower().replace("-", " ")
    text = _PUNCT_RE.sub("", text)
    words = [w for w in _WS_RE.split(text.strip()) if w and w not in _ARTICLES]
    if not words:
        return ""
    words[-1] = _singularize(words[-1])
    return " ".join(words)


def _dedupe(terms: list[Term]) -> list[Term]:
    """post_terms PK is (post_id, facet, raw_term) — first occurrence wins
    on a duplicate raw_term within one post/facet (e.g. the same brand named
    in both products[].brand and entities.brands)."""
    seen = set()
    out = []
    for term in terms:
        key = (term.facet, term.raw_term)
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def extract_terms(vlm_json: dict | None) -> list[Term]:
    """One post's vlm_json -> its (facet, raw_term) edges. confidence == 'low'
    on the post flags every term it produces; the rollup excludes them by
    default and reports how many."""
    if not vlm_json:
        return []

    low_conf = vlm_json.get("confidence") == "low"
    terms: list[Term] = []

    for product in vlm_json.get("products") or []:
        product = product or {}
        if name := product.get("name"):
            terms.append(Term("product", name, prominence=product.get("prominence"), low_conf=low_conf))
        if brand := product.get("brand"):
            terms.append(Term("brand", brand, low_conf=low_conf))

    for brand in (vlm_json.get("entities") or {}).get("brands") or []:
        if brand:
            terms.append(Term("brand", brand, low_conf=low_conf))

    if fmt := vlm_json.get("format"):
        terms.append(Term("format", fmt, low_conf=low_conf))

    for trait in vlm_json.get("format_traits") or []:
        if trait:
            terms.append(Term("format_trait", trait, low_conf=low_conf))

    for topic in vlm_json.get("topics") or []:
        if topic:
            terms.append(Term("topic", topic, low_conf=low_conf))

    if setting := vlm_json.get("setting"):
        terms.append(Term("setting", setting, low_conf=low_conf))

    if cta := vlm_json.get("cta"):
        terms.append(Term("cta", cta, low_conf=low_conf))

    if audio_kind := (vlm_json.get("audio") or {}).get("kind"):
        terms.append(Term("audio_kind", audio_kind, low_conf=low_conf))

    return _dedupe(terms)


def extract_scrape_terms(music_original: bool | None, music_author: str | None) -> list[Term]:
    """Audio provenance from TikTok's own music_info metadata (posts.music_*),
    not the VLM — Gemini can't identify a song from watching a clip, but the
    scrape already says whether the sound is original or a licensed/trending
    track, and who made it. Separate from extract_terms since the input isn't
    vlm_json; low_conf doesn't apply (nothing here comes from the VLM)."""
    terms: list[Term] = []
    if music_original is not None:
        terms.append(Term("audio_source", "original_audio" if music_original else "licensed_audio"))
    if music_author and music_original is False:
        terms.append(Term("music_author", music_author))
    return terms


def _self_check() -> None:
    assert normalize("Slow Feeder Bowl") == "slow feeder bowl"
    assert normalize("slow feeder bowls") == "slow feeder bowl"
    assert normalize("slow-feeder bowl") == "slow feeder bowl"
    assert normalize("The Dog Park") == "dog park"
    assert normalize("  ") == ""
    assert normalize("puppies") == "puppy"

    full = {
        "products": [
            {"name": "ceramic slow feeder bowl", "brand": "Neater", "prominence": "hero"},
            {"name": "silicone lick mat", "brand": None, "prominence": "incidental"},
        ],
        "format": "voiceover product demo",
        "format_traits": ["fast cuts", "trending audio"],
        "topics": ["puppy training", "fast eating"],
        "setting": "kitchen floor",
        "entities": {"brands": ["Neater"], "people": [], "places": [], "organizations": []},
        "cta": "shop_now",
        "audio": {"kind": "voiceover", "spoken_language": "en", "speaker_count": 1},
        "confidence": "high",
    }
    terms = extract_terms(full)
    by_facet: dict[str, list[Term]] = {}
    for t in terms:
        by_facet.setdefault(t.facet, []).append(t)

    assert [t.raw_term for t in by_facet["product"]] == ["ceramic slow feeder bowl", "silicone lick mat"]
    assert by_facet["product"][0].prominence == "hero"
    assert by_facet["product"][1].prominence == "incidental"
    # "Neater" named in both products[].brand and entities.brands — deduped to one row
    assert [t.raw_term for t in by_facet["brand"]] == ["Neater"]
    assert by_facet["format"][0].raw_term == "voiceover product demo"
    assert [t.raw_term for t in by_facet["format_trait"]] == ["fast cuts", "trending audio"]
    assert [t.raw_term for t in by_facet["topic"]] == ["puppy training", "fast eating"]
    assert by_facet["setting"][0].raw_term == "kitchen floor"
    assert by_facet["cta"][0].raw_term == "shop_now"
    assert by_facet["audio_kind"][0].raw_term == "voiceover"
    assert all(not t.low_conf for t in terms)

    # empty products, null hook/transcript: no product/brand terms, no crash
    sparse = extract_terms({"products": [], "topics": ["x"], "format": None, "hook": None, "transcript": None})
    assert "product" not in {t.facet for t in sparse}
    assert "brand" not in {t.facet for t in sparse}
    assert [t.raw_term for t in sparse if t.facet == "topic"] == ["x"]

    # low confidence flags every term the post produces
    low = extract_terms({"topics": ["y"], "confidence": "low"})
    assert low[0].low_conf is True

    assert extract_terms(None) == []
    assert extract_terms({}) == []

    # licensed track: both audio_source and music_author
    licensed = extract_scrape_terms(music_original=False, music_author="Imagine Dragons")
    assert {t.facet: t.raw_term for t in licensed} == {"audio_source": "licensed_audio", "music_author": "Imagine Dragons"}
    # original sound: audio_source only — no "artist" for a creator's own sound
    original = extract_scrape_terms(music_original=True, music_author=None)
    assert {t.facet: t.raw_term for t in original} == {"audio_source": "original_audio"}
    # unknown (scrape didn't return music_info): nothing
    assert extract_scrape_terms(music_original=None, music_author=None) == []

    print("terms self-check ok")


if __name__ == "__main__":
    _self_check()

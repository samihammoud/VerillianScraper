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
CLUSTERED_FACETS = {"product", "format", "format_trait", "topic", "setting", "characters_dynamic"}
NORMALIZE_ONLY_FACETS = {"brand", "music_author"}
RAW_FACETS = {
    "cta", "audio_kind", "audio_source",
    # SCHEMA v2/v3 romance/dialogue layer — closed enums, so RAW like cta/audio_kind:
    # canon_term == raw_term, no embed/cluster cost for values that are already canonical.
    "relationship_stage", "relationship_conflict", "relationship_register",
    "relationship_resolution", "relationship_perspective", "characters_pairing",
    "synthetic_presenter", "synthetic_voice",
    "pacing_energy_arc", "pacing_humor_sincerity", "pacing_speaker_dominance", "pacing_density",
    # Phase 9 layer 2 — cross-tab cells, e.g. "attention_neglect x funny". Still closed-enum
    # combinations under the hood, so still RAW: canon_term == raw_term, no clustering.
    "conflict_x_register", "register_x_resolution", "punchline_presence",
}
ALL_FACETS = CLUSTERED_FACETS | NORMALIZE_ONLY_FACETS | RAW_FACETS

# "not_applicable"/"unclear" mean "this field doesn't apply to this video" or "the
# VLM couldn't tell" — filler, not signal, and would otherwise dominate every one
# of these facets since describe_posts() is world-blind and runs this schema
# against product videos too (see vision_schema.py). "none" is excluded from this
# set on purpose: for fields like synthetic.voice or characters.pairing, "none" is
# a real, substantive answer ("no voice track", "no people on screen"), not filler.
_FILLER_VALUES = {"not_applicable", "unclear"}


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

    relationship = vlm_json.get("relationship") or {}
    for facet, key in [
        ("relationship_stage", "stage"),
        ("relationship_conflict", "conflict"),
        ("relationship_register", "register"),
        ("relationship_resolution", "resolution"),
        ("relationship_perspective", "perspective"),
    ]:
        if (value := relationship.get(key)) and value not in _FILLER_VALUES:
            terms.append(Term(facet, value, low_conf=low_conf))

    # Phase 9 layer 2 (CLAUDEphase9romanceoverview.md): rank *cells*, not columns —
    # "attention_neglect x funny" and "attention_neglect x venting" can have wildly
    # different lift even though "attention_neglect" alone averages them together.
    conflict, register, resolution = relationship.get("conflict"), relationship.get("register"), relationship.get("resolution")
    if conflict and conflict not in _FILLER_VALUES and register and register not in _FILLER_VALUES:
        terms.append(Term("conflict_x_register", f"{conflict} x {register}", low_conf=low_conf))
    if register and register not in _FILLER_VALUES and resolution and resolution not in _FILLER_VALUES:
        terms.append(Term("register_x_resolution", f"{register} x {resolution}", low_conf=low_conf))

    # Doc's "surface a single derived flag": does landing on a turn/joke/reversal
    # correlate with performance at all. Recorded whenever the schema asked the
    # question (key present, v3+) — punchline being null is a real answer, not a
    # missing one, so both states are worth their own row, not just the positive.
    if "punchline" in vlm_json:
        terms.append(Term("punchline_presence", "has_punchline" if vlm_json.get("punchline") else "no_punchline", low_conf=low_conf))

    characters = vlm_json.get("characters") or {}
    if (pairing := characters.get("pairing")) and pairing not in _FILLER_VALUES:
        terms.append(Term("characters_pairing", pairing, low_conf=low_conf))
    if dynamic := characters.get("dynamic"):
        terms.append(Term("characters_dynamic", dynamic, low_conf=low_conf))

    synthetic = vlm_json.get("synthetic") or {}
    if (presenter := synthetic.get("presenter")) and presenter not in _FILLER_VALUES:
        terms.append(Term("synthetic_presenter", presenter, low_conf=low_conf))
    if (voice := synthetic.get("voice")) and voice not in _FILLER_VALUES:
        terms.append(Term("synthetic_voice", voice, low_conf=low_conf))

    pacing = vlm_json.get("pacing") or {}
    for facet, key in [
        ("pacing_energy_arc", "energy_arc"),
        ("pacing_humor_sincerity", "humor_sincerity"),
        ("pacing_speaker_dominance", "speaker_dominance"),
        ("pacing_density", "density"),
    ]:
        if (value := pacing.get(key)) and value not in _FILLER_VALUES:
            terms.append(Term(facet, value, low_conf=low_conf))

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
        "punchline": "and that's why he's single now",
        "relationship": {
            "stage": "dating", "conflict": "jealousy_trust", "register": "petty",
            "resolution": "punchline", "perspective": "woman",
        },
        "characters": {"count": 2, "pairing": "romantic_couple", "dynamic": "anxious partner calm partner"},
        "synthetic": {"presenter": "real_person", "voice": "human", "signals": [], "certainty": "high"},
        "pacing": {
            "energy_arc": "build_to_punch", "humor_sincerity": "balanced",
            "speaker_dominance": "balanced", "density": "conversational", "expression_beats": [],
        },
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
    assert by_facet["relationship_stage"][0].raw_term == "dating"
    assert by_facet["relationship_conflict"][0].raw_term == "jealousy_trust"
    assert by_facet["relationship_register"][0].raw_term == "petty"
    assert by_facet["relationship_resolution"][0].raw_term == "punchline"
    assert by_facet["relationship_perspective"][0].raw_term == "woman"
    assert by_facet["conflict_x_register"][0].raw_term == "jealousy_trust x petty"
    assert by_facet["register_x_resolution"][0].raw_term == "petty x punchline"
    assert by_facet["punchline_presence"][0].raw_term == "has_punchline"
    assert by_facet["characters_pairing"][0].raw_term == "romantic_couple"
    assert by_facet["characters_dynamic"][0].raw_term == "anxious partner calm partner"
    assert by_facet["synthetic_presenter"][0].raw_term == "real_person"
    assert by_facet["synthetic_voice"][0].raw_term == "human"
    assert by_facet["pacing_energy_arc"][0].raw_term == "build_to_punch"
    assert by_facet["pacing_humor_sincerity"][0].raw_term == "balanced"
    assert by_facet["pacing_speaker_dominance"][0].raw_term == "balanced"
    assert by_facet["pacing_density"][0].raw_term == "conversational"
    assert all(not t.low_conf for t in terms)

    # not_applicable/unclear are filler for non-relationship videos — excluded.
    # "none" stays (synthetic.voice: none = real signal, not filler).
    filler = extract_terms({
        "relationship": {"stage": "not_applicable", "conflict": "not_applicable", "register": "not_applicable",
                          "resolution": "not_applicable", "perspective": "not_applicable"},
        "characters": {"count": 0, "pairing": "none", "dynamic": None},
        "synthetic": {"presenter": "unclear", "voice": "none", "signals": [], "certainty": "low"},
        "pacing": {"energy_arc": "not_applicable", "humor_sincerity": "not_applicable",
                   "speaker_dominance": "not_applicable", "density": "sparse", "expression_beats": []},
        "punchline": None,
    })
    filler_facets = {t.facet for t in filler}
    assert "relationship_stage" not in filler_facets
    assert "synthetic_presenter" not in filler_facets
    assert "pacing_energy_arc" not in filler_facets
    # cross-tab cells require BOTH sides non-filler — an all-not_applicable post forms none
    assert "conflict_x_register" not in filler_facets
    assert "register_x_resolution" not in filler_facets
    assert {t.raw_term for t in filler if t.facet == "characters_pairing"} == {"none"}
    assert {t.raw_term for t in filler if t.facet == "synthetic_voice"} == {"none"}
    assert {t.raw_term for t in filler if t.facet == "pacing_density"} == {"sparse"}
    # punchline key present but null is still a real answer — "no_punchline", not absent
    assert {t.raw_term for t in filler if t.facet == "punchline_presence"} == {"no_punchline"}

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

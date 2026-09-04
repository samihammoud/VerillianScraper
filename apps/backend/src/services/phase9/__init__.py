"""Phase 9 — romance world overview strategy (CLAUDEphase9romanceoverview.md).

Layers 1-3 are wired directly into `overview.rollup()` (they write into the
same `post_terms`/`world_term_stats` shape every other facet uses, per the
doc's own note that no new schema is needed through step 3):

- Layer 1 — `overview._premise_clusters`. Situation-space clustering over
  `premise` (fallback: summary+topics for pre-schema posts, excluding VLM-
  confirmed non-relationship content). PCA(100) + HDBSCAN(min_cluster_size=8)
  after `cluster_by_threshold` (single-link) was tried and ruled out — no
  threshold gave a usable middle ground between all-singletons and giant
  merged blobs. facet='premise_cluster'.
- Layer 2 — `terms.extract_terms`. Cross-tab *cells*, not columns:
  conflict_x_register, register_x_resolution, punchline_presence.
- Layer 3 — `overview._hook_clusters` / `_punchline_clusters`, same
  embed+cluster+rank machinery as layer 1 (factored into
  `overview._text_field_clusters`). Hook space works (facet='hook_cluster',
  10 clusters on the real corpus). Punchline space runs without error but
  currently returns 0 clusters above the support floor — punchlines are
  verbatim one-liners with far more lexical variety than premises, so the
  layer-1-tuned min_cluster_size doesn't fit; needs its own sweep via
  `tune_premise_threshold.py` (same script works on any text field, not just
  premise — just point `_candidate_posts`/`_premise_text` at a different
  field before running it).

Tools in this package, each independently runnable:

- `tune_premise_threshold.py` — embeds once (cached to `out/phase9_cache/`),
  sweeps PCA dims x HDBSCAN min_cluster_size, targets a cluster count scaled
  to the actual corpus size rather than the doc's raw ~10k-post reference.
- `recency_quadrant.py` — build order step 3. Splits a facet's clusters into
  recent vs. older by posted_at, classifies each into emerging/proven/dead/
  saturated on volume x lift. Works on any facet, not just premise_cluster.
- `stratify.py` — recomputes lift for a facet's terms within a stratum
  (dotted vlm_json path equality, e.g. `synthetic.presenter`, or a duration
  bucket via `estimated_duration_sec:lt15`) instead of against the whole
  world — the doc's "the global ranking measures a format you can't run."
- `cluster_profiles.py` — build order step 5. Writes `topology.cluster_profiles`:
  per-cluster enum distributions, median duration/dialogue-turns, and each
  `relationship_register` value's view_ratio *within* that cluster (not
  against the world) — flags `register_mismatch` when the modal register
  differs from the highest-lift one. Deliberately does NOT implement the
  doc's "Fit" check (matching a cluster's profile against one specific
  account's target profile) — no such target profile exists to compute
  against here; only the objective half (distributions + mismatch) is built.

Not started at all:
- Second metric (comment-rate resonance vs. view lift) — blocked, not just
  unbuilt: only 3 of 5265 romance posts have `comments` backfilled (`make
  enrich` was never run for this world). Building the computation now would
  produce empty output.
- Cheap derived features (n_turns, word ratios, hook_from_dialogue vs.
  on-screen-text hook, expression-beat lift, cta confirmation) — not started.
"""

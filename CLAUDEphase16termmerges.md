# Phase 16 — LLM term overrides on top of embedding clustering (test pass)

## Scope — test only

**This pass is a test, limited to one world and one facet:**

- world: `discount-shopping` (hyphen, not underscore — the slug in `topology.worlds`)
- facet: `topic` (the **Winning Topics** panel)

No other world or facet is exported, overridden, or changed. Everything below is built so the mechanism works for any `(world, facet)`, but the data loaded in this pass covers only `discount-shopping` / `topic`. Other worlds and facets must produce identical rankings before and after this phase.

**If the test works,** the same procedure rolls out to every world and every facet in a later phase. That rollout is data (more export groups, more override rows), not new code.

The facet string is `topic` (confirmed in `src/services/terms.py` — that is what is written to `post_terms.facet`).

---

## Status — v1 is built and shipped

v1 (merge-only) was implemented and committed as `affe845 "test llm clustering"`. What exists today:

- `src/scripts/export_merge_candidates.py` — `--world` / `--facet`, writes `data/term_merges/<world>__<facet>.candidates.json`
- `src/scripts/load_term_merges.py` — validation, chain resolution, upsert, `--demo` self-check
- `alembic/versions/b41e7c0d9a52_term_merges.py` — `topology.term_merges`, at head
- `src/services/overview.py` — `_apply_term_merges()` called inside `rollup()`
- `data/term_merges/discount-shopping__topic.{candidates.json,merges.csv}` — 1019 candidates, 241 merge rows

v1 result on `discount-shopping` / `topic`: **2058 → 1817 ranked terms**, 241 absorbed into 144 groups, 1673 untouched, zero changes unexplained by a merge row. Median lift across topic terms +0.023. Every other world × facet identical. Two consecutive `make overview` runs produced byte-identical rows.

**This document now specifies v2**, which extends that work rather than replacing it. Everything in "Phases" below describes the end state; the "what changes from v1" notes say what is new.

---

## Problem

The free-form VLM coins a new phrasing per post. Normalization + embedding clustering (phase 7) catches spelling and near-duplicates, and v1's LLM merges caught cross-phrasing synonyms, but Winning Topics still has too many clusters, for three reasons:

1. **Synonyms not merged** — e.g. `flea market haggling` / `price negotiation`. v1 fixed many of these but was deliberately conservative.
2. **Characteristics ranked as topics** — `paint stripping` is part of a furniture makeover, not a topic anyone would title a video with. It ranks at 14.69x on 23 posts while never being the only topic on any of them.
3. **Vague umbrella topics** — `free discarded item` is 47% curb finds and 47% dumpster finds and tells us nothing on its own.

A merge cannot express (2) or (3). Merging a characteristic into its parent inflates the parent's counts with posts that aren't about it; dropping is not a merge at all. So the vocabulary of decisions has to widen.

## Fix

Have an LLM assign each ranked term one of four **actions**, store the decision in its own table keyed on `norm_term`, and re-apply it on every `rollup()`. The embedding clustering stays; overrides are a layer on top of it.

| action | meaning | effect |
|---|---|---|
| `keep` | a hero topic, stands on its own | nothing; no row stored |
| `merge` | a synonym of `target` | `canon_term` rewritten to `target` before stats (v1 behavior) |
| `child_of` | a characteristic of `target` | stays a row, gets `parent_term = target`, shown nested |
| `drop` | vague umbrella, no information of its own | stats row deleted |

**`child_of` is not a merge.** The child keeps its own `n_posts`, `n_accounts` and lift, and its posts are **not** added to the parent's counts. It moves out of the top-level ranking and under its parent, where it stays readable as "this is what these videos involve."

**`drop` removes the term, not the posts.** A post carrying a dropped term still counts under its other terms — `free discarded item`'s 15 posts all carry `curbside salvage` or `dumpster diving find` too, and keep counting there.

Terms in scope: those clearing **`n_posts >= 6` and `n_accounts >= 4`**. The long tail below the floor is not mapped.

## Rules — read before writing code

- **Do not UPDATE `post_terms.canon_term` outside `rollup()`.** `post_terms` is truncated and rebuilt from `vlm_json` on every `make overview`; a one-off edit silently vanishes on the next run. The override table is an *input* to the rebuild, applied inside it.
- **Key overrides on `norm_term`, never on `canon_term`.** A cluster's label is its highest-post-count member and can change as posts arrive. `norm_term` is a pure function of the string and never drifts. This applies to all four actions, not just `merge`.
- **Overrides never cross facets or worlds.**
- **The LLM never invents terms.** Every `target` is an existing input term, so the displayed label is always something the VLM actually said.
- **The candidates file is the frozen reference.** The loader validates against it, never against live `world_term_stats` — which is what lets a later delta pass reference a term that a previous load has already absorbed.
- Before starting, read `src/services/overview.py` (`canonicalize`, `rollup`, `_apply_term_merges`), `src/services/terms.py`, `src/routes/worlds.py` and the existing migration pattern.

---

## Phases

Each ends at something runnable. Stop after each phase and report.

### Phase 0 — export

`src/scripts/export_merge_candidates.py` takes `--world` and `--facet` (this pass: `discount-shopping`, `topic`) and writes `data/term_merges/<world>__<facet>.candidates.json`, terms sorted by `n_posts` desc, each carrying:

- `world`, `facet`, `canon_term`, `n_posts`, `n_accounts`
- `norm_terms` — every `norm_term` currently under that `canon_term`
- `variants` — the raw phrasings, from `world_term_stats.variants`
- **`co_occurring`** *(new in v2)* — the top 5 other topic `canon_term`s appearing on the same posts, each as `{term, shared_posts, share}` where `share = shared_posts / n_posts` of the term being described, sorted by `shared_posts` desc

`norm_terms` are stored in the file so Phase 2 loads from the file, not from live `post_terms` — a `make overview` between export and load cannot shift the mapping.

Co-occurrence uses `DISTINCT (post_id, canon_term)` over `post_terms` for the same `(world, facet)`, self-joined on `post_id`, excluding the term itself.

*Check:* print the term count for `discount-shopping` / `topic`. (v1: 1019.)

**What changes from v1:** the `co_occurring` block. Everything else already exists.

### Phase 1 — LLM pass (manual, done by Sami)

One LLM call over the candidates file. Result saved as `data/term_merges/<world>__<facet>.overrides.csv` with header `world,facet,term,action,target`.

Prompt:

> You are consolidating labels from a product-discovery pipeline. Below is a list of ranked terms for world `discount-shopping`, facet `topic`. Each term has its post count, account count, the normalized and raw phrasings grouped under it, and the topics it most often co-occurs with (`share` = the fraction of this term's posts that also carry that other topic).
>
> Definitions:
>
> - A **hero topic** is one that could be the video's title.
> - A **characteristic** is present in the video but you wouldn't title it that way.
>
> Assign every term exactly one action:
>
> - `merge` — the term is a synonym or alternate phrasing of another term in the list. `target` is the term to merge into; prefer the one with the most posts. Regional and phrasing synonyms for the same activity count as synonyms (`yard sale` / `garage sale`, `dumpster dive` / `dumpster diving`), but pair like with like: an activity term merges into an activity term, an outcome term into an outcome term.
> - `child_of` — the term is a characteristic of another term in the list. Use when one co-occurring topic has `share >= 0.5`. `target` is that topic.
> - `drop` — the term's posts are split across 2 or more more-specific topics and it adds no information of its own. No `target`.
> - `keep` — a hero topic that stands on its own. Use when no co-occurring topic has `share` above roughly 0.3.
>
> Rules:
>
> - `target` must be one of the input terms exactly as written. Never invent a term.
> - A term used as a `target` must itself be `keep`. Do not point at a term you are merging, dropping, or nesting elsewhere.
> - When unsure, `keep`.
> - Output CSV with header `world,facet,term,action,target`. `target` is empty for `keep` and `drop`. No commentary.

**This replaces v1's "do not merge a more specific term into a more general one" rule.** That rule existed because there was nowhere for a specific term to go except oblivion. With `child_of`, specific terms stay visible as sub-topics, which is the better outcome — `curb alert furniture` becomes a child of `curb alert find` rather than either vanishing into it or cluttering the top level.

**Known limitation, measured in step 1:** `yard sale video game` (8 posts) has a top co-occurrence share of 0.25 and would be `keep`ed by the ≥0.5 rule, even though it is plainly a sub-case of yard-sale shopping. Its affinity is to a *family* of terms (`yard sale shopping` / `negotiation` / `find` / `haul`), not to any single one, and its 8 posts scatter 1–2 across them. Co-occurrence cannot see that. Accepted for this pass; the fix is to merge the yard/garage family first and re-export so the shares concentrate.

### Phase 2 — validate and load

1. Migration:
   - rename `topology.term_merges` → `topology.term_overrides`
   - add `action TEXT NOT NULL` (`merge` | `child_of` | `drop`), backfilling existing rows to `'merge'`
   - rename `canon_term` → `target_term`, made nullable (`drop` has no target)
   - add `parent_term TEXT NULL` to `topology.world_term_stats`

   ```
   topology.term_overrides (
     world_id    UUID NOT NULL REFERENCES topology.worlds(id),
     facet       TEXT NOT NULL,
     norm_term   TEXT NOT NULL,
     action      TEXT NOT NULL,
     target_term TEXT NULL,
     PRIMARY KEY (world_id, facet, norm_term)
   )
   ```

   `keep` rows are not stored — absence is `keep`.

2. `src/scripts/load_term_merges.py` takes a candidates file + an overrides file and **refuses to load** (exit non-zero, print offending rows) if:

   - a row's `world`/`facet` doesn't match the candidates file
   - `action` is not one of `keep` / `merge` / `child_of` / `drop`
   - `target` is empty for `merge` or `child_of`, or non-empty for `keep` or `drop`
   - a `term` or `target` is not in the candidates file
   - a `term` appears more than once
   - `term == target`
   - a `target` is itself the `term` of a `merge`, `child_of` or `drop` row — **except** that a `merge` target which is itself merged is resolved as a chain (A→B, B→C ⇒ A→C, B→C); a cycle is a refusal. `child_of` targets are never chained: pointing a child at something that isn't a standalone ranked term is a refusal, not something to resolve.

3. Expand and upsert, one transaction per run:

   - **`merge`** — for each final target group, every `norm_term` of the target **and** of each merged term (from the candidates file) gets a row with `action='merge'`, `target_term = <final target>`.
   - **`child_of`** — every `norm_term` of the child term gets `action='child_of'`, `target_term = <parent>`.
   - **`drop`** — every `norm_term` of the dropped term gets `action='drop'`, `target_term = NULL`.

   ```sql
   INSERT INTO topology.term_overrides (world_id, facet, norm_term, action, target_term)
   VALUES (...)
   ON CONFLICT (world_id, facet, norm_term)
   DO UPDATE SET action = EXCLUDED.action, target_term = EXCLUDED.target_term;
   ```

*Check:* one assert-based `demo()` / `__main__` self-check for the validation + chain-resolution function (out-of-scope row, bad action, missing/forbidden target, bad term, duplicate term, self-row, merge chain, cycle, `child_of` pointing at a merged term). Print rows inserted per action, and confirm every row in `term_overrides` is `discount-shopping` / `topic`.

### Phase 3 — apply inside `rollup()`

Two separate application points, because merges act on `post_terms` and the other two act on the computed stats.

**3a — merges, before aggregation** (v1's `_apply_term_merges`, now filtered on action):

```sql
UPDATE topology.post_terms pt
   SET canon_term = m.target_term
  FROM topology.term_overrides m
 WHERE pt.world_id = :world_id
   AND m.world_id  = pt.world_id
   AND m.facet     = pt.facet
   AND m.norm_term = pt.norm_term
   AND m.action    = 'merge'
   AND pt.canon_term IS DISTINCT FROM m.target_term;
```

Called immediately after `canonicalize()` and before `world_median` / `_aggregate`, exactly where it is today. `n_posts`, `n_accounts`, `variants` and lift all aggregate from `post_terms` after this point, so merged groups get combined support automatically.

**3b — `child_of` and `drop`, after stats are computed**, before the `world_term_stats` write:

Resolve each override's `norm_term`s to the `canon_term` they actually landed on (join `post_terms` on `world_id`/`facet`/`norm_term`), then:

- `child_of` → set `parent_term = target_term` on that stats row
- `drop` → remove that stats row from `stats` before the insert

Do **not** add child posts to the parent's counts.

Three edge cases the implementation must handle explicitly rather than silently:

- **Split resolution.** A term's `norm_term`s can land on more than one `canon_term` after re-clustering. Apply the action to the `canon_term` holding the most of them; print a warning naming the term and the split. `ponytail:` plurality rule, revisit if the warning ever fires.
- **Conflicting actions on one `canon_term`.** If a stats row's members carry different actions, take no action on it and print a warning. Silently dropping a term on a minority vote is worse than leaving it ranked.
- **Missing parent.** If a `child_of` target has no stats row this run (it fell below the floor), leave `parent_term` NULL so the child stays top-level, and warn. A child pointed at a nonexistent parent would otherwise disappear from the UI entirely.

No world/facet check in code: scope is enforced by what's in the table. With only `discount-shopping` / `topic` rows loaded, all of this is a no-op for everything else.

### Phase 4 — endpoint and UI

`GET /worlds/{slug}/overview` returns only terms where `parent_term IS NULL`, each with a `children` list holding the same fields, sorted by `n_posts` desc. Children obey the same read-time floors (`min_posts` / `min_accounts`) as parents in v1 — exempting them is deferred until it visibly loses something.

The Winning Topics row in `apps/ui/src/WorldsOverview.jsx` expands to show its sub-topics. A parent with no children renders exactly as it does today.

Note: annotations key on `slug:facet:canon_term`, so an annotation on a merged-away or dropped term is orphaned. 3 of 8 `discount-shopping` topic annotations were already orphaned by v1. Remapping them to their targets is a judgment call, not automatic.

### Phase 5 — rerun and verify

1. Before running, snapshot `world_term_stats` for **all** worlds and facets.
2. `make overview WORLD=discount-shopping`.
3. Report, for `discount-shopping` / `topic`:
   - top-level term count clearing the floor, before vs after, and the split by action (merged / nested / dropped / untouched)
   - the 20 largest merged groups with their `variants` — every merge should be defensible by reading them
   - every `child_of` with its parent and share — each should read as "part of" the parent
   - every `drop` with the co-occurring topics that justified it
   - median lift across ranked topics (should still be near zero; the `rollup()` assert enforces < 1.0)
   - drill-down for the #1 topic genuinely features it
   - any warning printed by the three edge cases in 3b
4. Confirm every other `(world, facet)` in `world_term_stats` is identical to the snapshot.
5. Run `make overview` a second time and confirm the `discount-shopping` / `topic` counts are identical (overrides survive a rebuild).

---

## Rollout if the test succeeds (later phase)

Success = Winning Topics for `discount-shopping` reads cleaner, every action is defensible, and nothing else moved. Then:

- Run Phases 0–2 for every world × facet (`product`, `format`, `topic`, and the clustered `presentation_facets.*.text` from phase 14).
- No code changes expected — the export, loader, rollup hook and endpoint are already generic over `(world, facet)`.

## Explicitly not doing (for now)

- Any world other than `discount-shopping` or any facet other than `topic`.
- Mapping terms below the floor. Add if overridden terms are visibly missing support.
- **Adding `primary_topic` / `secondary_topics` to the VLM schema.** This is the real fix *for new posts* — asking the model to name one hero topic and list the rest as secondary removes the characteristic-vs-topic ambiguity at the source instead of repairing it downstream. It needs a schema change in `data/<world>/crawl/vlm_schema.json`, a matching `extract_terms` change, and a re-describe of existing posts to be comparable, so it is its own phase. The override layer stays useful either way, for the corpus already described.
- Constraining the VLM to the merged vocabulary at extraction time.
- Auto-assigning new `norm_term`s that join an overridden cluster later; they keep the embedding cluster's label until the next export/override pass.
- Nesting deeper than one level — a child cannot itself have children.

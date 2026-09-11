# Phase 16 — LLM term merges on top of embedding clustering (test pass)

## Scope — test only

**This pass is a test, limited to one world and one facet:**

- world: `discount_shopping`  
- facet: `topic` (the **Winning Topics** panel)

No other world or facet is exported, merged, or changed. Everything below is built so the mechanism works for any `(world, facet)`, but the data loaded in this pass covers only `discount_shopping` / `topic`. Other worlds and facets must produce identical rankings before and after this phase.

**If the test works,** the same procedure rolls out to every world and every facet in a later phase. That rollout is data (more export groups, more merge rows), not new code.

Confirm the exact facet string in `src/services/terms.py` before starting (`topic` vs `topics`); use whatever the code writes to `post_terms.facet`.

## Problem

The free-form VLM coins a new phrasing per post. Normalization \+ embedding clustering (phase 7, cosine `>= 0.86`) catches spelling and near-duplicates but misses terms that mean the same thing in different words, so rankings fragment into too many clusters.

## Fix

Have an LLM decide which ranked `canon_term`s are the same thing, store that decision in its own table keyed on `norm_term`, and re-apply it on every `rollup()`. The embedding clustering stays; merges are a layer on top of it.

Terms in scope: those clearing **`n_posts >= 6` and `n_accounts >= 4`**. The long tail below the floor is not mapped.

## Rules — read before writing code

- **Do not UPDATE `post_terms.canon_term` outside `rollup()`.** `post_terms` is truncated and rebuilt from `vlm_json` on every `make overview`; a one-off edit silently vanishes on the next run. The merge table is an *input* to the rebuild, applied inside it.  
- **Key merges on `norm_term`, never on `canon_term`.** A cluster's label is its highest-post-count member and can change as posts arrive. `norm_term` is a pure function of the string and never drifts.  
- **Merges never cross facets or worlds.**  
- **The LLM never invents terms.** Every `to_term` is an existing input term, so the displayed label is always something the VLM actually said.  
- Before starting, read `src/services/overview.py` (`canonicalize`, `rollup`), `src/services/terms.py`, and the existing migration pattern. Confirm the table and column names below match the code; if they differ, use the code's names.

---

## Phases

Each ends at something runnable. Stop after each phase and report.

### Phase 0 — export

Script `src/scripts/export_merge_candidates.py` (or a `make` target) taking `--world` and `--facet` (this pass: `discount_shopping`, `topic`), writing `data/term_merges/discount_shopping__topic.candidates.json` with terms sorted by `n_posts` desc:

SELECT w.slug AS world, s.facet, s.canon\_term, s.n\_posts, s.n\_accounts,

       (SELECT array\_agg(DISTINCT pt.norm\_term ORDER BY pt.norm\_term)

          FROM topology.post\_terms pt

         WHERE pt.world\_id \= s.world\_id AND pt.facet \= s.facet

           AND pt.canon\_term \= s.canon\_term) AS norm\_terms,

       s.variants

FROM topology.world\_term\_stats s

JOIN topology.worlds w ON w.id \= s.world\_id

WHERE w.slug \= :world AND s.facet \= :facet

  AND s.n\_posts \>= 6 AND s.n\_accounts \>= 4

ORDER BY s.n\_posts DESC;

`norm_terms` are stored in the file so Phase 2 loads from the file, not from live `post_terms` — a `make overview` between export and load cannot shift the mapping.

*Check:* print the term count for `discount_shopping` / `topic`.

### Phase 1 — LLM merge (manual, done by Sami)

Not for Claude Code to run. One LLM call over the candidates file, using the prompt below. Result saved as `data/term_merges/discount_shopping__topic.merges.csv` with header `world,facet,from_term,to_term`.

Prompt:

> You are consolidating labels from a product-discovery pipeline. Below is a list of terms for world `discount_shopping`, facet `topic`. Each term has its post count, account count, and the normalized phrasings (`norm_terms`) and raw phrasings (`variants`) grouped under it.  
>   
> Identify terms that refer to **the same topic** and should be one ranked item.  
>   
> Rules:  
> 

> - `to_term` must be one of the input terms exactly as written. Prefer the one with the most posts. Never invent a new term.  
> - Do not merge a more specific term into a more general one (e.g. `curb alert furniture` stays separate from `curb alert`) — specificity is the signal.  
> - When unsure, do not merge.  
> - Output only merged rows as CSV with header `world,facet,from_term,to_term`. No commentary.

### Phase 2 — validate and load

1. Migration adding:  
     
   CREATE TABLE topology.term\_merges (  
     
     world\_id   UUID NOT NULL REFERENCES topology.worlds(id),  
     
     facet      TEXT NOT NULL,  
     
     norm\_term  TEXT NOT NULL,  
     
     canon\_term TEXT NOT NULL,   \-- the to\_term  
     
     PRIMARY KEY (world\_id, facet, norm\_term)  
     
   );  
     
2. Script `src/scripts/load_term_merges.py` takes a candidates file \+ merges file and **refuses to load** (exit non-zero, print offending rows) if:  
     
   - a row's `world`/`facet` doesn't match the candidates file (this pass: anything other than `discount_shopping` / `topic`)  
   - a `from_term` or `to_term` is not in the candidates file  
   - a `from_term` appears more than once  
   - `from_term == to_term`

   

   Then it resolves chains: if a `to_term` is also a `from_term`, follow to the final target (A→B, B→C ⇒ A→C, B→C). A cycle is a refusal.

   

3. Expand and upsert. For each final `to_term` group, every `norm_term` of the `to_term` **and** of each `from_term` (from the candidates file) maps to `to_term`:  
     
   INSERT INTO topology.term\_merges (world\_id, facet, norm\_term, canon\_term)  
     
   VALUES (...)  
     
   ON CONFLICT (world\_id, facet, norm\_term)  
     
   DO UPDATE SET canon\_term \= EXCLUDED.canon\_term;  
     
   One transaction per run.

*Check:* one assert-based `demo()` / `__main__` self-check for the validation \+ chain-resolution function (out-of-scope row, bad term, duplicate from, chain, cycle). Print rows inserted, and confirm every row in `term_merges` is `discount_shopping` / `topic`.

### Phase 3 — apply inside `rollup()`

In `rollup(db, world_slug)`, immediately **after** the cluster step writes `canon_term` and **before** the `GROUP BY` / stats step:

UPDATE topology.post\_terms pt

SET canon\_term \= m.canon\_term

FROM topology.term\_merges m

WHERE pt.world\_id \= :world\_id

  AND m.world\_id  \= pt.world\_id

  AND m.facet     \= pt.facet

  AND m.norm\_term \= pt.norm\_term;

No world/facet check in code: scope is enforced by what's in the table. With only `discount_shopping` / `topic` rows loaded, this is a no-op for everything else.

Confirm `world_term_stats.variants`, `n_posts`, `n_accounts`, `effective_n_accounts` (phase 12\) and lift all aggregate from `post_terms` after this point, so merged groups get combined support automatically. If any of them is computed from the pre-merge clusters, move it after the update.

### Phase 4 — rerun and verify

1. Before running, snapshot `world_term_stats` for **all** worlds and facets.  
2. `make overview`.  
3. Report, for `discount_shopping` / `topic`:  
   - term count clearing the floor, before vs after  
   - the 20 largest merged groups with their `variants` — every merge should be defensible by reading them  
   - median lift across ranked topics (should still be near zero)  
   - drill-down for the \#1 topic genuinely features it  
4. Confirm every other `(world, facet)` in `world_term_stats` is identical to the snapshot.  
5. Run `make overview` a second time and confirm the `discount_shopping` / `topic` counts are identical (merges survive a rebuild).

---

## Rollout if the test succeeds (later phase)

Success \= Winning Topics for `discount_shopping` reads cleaner, merges are defensible, and nothing else moved. Then:

- Run Phases 0–2 for every world × facet (`product`, `format`, `topic`, and the clustered `presentation_facets.*.text` from phase 14).  
- No code changes expected — the export, loader and rollup hook are already generic over `(world, facet)`.

## Explicitly not doing (for now)

- Any world other than `discount_shopping` or any facet other than `topic`.  
- Mapping terms below the floor. Add if merged terms are visibly missing support.  
- Constraining the VLM to the merged vocabulary at extraction time — next phase, once the merged vocabulary is trusted.  
- Auto-merging new `norm_term`s that join a merged cluster later; they keep the embedding cluster's label until the next export/merge pass.


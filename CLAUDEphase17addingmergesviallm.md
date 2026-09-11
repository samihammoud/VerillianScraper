Context: CLAUDEphase16termmerges.md (LLM term merges, test scope: world
discount_shopping, facet topic only). Winning Topics still has too many
clusters, for three reasons:

1. Synonyms not merged (e.g. "flea market haggling" / "price negotiation").
2. Characteristics ranked as topics (e.g. "paint stripping" is probably part of
   "furniture makeover"; "yard sale video games" is probably part of "yard sale").
3. Vague umbrella topics (e.g. "free discarded item" is mostly curb finds or
   dumpster finds and tells us nothing on its own).

STEP 1 (investigate only, change nothing):
Run a co-occurrence query over post_terms for discount_shopping / topic. For
each of "paint stripping", "yard sale video games" and "free discarded item",
list the other canon_terms on the same posts: shared_posts and share (shared
posts divided by the term's total posts), sorted by shared_posts desc, top 10.
Use DISTINCT (post_id, canon_term). Confirm the exact facet string in terms.py
first. Report the tables and stop.

STEP 2 (after I approve): extend phase 16. Build phase 16 with these changes
if it isn't built yet; extend it if it is.

- Export: for each candidate term, also include its top 5 co-occurring topic
  canon_terms with share.
- LLM output format becomes: world,facet,term,action,target
  where action is keep | merge | child_of | drop
  merge -> target is the synonym term to merge into (existing behavior)
  child_of -> term is a characteristic of target; shown as a sub-topic
  drop -> vague umbrella term; removed from rankings
- Rename term_merges to term_overrides and add an `action` column. Loader
  validation: target is required for merge/child_of and must exist in the
  candidates file; a target cannot itself be merged, dropped or child_of
  (resolve merge chains; refuse otherwise); no cycles.
- rollup(): merge works as today (norm_term -> canon_term update after
  clustering). After stats are computed, set world_term_stats.parent_term for
  child_of rows (new nullable column), and delete stats rows for drop. Don't
  add child posts to the parent's counts. Posts with a dropped term still
  count under their other terms.
- Endpoint and UI: /overview returns only terms where parent_term IS NULL,
  each with a `children` list (same fields). The Winning Topics row expands to
  show its sub-topics.
- Update the Phase 1 prompt in the doc with these definitions:
  hero topic = could be the video's title
  characteristic = present in the video but you wouldn't title it that way
  use child_of when one co-occurring topic has share >= 0.5
  use drop when the term's posts are split across 2+ more specific topics
  and the term adds no information of its own
  keep when no co-occurring topic has share above ~0.3
  This replaces the earlier "don't merge specific into general" rule:
  specific terms stay visible as sub-topics.
- Keep the test scope (discount_shopping / topic only) and Phase 4's check
  that every other world and facet is unchanged.
- Update CLAUDEphase16termmerges.md to match, then stop and report before
  implementing.

Not in this pass: adding primary_topic / secondary_topics to the VLM schema.
Note it in the doc's "Explicitly not doing" section as the fix for new posts.

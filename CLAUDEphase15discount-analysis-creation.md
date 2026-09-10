Phase 15 — Discount World: hero-by-cost product & format ranking (v1)
Goal
Actionable v1 answer to two questions: which products win when they're actually part of a discount/free story, and what formats those videos use. Reuses phase 7's rollup machinery unchanged, just scoped to a filtered instance/post set instead of the whole corpus. No new ranking algorithm, no schema change — the crawl already has what this needs.
Why not prominence
prominence (hero/incidental) answers "was the camera on this thing," not "was this a discount story." A full-price hero product tells you nothing about discounting; an incidental background item can still have its cost called out on the audio track. products[].value_signal.acquisition_cost is the field that actually answers what this needs, and it's already scoped per product instance rather than per post.

Confirmed present in the deployed schema (data/discount-shopping/crawl/vlm_schema.json): every products[] entry requires value_signal, and acquisition_cost is one of free | deep_discount | discount | full_price | not_stated.

Decision: skip prominence entirely for this pass. Revisit only if step 2/3 results come back noisy from one-off or background mentions — don't add it preemptively.
Step 1 — hero-candidate product instances
Per post, per products[] entry: keep it iff acquisition_cost is one of {free, deep_discount, discount}. Drop full_price (not a discount story) and not_stated (cost never addressed — no signal either way).

This is per-instance, not per-post. One post can contribute zero, one, or several qualifying instances depending on how many of its shown items were actually discussed as a deal.
Step 2 — discounted product ranking
Same pipeline as phase 7's rollup(): extract_terms → normalize → embed/cluster → lift vs. world median → support floor (n_posts >= 5, n_accounts >= 3, effective_n_accounts >= 4 per phase 12) — but the input is the filtered instance set from step 1, not every product instance in the world.

Output: ranked "discounted products" list. This directly answers "get discounted products."
Step 3 — format ranking on those posts
Take the set of posts (not instances) that contributed at least one step-1 instance. Cluster format (and format_traits) on exactly that post set, same clustering mechanism phase 7 already uses for the world-wide format ranking.

Output: ranked "formats showcasing discounted products" list. This directly answers "get the most popular formats showcasing those discounted products."

Scoping note, deliberate not accidental: format is post-level, acquisition_cost is product-level. A post with one discounted item and two full-price items still counts once toward step 3 — the question is "how are videos containing a discount story typically built," not "how is this one item specifically shot."
Modules touched
No new schema, no new crawl work. This is a rollup addition next to phase 7's existing overview.py / terms.py.

src/services/terms.py extract_terms: unchanged signature. Called twice —

                              once over the step-1-filtered product instances,

                              once over format on the step-1 post set.

src/services/overview.py rollup(): add a discount_hero variant that takes

                              a pre-filtered instance/post set instead of

                              querying the full world. Same clustering, lift,

                              and floor code path as the existing rollup.

src/routes/worlds.py + one endpoint, or a `filter=discount_hero` param

                              on the existing overview endpoint — pick

                              whichever fits the current route shape.

Check
Run once against the current discount_shopping corpus:

Step 1 instance count vs. total product instance count in the world — not near-zero (would mean the field isn't populating) and not near-100% (would mean the filter isn't excluding anything).
Top 10-15 rows of the step 2 product ranking, read by hand — do they look like real recurring discount items, not one-off noise.
Top 10-15 rows of the step 3 format ranking, same manual check.
Coverage block: % of the corpus with vlm_json at all, same requirement phase 7 already has.
Deferred — hypothesis tests, not part of this build
Not run now. Come back to these once the v1 rankings above are read and trusted:

Free vs. discount world-level test — split the world's overall view-ratio distribution by acquisition_cost bucket and compare medians. This is the actual test of whether "free" outperforms "discount" as a trigger. The per-product value-cut (if added later) is descriptive color, not this test, and shouldn't be read as answering it.
presentation_facets.choice vs. clustered .text comparison — run the enum ranking and the free-text cluster ranking side by side per facet; disagreement flags where the starter enum missed a real category.
Product × presentation cross-tabs — whether specific formats only work with specific product/trigger types. Needs the single-facet rankings trusted first.
Account-specific format signature — per-account concentration on presentation_facets combinations, reusing phase 12's Herfindahl math for discovery instead of filtering.
Reconsider prominence as a tightening filter — only if step 1's acquisition_cost-only filter turns out too noisy in practice.

Phase 14 remains the fuller deferred-analytics backlog; this list is just which of those are relevant once this v1 build lands.

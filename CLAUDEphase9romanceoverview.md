# Phase 9 — Romance World Overview: embedding & clustering strategy

## What's different from phase 7

Phase 7 was built for pets, where the money field is `products[].name` and the
argument was **term aggregation, not point clustering** — clusters have no names,
and the facets must be able to disagree.

Both halves of that argument still hold for the short free-text facets
(`format`, `format_traits`, `setting`, `topics`, `characters.dynamic`). Reuse the
phase 7 pipeline for those unchanged: normalize -> embed -> agglomerative at 0.86 ->
canonical label by post count.

But romance has three things pets did not, and each needs a different treatment:

1. **`products` is empty.** The winning-products panel is dead weight here. Drop it.
2. **`premise` is a sentence, not a term.** It is a one-line description of a
   *situation*, written to be human-readable. Clustering premises does not
   produce unnamed regions — the medoid sentence **is** the label, and it is
   already in the shape of a script brief. The phase 7 objection inverts here,
   because `premise` is a single-facet projection, not the whole-post blob.
3. **Five closed enums describe the emotional shape of the piece**
   (`stage`, `conflict`, `register`, `resolution`, `perspective`), plus
   `pacing.*`, `characters.pairing`, `synthetic.*`. These are low-cardinality.
   Never embed them. Cross-tabulate them.

So the strategy is three layers, not one ranking.

---

## Layer 1 — Situation space (the primary embedding)

**Embed:** `premise` alone. If `premise` is null, fall back to
`summary + " " + topics.join(" ")`. Do **not** concatenate the transcript in —
verbatim dialogue drags the vector toward wording and away from situation, and
two videos about the same premise with different scripts must land together.

**Cluster:** HDBSCAN or agglomerative over the premise vectors, per world, at a
threshold tuned to produce roughly 60–150 clusters over ~10k posts. Coarser than
that and "he texts back late" merges with "he's on his phone at dinner"; finer
and you get one cluster per post.

**Label:** the medoid premise sentence, verbatim. Show the 5 nearest premises as
`variants` on hover — same tooltip discipline as phase 7, same reason.

**Rank:** phase 7's statistic, unchanged —
`lift = median(log1p(views) | cluster) - median(log1p(views) | world)`,
`view_ratio = exp(lift)`. Support floor raised: `n_posts >= 8`, `n_accounts >= 5`.
The account floor matters more here than it did in pets, because AI-generated
romance content is mass-produced by a small number of farms; without it the top
of the list will be one account's slop repeated.

**Dedupe first.** Before counting, collapse near-identical posts on transcript
embedding at cosine >= 0.97 and count a duplicate group as one post from one
account. Reposted and re-voiced clips are the single largest source of fake
support in this corpus.

This layer alone answers "what situations do people latch onto." It is the panel
worth building first.

---

## Layer 2 — Enum cross-tabs (no embedding at all)

Count directly, and rank *cells*, not columns. The populated cells of
`conflict x register x resolution` — maybe 40 of them have real support — are the
most directive output in the whole system, because each cell reads as an
instruction:

> `attention_neglect x funny x punchline` -> 2.4x
> `attention_neglect x venting x unresolved` -> 0.9x

Same situation, opposite outcomes, and the second is where most of the volume is.
That gap is the opportunity: a niche the corpus is serving in the wrong register.

Compute, each with lift + support:

- `conflict` alone, and `conflict x register`
- `register x resolution` (does landing a punchline beat reassurance?)
- `perspective` (woman / man / both) — you post `both`; check that it isn't
  structurally penalized before reading anything else
- `stage` — `long_term` vs `dating` vs `situationship` changes who Claire is
- `characters.pairing` — `romantic_couple` vs `one_person_two_roles`. You are the
  former. Most of the corpus is the latter. Their lifts are not interchangeable.

**Also surface a single derived flag:** `punchline IS NOT NULL`. That is a binary
"does this land on a turn" and its world-level lift tells you whether Kevin needs
a closing line every time or whether cute-and-unresolved travels too.

---

## Layer 3 — Craft spaces (secondary embeddings, cross-examined)

Two more embedding spaces, each independently clusterable, each answering a
question the situation ranking cannot.

**Hook space.** Embed `hook` verbatim. The first three seconds is a separable
unit — hook phrasings transfer across situations, so cluster them on their own
and rank by lift. Output is a reusable library of opening moves
("did you even—", "babe. babe. look at me.", flat statement of grievance),
not a topic list. This is the highest-leverage small artifact for a 10-second
format, because in 10 seconds the hook is a third of the runtime.

**Punchline space.** Embed `punchline`. Cluster and hand-label the resulting
groups by *rhetorical move* rather than content: reframe, deflate, deadpan
agreement, turn-it-back, concede-and-tease. That set is Kevin's move list.
Rank by lift within the situations you're targeting, not globally.

**Do not build a separate "format space."** `format` and `format_traits` are 2–4
word phrases — phase 7's term pipeline handles them, and their cardinality is
low. Cluster them there, not here.

### The cross-examination that matters

For each Layer 1 premise cluster, compute a **profile**: the distribution of each
enum over its posts, plus median duration, median `len(dialogue)`, and the
`view_ratio` of each register *within* that cluster.

Then read two things off it:

1. **Fit.** Which clusters' winning profile already looks like Boredinary Life —
   `pairing=romantic_couple`, `register in {cute_affectionate, funny}`,
   `resolution in {punchline, reassurance}`, `perspective=both`, duration under 15s,
   4–8 dialogue turns? Those are shoot-tomorrow topics.
2. **Mismatch.** Clusters where the *modal* register in the corpus is
   `venting` or `advice` but the *highest-lift* register inside the cluster is
   `funny` or `cute_affectionate`. That is a proven situation being executed in a
   register you happen to be better at. This is the best output the system can
   produce and it is invisible to any single ranking.

---

## Stratify, don't just filter

Three variables change the meaning of every number and should be selectable
strata rather than global filters:

- **`synthetic.presenter`.** You are `ai_generated_person`. Compute lift *within*
  that stratum as well as globally. If AI-presented romance is discounted by the
  algorithm or the audience, the global ranking is measuring a format you can't
  run, and the within-stratum ranking is the one that applies to you.
- **`estimated_duration_sec`**, bucketed `<8 / 8–15 / 15–30 / 30+`. Restrict the
  primary view to `<15`. A situation that needs 40 seconds to pay off is not a
  topic you can use, however well it performs.
- **Recency.** Phase 7 deliberately excluded time series; here it is the point.
  Split on `posted_at` at 30 (or 60) days and plot each premise cluster on
  volume x lift:

  | | low volume | high volume |
  |---|---|---|
  | **high lift** | *emerging* — the queue | *proven* — safe, competitive |
  | **low lift** | dead | *saturated* — avoid |

  "Emerging" is the only quadrant that answers the actual question.

---

## Second metric: resonance, not reach

`log1p(views)` measures what the algorithm distributed. For "topics people latch
onto," the closer signal is engagement rate. If comment counts are available on
`posts`, compute a parallel statistic:

```
r = log1p(comments) - log1p(views)      -- log comment rate
lift_r = median(r | cluster) - median(r | world)
```

Rank by both and **look specifically where they disagree.** A premise cluster
with low view lift and high comment-rate lift hit a nerve without winning
distribution — for a small niche account that is a better target than a
high-reach cluster already saturated by large accounts. Show both columns in the
same table; the disagreement is the signal, not noise to reconcile.

---

## Cheap derived features worth extracting at the same time

All computed in `extract_terms`, no extra API calls, all directly actionable for
a fixed two-hander format:

| feature | why |
|---|---|
| `n_turns = len(dialogue)` | tells you the winning turn count for a 10s piece |
| words per turn, and Kevin-side / Claire-side word ratio | who should carry the words |
| `pacing.speaker_dominance` | same question, from the VLM's own judgement |
| `hook_from_dialogue` vs hook from `on_screen_text` | do you need a burned-in opener |
| `len(on_screen_text)` | text-heavy vs clean frame |
| expression set from `pacing.expression_beats` | `deadpan`, `smirk`, `knowing_look` are literally Kevin — test their lift |
| `cta` | almost certainly `none` or `tag_someone` wins; confirm rather than assume |

`characters.dynamic` deserves special attention: it is free text but short, so it
goes through the phase 7 term pipeline, and it is the one facet that describes
the account's premise directly. `anxious partner calm partner` and `one lectures
one deflects` are the Claire/Kevin dynamic. Their lift is a direct read on
whether the whole concept is well-formed.

---

## Build order

1. Premise clustering + lift + drill-down. Read the top 40 by hand. This is the
   moment you find out whether the corpus is any good — if the premise clusters
   are mush, nothing downstream helps.
2. Enum cross-tab panel (`conflict x register x resolution` cells, ranked).
3. Recency split on the premise clusters -> the four-quadrant view.
4. Hook library + punchline moves.
5. Per-cluster profile and the fit/mismatch report.

Everything through step 3 reuses phase 7's `post_terms` / `world_term_stats`
shape with `facet='premise_cluster'` and a `canon_term` that happens to be a
sentence. No new schema is needed until step 5, which wants a
`cluster_profiles` table.

## Verification

Phase 7's checks carry over — median lift across ranked terms near zero, hand-check
the top 10 drill-downs. Add one: **read 20 premises from a single cluster and
confirm they are the same situation.** Premise clustering is the one step where a
bad threshold produces a list that still looks completely plausible.

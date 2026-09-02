# Phase 8 — Romance World Crawl

## Scope

**Build the corpus. Nothing else.** A ninth world, `romance`, and a six-round
crawl that fills it. Ranking and analytics are a later phase.

The corpus feeds a two-character relationship dialogue account (~10s skits), which
matters here only because it shapes what goes in — not what gets built.

```
data/romance/world.txt         ──▶ topology.worlds row
data/romance/seed_queries.txt  ──▶ crawl_queries round 0
data/romance/query_prompt.txt  ──▶ the generator's instruction
        ──▶ make crawl WORLD=romance ROUNDS=6
        ──▶ a corpus of ~10-15k described posts across ~250-400 accounts
```

No new tables. No new pipeline stages. Three data files, one loader, one
retargeted prompt, three constants, and one data-integrity fix.

**Done means:** the corpus SQL in *Corpus checks* returns healthy numbers.
Not a ranked list — there is no ranking in this phase.

---

## Already done — do not re-implement

Verified against the working tree:

| thing | state |
|---|---|
| `query_gen._evidence(db, world_slug)` | already world-scoped and cumulative — filters `Post.discovered_by_world == world_slug`, no round filter, so a term that stopped appearing stays visible |
| `N_EXPLOIT = 2`, `N_EXPLORE = 3` | already the intended split (but see the schedule below) |
| `crawl_queries.world_slug` scoping | already applied in `run_round`, `_build_prompt`, the dedupe set, and `seed_crawl`'s refusal check |
| `posts.discovered_by_world` | already stamped by the crawl path via `ingest_account(..., discovered_by_world=query.world_slug)` — **but not by the manual `make ingest` path; see Change 4** |
| resume semantics | already correct; kill mid-round and rerun, it continues |

---

## The world definition

Authored in `data/romance/world.txt`, reproduced here so review does not require
opening the file. `seed_worlds` embeds `description + "\n" + "\n".join(snippets)`
into `worlds.reference_embedding`; routing assigns each post by argmax cosine
against all nine worlds.

**slug** `romance`  ·  **name** `Romance & Relationships`

**description**

> Romantic relationships between partners and the discourse around them: dating
> and courtship, situationships and defining the relationship, long distance,
> moving in together, jealousy and trust, money and chores, in-laws and friends,
> communication habits, breakups and getting back together. Covers both scripted
> couple content — skits, POV dialogue, two-person reenactments of a relationship
> moment — and commentary about modern dating: advice, hot takes, therapist
> explainers, podcast clips. Distinct from Parenting & Baby: content about how two
> partners relate to each other is Romance, content centered on raising a child is
> Parenting, even when both partners appear. Distinct from Health & Wellness:
> attachment styles and emotional patterns framed as relationship dynamics are
> Romance, while therapy, mental health treatment and self-care framed around one
> person's own wellbeing are Health. This world is about people and their dynamics
> rather than physical goods — a Romance post typically shows no product at all.

**example_snippets**

```
pov your girlfriend asks if you would still love her
he takes four hours to reply and says he was just busy
green flags i ignored because i was scared of being single
relationship therapist explains why he pulls away
we argue about the dishes more than anything serious
```

### Why it is written this way

**The "Distinct from" clauses are load-bearing, not documentation.** They are part
of the embedded text, and they are what pushes the vector away from
`parenting-baby` and `health-wellness` — the two worlds most likely to breach the
0.8 similarity check in Phase 0. Sharpening them is the first lever if that check
fails.

**The five snippets deliberately span the territory** rather than clustering on
one register: POV skit, everyday friction, reflective discourse, therapist
explainer, long-term domestic. A narrow snippet set produces a narrow reference
vector, and posts on the edges of the world get argmax'd elsewhere.

**The snippets are written in caption register, matching the existing eight
worlds — do not "improve" this.** What routing actually embeds per post is
`flatten_for_blob(vlm_json)`, which renders as:

```
<one-sentence summary>
Setting: <setting>.
Topics: <topic, topic, topic>.
Products: <...>.        <- absent for this world
```

That is summary register, not caption register, so every world's reference vector
sits at the same slight remove from the blobs it is compared against. Rewriting
romance's snippets into summary register alone would raise its cosine against
*every* post — pet posts included — and bias argmax toward over-capturing. The
register gap is uniform and therefore harmless; making one world's gap smaller is
what breaks it.

**The final sentence is the product signal.** Eight of the nine worlds are
organized around physical goods and their blobs carry a `Products:` line. Stating
that Romance posts typically show no product helps separate it from all eight at
once, not just the two named neighbours.

---

## Change 1 — `seed_worlds` must stop being destructive

`seed_worlds()` blind-`db.add()`s every entry in `WORLDS` with no conflict
handling, and `worlds.slug` is unique. Appending a ninth world and re-running
raises on the existing eight. The only escape hatch in the Makefile is
`make reseed-worlds`, which runs `TRUNCATE topology.worlds CASCADE` — that
**wipes every world's `world_posts`, churns all world UUIDs, and invalidates the
entire pets corpus.**

Make it additive:

```python
def seed_worlds(db) -> None:
    existing = set(db.execute(select(World.slug)).scalars().all())
    for w in WORLDS:
        if w["slug"] in existing:
            continue                      # adding a world never touches the others
        embed_input = w["description"] + "\n" + "\n".join(w["example_snippets"])
        db.add(World(slug=w["slug"], name=w["name"], description=w["description"],
                     example_snippets=w["example_snippets"], reference_embedding=embed(embed_input)))
    db.commit()
```

Add a `seed-world` Makefile target that runs `seed_worlds` **without** the
truncate. Leave `reseed-worlds` as the deliberate nuclear option.

**Check:** `topology.worlds` has 9 rows; the 8 original UUIDs are byte-identical
before and after (`SELECT slug, id FROM topology.worlds ORDER BY slug`); the
`world_posts` row count is unchanged.

---

## Change 2 — data files and the loader

Three files already written under `apps/backend/data/`:

```
data/romance/world.txt          3 blank-line blocks: name, description, snippets
data/romance/seed_queries.txt   one query per line; blank + # lines ignored
data/romance/query_prompt.txt   generator instruction, {placeholder} slots
data/_default/query_prompt.txt  fallback for the 8 existing product worlds
```

New module `src/services/crawl_config.py`:

```python
"""Loads a world's hand-authored crawl inputs from data/<slug>/.

Text files rather than Python literals because these three are what gets iterated
on between runs — a bad seed query set is fixed by reading round 0's yield table
and editing a line, not by editing a module.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"   # -> apps/backend/data


def load_seed_queries(slug: str) -> list[str]:
    lines = (l.strip() for l in (DATA_DIR / slug / "seed_queries.txt").read_text().splitlines())
    return [l for l in lines if l and not l.startswith("#")]


def load_query_prompt(slug: str) -> str:
    """Falls back to data/_default/ so the eight product worlds keep the original
    prompt without needing a file each."""
    path = DATA_DIR / slug / "query_prompt.txt"
    if not path.exists():
        path = DATA_DIR / "_default" / "query_prompt.txt"
    return "\n".join(l for l in path.read_text().splitlines() if not l.startswith("#")).strip()


def load_world(slug: str) -> dict:
    """Blocks separated by blank lines: name, description, snippets."""
    blocks = [b.strip() for b in (DATA_DIR / slug / "world.txt").read_text().split("\n\n") if b.strip()]
    if len(blocks) != 3:
        raise ValueError(f"{slug}/world.txt: expected 3 blank-line blocks, got {len(blocks)}")
    name, description, snippets = blocks
    return {
        "slug": slug,
        "name": name,
        "description": " ".join(description.split()),   # unwrap the hand-wrapped paragraph
        "example_snippets": snippets.splitlines(),
    }
```

Wire into two callers:

- `seed_worlds.py` — append `load_world("romance")` to `WORLDS`. Leave the eight
  hardcoded dicts alone; they work, and rewriting them into files is churn.
- `seed_crawl.py` — replace the `SEED_QUERIES` dict with
  `load_seed_queries(world_slug)`. Move pets' ten lines to
  `data/pets/seed_queries.txt` for symmetry. Keep the "refuse if this world
  already has queries" guard exactly as it is.

`data/` must **not** go in `.gitignore` — unlike `out/`, these are inputs.

**Check:** `python -c "from src.services.crawl_config import *; print(load_world('romance')); print(len(load_seed_queries('romance')))"`
prints a 4-key dict with a single-line description, 5 snippets, and `10`.

---

## Change 3 — retarget the generator, load its prompt from a file

`INSTRUCTION` is a module-level f-string hardcoded for product worlds. It becomes
a per-world file. In `query_gen.py`, delete the constant and build it per call:

```python
from src.services.crawl_config import load_query_prompt

def generate(db: Session, world_slug: str, round_no: int) -> list[CrawlQuery]:
    instruction = load_query_prompt(world_slug).format(
        QUERIES_PER_ROUND=QUERIES_PER_ROUND, N_EXPLOIT=N_EXPLOIT, N_EXPLORE=N_EXPLORE
    )
    ...
    config=types.GenerateContentConfig(..., system_instruction=instruction, ...)
```

`.format()` on file text means a stray `{` in a hand-edited prompt raises rather
than silently mangling. The self-check below catches it before a run.

**Why the text changed.** The old prompt opens with *"find as many distinct
creator accounts as possible"* — right for a product world, where a product needs
`n_accounts >= 3` and recurs across creators. Wrong here. But naive "topic
variety" is also wrong: see *Crawl strategy*. The replacement asks for a corpus
that can eventually be ranked — enough topics to compare, enough posts per topic
for a stable median. Read `data/romance/query_prompt.txt`.

**Check:** after round 1, `SELECT round_no, query_text, intent, rationale FROM
crawl_queries WHERE world_slug='romance' ORDER BY round_no, id;` — round-1
rationales should cite observed topic terms; explore queries should name
situations absent from both the ledger and the observed-topics line.

---

## Change 4 — `make ingest` does not stamp `discovered_by_world`

**This is the one that silently breaks the loop, and it is new.**

`ingest_account` accepts `discovered_by_world`, and `crawl.py` passes it. But
`run_ingest.py` never does — it reads only `handles` and `count` from argv, so
every post ingested by `make ingest` lands with `discovered_by_world = NULL`.

That matters because the hand-seeded peer accounts (below) go in through exactly
that path. Their posts would be invisible to `_evidence()`, so the query
generator would never see the terms from the most format-relevant accounts in the
corpus — and invisible to every world-scoped corpus query in *Corpus checks*.

Two-line fix in `run_ingest.py`:

```python
world = sys.argv[3] if len(sys.argv) > 3 else None
... ingest_account(db, handle, count, discovered_by_world=world)
```

and in the Makefile: `$(PY) -m src.scripts.run_ingest "$(HANDLES)" $(COUNT) $(WORLD)`.

If the seeding is already done before this is fixed, the repair is one statement:

```sql
UPDATE posts SET discovered_by_world = 'romance'
WHERE discovered_by_world IS NULL
  AND account_id IN (SELECT id FROM accounts WHERE handle IN ('h1','h2', ...));
```

**Check:** `SELECT discovered_by_world, count(*) FROM posts GROUP BY 1;` — no
unexpected NULL bucket after seeding.

---

## Change 5 — volume constants

In `crawl.py`:

```python
ACCOUNTS_PER_QUERY = 20   # was 5 (a labeled testing cap)
POSTS_PER_ACCOUNT = 40    # unchanged — below ~30, peaks.py drops to its top-10% fallback
QUERIES_PER_ROUND = 5     # unchanged
```

6 x 5 x 20 x 40 = **24,000 posts ceiling**; realistically 10-15k after handle
overlap, across ~250-400 distinct accounts. That is an order of magnitude past the
pets run, and three things break at that scale:

1. **Files API quota is 20GB.** At ~2MB per short video, 15k videos is ~30GB
   passing through. `describe_posts` must upload, collect and delete in chunks —
   confirm the code actually deletes uploaded objects on collect.
2. **`describe_posts()` takes no world argument.** It claims every post with
   `visual_description IS NULL AND visual_attempts < 3` globally, so any pets
   backlog gets described on romance's clock. Check first:
   `SELECT count(*) FROM posts WHERE visual_description IS NULL AND visual_attempts < 3;`
3. **Wall clock.** With the Batch API's ~24h SLA, six rounds is a six-day
   foreground process. Run round 0 synchronously to validate the schema against
   relationship content before switching to batch.

**Lever:** `POSTS_PER_ACCOUNT = 20` halves the VLM bill and costs nothing for
topic breadth. The only casualty is `peaks.py`, which needs ~30 to avoid its
top-10% fallback. Keep 40 only if per-account peak analysis is planned for this
world later.

---

## Crawl strategy

Two decisions that shape the corpus and therefore belong in this phase. Everything
about *reading* the corpus is deferred.

### Breadth and depth are both required

The eventual support floor (`n_posts >= 5`, `n_accounts >= 3`) is a minimum, not a
target — a median over 5 log-view values cannot be ranked on. A corpus of 500
topics at 5 posts each is the worst available outcome: everything clears the
floor, nothing is trustworthy. Aim for roughly **40-60 topics at 25+ posts each**.

Explore builds breadth, exploit builds depth, so run the split on a schedule:

```
rounds 0-2:  N_EXPLORE = 3, N_EXPLOIT = 2    # map the territory
rounds 3-5:  N_EXPLORE = 2, N_EXPLOIT = 3    # deepen what surfaced
```

Hand-edit two constants between runs. Not round-conditional code — the rounds are
run manually across several days anyway.

### Hand-seed the format peer cohort first

Search will mostly return advice, discourse and podcast-clip accounts. The account
this feeds is a two-character skit, and a topic that carries a 3-minute monologue
may not survive compression into 10 seconds. If the corpus contains no
skit-format posts, that question can never be asked — so guarantee the cohort
rather than hoping search finds it.

Hand-pick 15-20 accounts already doing two-character relationship dialogue and
ingest them **after Change 4, before the crawl**:

```bash
make ingest HANDLES=h1,h2,h3,... COUNT=40 WORLD=romance
```

Ten minutes of manual work, existing command. Then let the crawl do what it is
actually good at: breadth nobody would have thought to search for.

---

## Run order

```bash
# one time
python -m src.scripts.seed_worlds            # additive after Change 1; adds romance only
python -m src.scripts.seed_crawl romance     # 10 queries at round 0

# guarantee the skit cohort (after Change 4)
make ingest HANDLES=h1,h2,... COUNT=40 WORLD=romance

# the crawl — STOP after the first round and read the yield table
make crawl WORLD=romance ROUNDS=1
make crawl WORLD=romance ROUNDS=2            # rounds 1-2, explore-heavy
# flip N_EXPLORE/N_EXPLOIT to 2/3
make crawl WORLD=romance ROUNDS=3            # rounds 3-5, exploit-heavy
```

`make route` and `make overview` are **not** part of this phase.

---

## Corpus checks

The done-condition. Raw SQL, no code, no `make overview`.

```sql
-- 1. size and VLM coverage
SELECT count(*) AS posts,
       count(DISTINCT account_id) AS accounts,
       count(*) FILTER (WHERE vlm_json IS NOT NULL) AS described
FROM posts WHERE discovered_by_world = 'romance';

-- 2. topic depth distribution — the breadth/depth check
SELECT topic, count(*) AS n_posts, count(DISTINCT account_id) AS n_accounts
FROM posts, LATERAL jsonb_array_elements_text(vlm_json->'topics') AS topic
WHERE discovered_by_world = 'romance' AND vlm_json IS NOT NULL
GROUP BY topic ORDER BY n_posts DESC LIMIT 40;

-- 3. how many topics clear a usable depth bar
SELECT count(*) FROM (
  SELECT topic FROM posts, LATERAL jsonb_array_elements_text(vlm_json->'topics') AS topic
  WHERE discovered_by_world = 'romance' AND vlm_json IS NOT NULL
  GROUP BY topic HAVING count(*) >= 25
) t;

-- 4. did the skit cohort survive?
SELECT vlm_json->>'format' AS format, count(*)
FROM posts WHERE discovered_by_world = 'romance' AND vlm_json IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
```

Healthy looks like: **(1)** 10k+ posts, 250+ accounts, described ≈ 90%+ of posts;
**(2)** recognizable relationship situations at 2-4 words, not `relationship
advice` on every row; **(3)** 40+; **(4)** skit / POV / dialogue formats present
with meaningful counts, not a rounding error.

These count **raw** topic strings — canonicalization has not run, so (2) and (3)
are fragmented and *undercount*. The real post-canonicalization depth will be
higher. Treat these as a floor, not an estimate.

---

## Phases

**Phase 0 — world exists, routing can separate it.** Change 1 + `load_world`.
Seed the world, run `print_similarity_matrix` (already in `seed_worlds.py`).
*Check:* romance scores **below 0.8** against all eight existing worlds. The real
risks are `parenting-baby` and `health-wellness`. If either exceeds 0.8, sharpen
the "Distinct from" clauses in `data/romance/world.txt` and re-seed that row
alone. Do not proceed with an ambiguous world — argmax routing would scatter the
corpus and nothing built on it later would be recoverable without a re-crawl.

**Phase 1 — inputs load, ingest stamps correctly.** Rest of Change 2, plus
Change 4. Hand-seed the peer accounts.
*Check:* the loader check above, and no unexpected `discovered_by_world IS NULL`
bucket after seeding.

**Phase 2 — round 0 yields.** Change 5's constants. `ROUNDS=1`.
*Check — this decides whether the other five rounds are worth running.* Read the
printed `query / handles / new` table. If handles-per-query is tiny, or `new`
collapses toward zero by query 5, relationship search saturates faster than the
loop can exploit and six rounds will mostly re-ingest the same accounts. Then
spot-read ten `vlm_json` rows: is `topics` returning distinct relationship
situations at the stated 2-4 word shape, or degenerating to `relationship advice`
on every post? If topics are that flat, the corpus has nothing to rank later and
the schema needs a romance-aware `topics` description **before** burning five more
rounds and a five-figure token bill.

**Phase 3 — retargeted generation.** Change 3. One more round.
*Check:* the ledger query. Round 1 grounded in round 0's observed topics, not
paraphrases. A sharp `new_handles` drop round 0 → 1 means the generator is
narrowing rather than exploring.

**Phase 4 — the remaining rounds.** Flip the explore/exploit constants at round 3.
Nothing to check mid-flight; the ledger is the log.

**Phase 5 — corpus checks.** The four queries above. This is where the phase ends.

---

## Verification

- **`load_world` / `load_seed_queries` / `load_query_prompt` get an assert-based
  self-check** in `if __name__ == "__main__"`, same pattern as
  `terms.py::_self_check`. They sit between hand-edited text files and the world's
  reference embedding; a silent parse failure is a world seeded with a truncated
  description and a routing failure that never errors. The `load_query_prompt`
  assertion must confirm `.format()` succeeds with all three placeholders and
  returns non-empty — a prompt file that lost its placeholders fails at generation
  time, a full round in.
- **Confirm the eight original world UUIDs are unchanged** after seeding. One
  accidental `reseed-worlds` invalidates every `world_posts` row in the database,
  and the only symptom is empty rankings much later.
- **Re-run corpus check (1) between rounds.** A round that adds posts but no new
  accounts means search has saturated; stop early rather than finishing six.

---

## Applies throughout

YAGNI. Every number here — 20 accounts per query, 6 rounds, 40 posts, the 25-post
depth bar — is a guess until round 0's yield table exists. Run one round, read the
table, then decide whether to spend the other five.

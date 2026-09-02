# Phase 8 — Romance World Crawl

## Goal

Crawl the `romance` world for six rounds to build a corpus of relationship
content. Ranking and analytics are a later phase — this one only fills the tables.

The run is a multi-day unattended foreground process. **Claude Code's job is to
apply the one fix below, start it, and then monitor and debug it while the
terminal stays open.**

## Already implemented — verified against the working tree

Setup is done. Do not redo any of it.

| | |
|---|---|
| `data/romance/{world,seed_queries,query_prompt}.txt`, `data/_default/query_prompt.txt` | written |
| `crawl_config.py` with `load_world` / `load_seed_queries` / `load_query_prompt` + self-check | done |
| `seed_worlds` additive (skips existing slugs), `load_world("romance")` in `WORLDS`, `make seed-world` target | done |
| `seed_crawl` reads `load_seed_queries`; `query_gen` builds its instruction from `load_query_prompt` | done |
| `ACCOUNTS_PER_QUERY = 20` | done |
| `run_ingest` / `make ingest` accept `WORLD=` and stamp `discovered_by_world` | done |
| `query_gen._evidence` world-scoped and cumulative | done |

---

## The one fix — `CLAIM_BATCH_SIZE` will silently destroy ~40% of each round

`describe_posts()` claims at most `CLAIM_BATCH_SIZE = 2500` posts, then calls
`clear_videos()` on the way out — which is `shutil.rmtree(VIDEOS_DIR)`, deleting
**every** video in the directory, not just the claimed ones.

At `ACCOUNTS_PER_QUERY = 20`, one round ingests up to `5 x 20 x 40 = 4000` posts
and downloads 4000 videos. `describe_posts()` describes 2500 and deletes all 4000
files. The remaining ~1500 posts keep `visual_description IS NULL`, have no video
on disk, and **nothing re-downloads them** — the `play` URL is signed and
short-lived, captured only at ingest. They are permanently undescribable: invisible
to `query_gen._evidence`, and dead weight in the corpus.

This was fine at the old `ACCOUNTS_PER_QUERY = 5` (≤1000 posts/round). It is not
fine now. The comment on the constant already flags it as tunable:

```python
CLAIM_BATCH_SIZE = 5000   # must exceed one round's max ingest (QUERIES_PER_ROUND * ACCOUNTS_PER_QUERY * POSTS_PER_ACCOUNT)
```

4000 videos at ~2MB is ~8GB per batch, under the Files API's 20GB quota.

---

## Run

```bash
make seed-world                      # additive; adds romance only
make seed-crawl WORLD=romance
make crawl WORLD=romance ROUNDS=6
```

Check first that nothing else is queued — `describe_posts()` takes no world
argument and claims every undescribed post in the database:

```sql
SELECT count(*) FROM posts WHERE visual_description IS NULL AND visual_attempts < 3;
```

---

## While it runs

The crawl swallows failures by design — `search_handles`, `ingest_account` and
`query_gen.generate` all log a warning and continue rather than raising. That makes
it resumable and makes it fail quietly. Monitoring is the job.

```sql
-- the ledger: is each round yielding?
SELECT round_no, count(*) AS queries, sum(handles_found) AS handles,
       sum(new_handles) AS new, count(*) FILTER (WHERE status='pending') AS pending
FROM crawl_queries WHERE world_slug='romance' GROUP BY round_no ORDER BY round_no;

-- corpus growth; `described` must track `posts`, not lag it
SELECT count(*) AS posts, count(DISTINCT account_id) AS accounts,
       count(*) FILTER (WHERE vlm_json IS NOT NULL) AS described
FROM posts WHERE discovered_by_world='romance';
```

| symptom | cause | action |
|---|---|---|
| loop ends early printing `no pending queries` | `query_gen.generate` returned `[]` — API error, or every candidate rejected as a reworded duplicate. `run_round` still returns True, so the next round finds nothing and exits cleanly. **A six-round run can finish at round two looking like success.** | re-run the same command; if it repeats, read the generator's logged exception |
| `described` lags `posts` by a growing gap | `CLAIM_BATCH_SIZE` too low — the fix above | stop immediately; those posts are unrecoverable |
| the crawl hangs indefinitely | `describe_posts` polls `while not job.done` with no timeout and there is **no `--batch-id` resume flag** (the phase-6 doc planned one; it was never built) | check the batch in the Gemini console; killing the run loses the batch |
| `handles_found = 0` across a whole round | RapidAPI failing or the search response shape changed | check `RAPIDAPI_KEY` and hit the endpoint manually |
| posts stuck undescribed after restarts | `_mark_attempted` increments **before** submitting, so each kill burns an attempt; at `visual_attempts >= 3` a post is skipped forever | avoid repeated kills mid-VLM |
| `new_handles` collapses toward zero | genuine search saturation | stop early, it's finished |
| second run exits immediately and silently | advisory lock 8802 held | expected; don't start two |

**Resume:** kill and re-run the same command. `crawl_queries.status` is committed
per query, ingest is `on_conflict_do_nothing`, and the VLM claim is
`visual_description IS NULL AND visual_attempts < 3`.

---

## Done

```sql
SELECT count(*) AS posts, count(DISTINCT account_id) AS accounts,
       count(*) FILTER (WHERE vlm_json IS NOT NULL) AS described
FROM posts WHERE discovered_by_world='romance';

SELECT topic, count(*) FROM posts,
       LATERAL jsonb_array_elements_text(vlm_json->'topics') AS topic
WHERE discovered_by_world='romance' AND vlm_json IS NOT NULL
GROUP BY topic ORDER BY 2 DESC LIMIT 40;
```

Healthy: 10k+ posts, 250+ accounts, ~90% described, and a topic list of
recognizable relationship situations rather than `relationship advice` on every
row. Those are raw pre-canonicalization strings — they fragment and undercount.

**One hand check after round 0, before the other five run:** read ten `vlm_json`
rows. If `topics` is flat — the same one or two phrases on every post — five more
rounds buys an unrankable corpus and the token bill behind it.

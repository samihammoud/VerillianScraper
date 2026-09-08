# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This is an npm-workspaces monorepo (workspace root: `apps/*`), driven through a `Makefile`:

```bash
make install   # npm install
make build     # npm run build --workspaces --if-present
make test      # npm run test --workspaces --if-present
make lint      # npm run lint --workspaces --if-present
make clean     # remove node_modules and per-app node_modules/dist
make ui        # cd apps/ui && npm run dev — 3D topology viewer (Vite + React Three Fiber)
```

`apps/ui` is a real workspace member now (Vite + React + `@react-three/fiber`/`drei` + `three`) — a 3D scene that renders the topology t-SNE projection served by the backend. `apps/backend` is a Python/FastAPI app with its own `requirements.txt` and `.venv` (not an npm workspace member — see its `docker-compose.yml` for the local Postgres+pgvector container). Its own targets are added directly to the root `Makefile` rather than as workspace scripts:

```bash
make reset-data                        # truncate accounts, posts, topology.world_posts (NOT topology.worlds)
make reseed-worlds                     # re-seed topology.worlds from scratch (deliberate — world UUIDs churn); follow with `make route`
make enrich                            # stage 2 — backfill comments for whatever still lacks them (VLM descriptions run inside `make crawl` instead)
make route                             # stage 3 — batch-embed + route every post in the Account Store to a world
make analyze HANDLE=handle             # stage 4 — peak detection + embedding clustering + cue labeling for one account, dumps a CSV to out/
make overview WORLD=world_slug         # stage 5 — rebuild topology.post_terms/world_term_stats for one world; read via GET /worlds/{slug}/overview
make overview-deep WORLD=world_slug    # stage 5 + phase 9's second passes (cluster_profiles, recency_quadrant) — see below
make redescribe WORLD=slug [WIPE=1] [DRY=1]  # fetch videos for one world's undescribed posts — see below
```

`make redescribe` exists because a described post has no video: the pre-phase-10 pipeline deleted it on success and the TikTok play URL dies within hours, so re-describing means re-listing the account for a fresh signed URL, which can fail permanently (post deleted, account private, aged past the 100-post window). It **selects on `vlm_json IS NULL`** — "still needs describing" — never on "currently has a description": after a wipe every post is NULL, so the latter would match nothing and silently report success having done nothing. Keying on outstanding work makes it idempotent and safe to re-run after a partial failure. Posts already holding a video in GCS are skipped rather than re-downloaded.

`WIPE=1` clears every existing description in the world first, unconditionally. That's the right choice when the defect is *mis-attribution*, since you can't tell which rows are wrong and a confidently-wrong description poisons routing and `world_term_stats` worse than a missing one does — it accepts permanent holes wherever a video can't be refetched. It always dumps `out/vlm_backup_<world>_<ts>.jsonl` (full row state, fsynced) first; that file is the only undo.

`src/scripts/migrate_local_videos.py` is the one-way local-disk → GCS migration for videos predating phase 10. Idempotent and resumable (skips what's already in GCS), retries transient TLS failures per file, and never deletes the local copy.

Backend one-time setup:
```bash
cd apps/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d          # Postgres (pgvector) on localhost:5432
alembic upgrade head
uvicorn src.main:app --reload --port 8000   # serves GET /health and GET /api/topology
```

Requires `RAPIDAPI_KEY`/`RAPIDAPI_HOST` (TikTok scraping), `OPENAI_API_KEY` (`text-embedding-3-small`, routing + world reference embeddings), `GEMINI_API_KEY` (`gemini-3.7-flash`, video VLM description via the Batch API — must be an *authorization key* bound to a service account, not a bare AI Studio key, or `files.register_files` is refused), `GOOGLE_APPLICATION_CREDENTIALS` (service account JSON path, for this project's own GCS calls) and `GCS_BUCKET` (bucket name only, no `gs://`) in `apps/backend/.env` — see `.env.example`.

Note `GOOGLE_APPLICATION_CREDENTIALS` is read by pydantic-settings into the `Settings` object and is **not** exported to `os.environ`, so `google.auth.default()` would not see it — `gcp_auth.py` loads the file by path deliberately, otherwise the storage client silently falls back to whatever gcloud ADC the machine happens to have (a developer's own user account locally, nothing at all on a fresh box).

## Architecture

### Repo layout

- `apps/*` — one directory per application/service, wired as npm workspace members from the repo root.
- `apps/backend` — Python/FastAPI. `src/services/` holds one module per pipeline stage (`ingest`, `enrich`, `routing`, `peaks`, `clustering`, `vision`, `embeddings`, `blob`, `mapper`, `persistence`, `cover_storage`, `csv_export`, `linalg`); `src/scripts/run_*.py` are the CLI entry points the Makefile calls; `src/routes/topology.py` is the one FastAPI route.
- `apps/ui` — Vite + React Three Fiber. Renders `GET /api/topology`'s t-SNE projection of worlds + routed posts as a rotatable 3D scene.
- Root `package.json` / `Makefile` only orchestrate workspace-level scripts; no shared library packages exist yet.

### Project

A scraper that crawls TikTok accounts, classifies their posts into pre-defined semantic "worlds," and uses accumulated post data per world to surface product ideas — content that looks like it's trending or sellable, based on semantic clustering + engagement. LLM-generated product pitches are the eventual goal but are not built yet (see "Explicitly out of scope" below).

### Architecture as built

**Two separate stores, both live:**

- **Account Store** (Postgres) — `accounts` + `posts`. Raw scraped data, plus enrichment fields written back onto the same `posts` row after the initial scrape: `comments` (JSONB, backfilled), `cover_key`/`cover_status` (downloaded cover bytes, see `cover_storage.py`), `visual_description`/`visual_model`/`visual_generated_at` (VLM output on the cover image), and `comment_attempts`/`visual_attempts` retry counters. System of record.
- **Topology Store** (Postgres + pgvector, `topology` schema) — `topology.worlds` (8 hand-authored worlds, seeded by `seed_worlds.py`, each with a `reference_embedding`) and `topology.world_posts` (one row per routed post: `embedding`, `world_id`, `cosine`, `margin` to the runner-up, and the exact `blob_text` that was embedded — kept because comments keep accruing after the scrape, so the blob can't be reconstructed later). Upserted on `post_id`, so re-routing is idempotent.

**Worlds are pre-populated, not discovered.** 8 worlds (`seed_worlds.py`), each with a description + example snippets, embedded once into `reference_embedding`. Still no autonomous world creation or clustering-based world discovery — additions/edits to the world set are a manual, deliberate edit + `make reseed-worlds` + `make route`.

**Ingest is split into two independently-runnable, independently-retryable stages**, not one coupled scrape (see `ingest.py`'s module docstring for the incident that motivated the split — a mid-scrape VLM crash used to lose an entire account's already-paid-for video-list call):
- **Stage 1 — `ingest_account`**: video list (paginated) + cover-image bytes only. Cover bytes are downloaded synchronously here because the *signed URL* expires, not the underlying frame.
- **Stage 2 — `enrich.py`**: backfills `comments` on whatever posts still lack them, via a claim-query + `ThreadPoolExecutor` pass against RapidAPI. `visual_description` no longer gets backfilled here — it's populated inside the crawl loop (`crawl.run_round` calls `vision.describe_posts` per round, since query generation needs that round's descriptions). No queue table — "needs enrichment" is a derivable fact from the row itself (`comments IS NULL`, partial-indexed), and a Postgres advisory lock keeps overlapping runs from double-spending quota.

**Routing is per-post, not per-account**, and is early-fusion, not multi-vector. `blob.py` assembles one token-budgeted text blob per post — visual description (from the VLM, budgeted highest) + caption + top filtered comments — embedded once (`text-embedding-3-small`) and compared via cosine similarity against every competing world's `reference_embedding`; highest similarity wins, margin to the runner-up is stored for a future abstain threshold. Still no video-frame embedding — the visual signal is a VLM *text description* of the cover frame, folded into the same text blob, not a separate visual vector. `run_routing.py` batches this (keyset-paginated, one `embed_batch` + one matmul per page) for bulk use; `routing.route_post`/`persist_routing` are the single-post path used by the smoke test.

**`ai-romance-subworld` never competes in that argmax** (phase 11, `CLAUDEphase11romance-ai-world`). AI-generated vs. real-person romance content isn't a topical distinction an embedding can see — it's a visual/audio rendering-artifact judgment the VLM already made in `synthetic.presenter`/`synthetic.certainty`. So routing is two steps, not one: `run_routing.py` excludes `ai-romance-subworld` from the world matrix entirely, and `routing.apply_ai_romance_split()` reassigns any post that argmaxed to `romance` — `ai_generated_person` → `ai-romance-subworld`, `real_person` → stays `romance`, anything else (low certainty, `unclear`, `animated_character`, ...) → held out of both (no `WorldPost` row; `persist_routing`/`persist_routing_batch` delete any existing one rather than upsert). This is a topical + presenter gate, not a slop filter — on-topic, farmed/repetitive content still passes cleanly; see the phase 11 doc for what's deliberately deferred (slop scoring, craft-space embeddings, cross-tabs).

**Peak + cluster analysis exists (stage 4, `run_analyze.py`) but stops short of LLM pitches.** Per account: `peaks.py` finds view-count outliers vs. that account's own post history (median+MAD, log1p'd, top-10% fallback for small samples), `clustering.py` groups those peaks by cosine similarity over the *already-computed* routing embedding (connected components at a threshold — no re-embedding, no sklearn), and each cluster is labeled from the VLM's structured `products`/`topics` fields (`clustering.product_names`/`clustering.topics`, reading `Post.vlm_json` directly). Output is a CSV per account, not a stored pitch — there is no LLM synthesis step yet.

**A visualization layer exists.** `GET /api/topology` projects all world reference embeddings + routed post embeddings into a shared 3D space via joint t-SNE (not PCA — variance is too spread across the 1536 dims for a linear projection to be meaningful) and returns each post's real cosine similarity to its own world. `apps/ui` renders this as a rotatable Three.js scene.

**Full target loop (future phases — not built):**

```
explore-page API → candidate accounts → crawl_queue
   → pull next pending account
   → [built: ingest → enrich → route → peak/cluster analysis, per account]
→ LLM pitches products from cluster evidence → store pitch
→ pull leads from account metadata (similar accounts, hashtags) + topology → push into crawl_queue
→ repeat
```

### Explicitly out of scope, do not build preemptively

Dedup/content-hash logic beyond the existing external-id uniqueness check, frontier priority scoring, per-world API budgets, autonomous world creation, true video-frame embeddings (the VLM text description is already folded into the routing blob), LLM product-pitch generation/storage, the explore-page endpoint, Instagram support (TikTok only so far). These belong to later phases and get added once the core loop (crawl → enrich → route → analyze) has been run against enough real accounts to validate world descriptions, routing quality, and the peak/cluster heuristics.

### Current status

Five pipeline stages are built and functional, each runnable independently via `make`:

- **`make crawl`** (`crawl.py`) — cycles pending `crawl_queries` for a world: search API → candidate handles → ingest posts+videos per account (Account Store) → VLM video-description pass → mark queries done → generate next round's queries. Round 0 queries for a new world are inserted into `crawl_queries` by hand, not via a script — `crawl_queries.world_slug` is a plain string, not an FK, so the ledger survives a worlds reseed.
- **`make enrich`** (`enrich.py`) — backfills comments per post from RapidAPI, persists to `posts.comments` (JSONB).
- **`make route`** (`routing.py`) — embeds posts (OpenAI `text-embedding-3-small`) using the VLM's video description only — caption/comments were dropped from the routing blob to keep the vector purely topical (see `blob.py`) — cosine-matches against each world's `reference_embedding`, upserts into `topology.world_posts`. Idempotent and re-runnable at any time — a post can switch worlds between runs (upsert on `post_id`), which `overview.py` has to account for (see below).
- **`make analyze HANDLE=...`** (`run_analyze.py`) — peak detection (`peaks.py`) + embedding clustering (`clustering.py`) + cluster labeling, dumps a CSV per account.
- **`make overview WORLD=...`** (`overview.py`/`terms.py`, served read-only at `GET /worlds/{slug}/overview`) — turns one world's routed posts into ranked terms: `terms.extract_terms` projects each post's `vlm_json` into `(facet, raw_term)` edges (mirrors `flatten_for_blob`, same input, different projection), `overview.rollup` embeds+clusters the free-text facets (`CLUSTERED_FACETS`, `CLUSTER_THRESHOLD=0.86`, single-link) into a `canon_term`, and writes the ranked result to `topology.world_term_stats` (deleted and reinserted wholesale per world every run — a cache table, not incrementally updated). `RAW_FACETS` (closed enums — `cta`, `audio_kind`, plus the whole romance relationship/pacing layer below) skip clustering entirely: `canon_term == raw_term`, no embedding spend for strings that are already canonical.
  - **`_extract_and_store` deletes `post_terms` by the `post_id`s being re-extracted, not by `world_id`.** `PostTerm`'s PK is `(post_id, facet, raw_term)` — no `world_id` in it — so scoping the delete to `WHERE world_id = <this world>` leaves a stale row in place for any post that has since routed to a *different* world, and the next insert collides with it on that PK the moment the post migrates back. Real incident, 2026-09-03: `romance`'s `world_posts` went 0 → 5265 in one `make route` run (never routed there before), and every one of those posts' pre-existing rows under their old `world_id` blocked the fresh insert until the delete was rescoped.
  - **How much overview a world gets depends on its VLM schema, and that divergence is deliberate.** Every world runs the same `rollup()`, but the layers only produce output where the schema has fields to feed them: phase 9's premise/hook/punchline clustering (`overview._text_field_clusters`) needs `premise`/`hook`/`punchline`, and `phase9/cluster_profiles.py` needs the `relationship`/`characters`/`synthetic`/`pacing` enums — all romance-schema-only, so on a pets-schema world those layers run clean and report zero clusters rather than erroring. Phase 9's extra passes are *not* inside `rollup()` (they read the `post_terms` it writes), which is why `make overview-deep` exists: `overview` + `cluster_profiles` + `recency_quadrant` for premise and hook. `phase9/stratify.py` stays off that chain on purpose — its stratum argument is a per-question choice, not a fixed step. `phase9/__init__.py` is the authoritative map of which layer lives where and what's deliberately unbuilt.
  - **Phase 11's `setting_cluster` layer is `_text_field_clusters` over `setting` instead of `premise`/`hook`/`punchline`**, reusing premise's exact thresholds (8 posts/5 accounts, 0.97 transcript dedupe) rather than new constants, per `CLAUDEphase11romance-ai-world`'s v1 analysis spec. Unlike those three, `setting` isn't romance-only — it's a required field in every schema and already has its own generic short-noun-phrase rollup via the normal `CLUSTERED_FACETS` path (`terms.py`/`_aggregate`, threshold 0.86). This is a second, sentence-level pass over the same raw field, so it runs for every world through the same `rollup()` loop, not gated to `ai-romance-subworld` — harmless elsewhere, intentionally minimal (v1 is just premise + setting; cross-tabs, craft-space embeddings, and slop scoring are deferred, see the phase 11 doc).
  - **`rollup()` asserts the median lift across all ranked terms is near zero, and that assert is load-bearing.** It fires when the baseline is computed over a different post set than the terms — most commonly a world whose posts are all `vlm_json IS NULL` (after a `redescribe WIPE=1`, or before its first VLM pass), where `world_median` falls back to `0.0` and every lift becomes raw `log1p(views)`. Real occurrence, 2026-09-04: romance had 5265 routed posts and 0 described ones, asserting at `+8.89`. The fix is upstream — describe the posts, `make route`, then overview — never loosening the tolerance.
  - **The VLM's response schema is per-world, not fixed.** `vision_schema.py` loads `data/<world>/crawl/vlm_schema.json`; `ACTIVE_SCHEMA_WORLD` there is the one deliberate, whole-process switch for which world's schema `describe_posts()` uses (it's world-blind at claim time — routing hasn't run yet — so every schema field must be nullable/`not_applicable` for content it doesn't apply to). Two schema versions exist: the leaner pets/product shape, and the romance/dialogue layer (`relationship`, `characters`, `synthetic`, `pacing` — `premise`/`dialogue`/`punchline` too, though those feed prose, not terms). **`extract_terms` does not automatically pick up new schema fields** — it only ever read the fields common to both schemas (`product`/`brand`/`format`/`format_trait`/`topic`/`setting`/`cta`/`audio_kind`) until the romance facets below were added by hand. Adding a new schema field that should show up in an overview means adding it to `extract_terms` explicitly, choosing `RAW_FACETS` (closed enum) vs. `CLUSTERED_FACETS` (free text), and deciding whether `not_applicable`/`unclear` filler values should be excluded (see `_FILLER_VALUES` — `"none"` is deliberately *not* filler; it's a real answer for fields like `synthetic.voice`).

The VLM step is a real batch flow, not synchronous per-video calls: `vision.py` registers each claimed video's GCS URI with the Files API, waits for it to reach `ACTIVE`, submits every request in one `batches.create` call, and polls `job.done` until Gemini finishes the whole job before collecting results. The batch stays `inlined_requests` rather than the GCS-JSONL form: the SDK hard-raises on `gcs_uri`/`format` outside Gemini Enterprise mode (`_BatchJobSource_to_mldev`), and the Developer API's file-based alternative returns responses "in the same order as the input requests" — ordering, which is exactly what the 2026-09-03 incident forbids trusting. Inlined requests keep the `metadata` round-trip that correlation depends on.

**Video bytes live in GCS and are *registered* with Gemini by URI, never uploaded** (phase 10). `video_storage.py` writes `gs://<bucket>/videos/non-described/{post_id}.mp4` at ingest; `vision.py` calls `files.register_files` on that URI. Because no bytes ever enter Files API storage, the 20GB live-storage quota that used to bound a pass is gone — and with it `UPLOAD_LEDGER`, `_ledger_append`, `_recover_orphaned_uploads`, and `CLAIM_BATCH_SIZE`.

**`posts.gemini_file_name` replaced the ledger.** Registration state is now a committed Postgres column, so an interrupted pass resumes from a claim query instead of a file on disk. The VLM pass is two passes over two different sources of truth: pass A claims `gemini_file_name IS NULL` ∧ video present in the GCS listing, registers one URI per call (never bulk — the response's `files` order vs. the input `uris` order is undocumented, and this codebase already lost a day to trusting an unverified order guarantee on this same API), and writes `gemini_file_name` immediately per post. Pass B claims `gemini_file_name IS NOT NULL` ∧ `vlm_json IS NULL`, confirms each registration is still `ACTIVE`, and batches. A registration that has aged out (Files API entries expire after 48h) is detected by `files.get` failing, which clears the column back to NULL so pass A re-registers it — self-healing, no manual purge.

**Registration authenticates with the api_key alone — every OAuth path is a dead end.** `register_files(auth=...)`'s only job is minting a Bearer token, and a Bearer alongside the client's api_key is rejected (`OVERLOADED_CREDENTIALS`), user ADC can't be re-scoped so it fails `ACCESS_TOKEN_SCOPE_INSUFFICIENT`, and a raw service account is refused by name ("Access to Gemini API is restricted with service accounts. Use authorization keys instead"). `GEMINI_API_KEY` already *is* such an authorization key, so `vision.py` calls the private `_register_files(uris=[...])` — the public method minus the token it can't use. `gcp_auth.py`'s service-account credentials are for this project's own GCS calls only; Gemini never sees them.

**Three separate principals need bucket access, and each is only discoverable by triggering its own failure** (verified 2026-09-04):
- `gemini-batch-worker@proud-spring-472720-m6` (ours) — GCS read/write/copy, via `gcp_auth.py`
- `ais-gemini-key-…@676164407184` — the account `GEMINI_API_KEY` is bound to; needs `objectViewer` or **registration** fails
- `service-676164407184@gcp-sa-generativelanguage` — needs `objectViewer` or **inference** fails, and only at describe time, after the batch has run. Missing this one cost a full pass to `code=7 'The caller does not have permission'` with registration having succeeded cleanly.

`files.delete()` drops only the Gemini-side registration; the GCS object is untouched (confirmed empirically), so a post can always be re-registered. `files.list` remains broken Google-side (persistent 500 "Failed to convert server response to JSON", confirmed 2026-09-03 and still failing 2026-09-04) — irrelevant, since nothing in the flow calls it: cleanup is always a targeted `delete` by the exact name echoed back in the response metadata.

`mark_described()` archives `non-described/` → `described/` (copy+delete; GCS has no atomic move) and replaces the old `delete_video()` at the same `_finalize_videos` call site — only for posts that succeeded or exhausted `MAX_ATTEMPTS`, so a post with retries left keeps its video for the next pass.

Cover images are no longer fetched at all — ingest downloads video bytes only (`video_storage.py`); `cover_key`/`cover_status` on `Post` are legacy, unpopulated columns from when a cover-image VLM pass existed. Cluster labeling in `run_analyze.py`/`clustering.py` reads `Post.vlm_json` directly (`product_names`/`topics` helpers in `clustering.py`) rather than parsing a formatted string — this replaced an earlier mismatch where labeling still expected the old 5-line cover-image prompt format that `vision.py` no longer produces.

## Working rules

- Don't build ahead into product-pitch generation or the explore-page endpoint — those depend on decisions not yet finalized.
- Ask before assuming a RapidAPI provider/endpoint, embedding model, or VLM model if one isn't already configured in this repo.
- Keep the scraper wrapper as a standalone, directly-testable module — it should not require the database to be running to verify it works.
- Ingest and enrich are deliberately decoupled — don't recouple comment/video fetching back into the initial scrape call.
- Routing stays early-fusion (one blob, one vector) and now visual-only — don't introduce per-modality vectors, weighted fusion, or add caption/comments back into the routing blob without an explicit decision to do so.
- No cover-image fetching or cover-image VLM path — the VLM only ever describes the full downloaded video. Don't reintroduce cover-image code without an explicit decision to do so.
- Any long-running script launched in the background (`make crawl`, `run_enrich`, `run_routing`, etc.) must be detached from the calling shell/session, not just backgrounded — a plain `&` is still a child of that session and dies with it. Launch as `nohup caffeinate -i <cmd> > logfile 2>&1 < /dev/null & disown`, then confirm with `ps -o pid,ppid,command -p <pid>` that it reparented to PID 1 (launchd). `setsid` is not available on macOS, so don't rely on it. Verified 2026-09-05: a crawl backgrounded without this was killed twice mid-run because its process tree was a direct child of the `claude` CLI process.
- The `caffeinate -i` above matters independently of the detach step: on a laptop, letting the Mac sleep mid-run doesn't kill the process outright, but it always tears down open network connections (every RapidAPI/GCS/Gemini call in flight), which either surfaces as a wave of transient network errors on wake or, worse, an unhandled exception that kills the whole script if that particular call site has no retry around it. `caffeinate -i` prevents idle sleep for the life of the process, so a multi-hour crawl/enrich/route run doesn't depend on the laptop staying awake on its own.

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
```

Backend one-time setup:
```bash
cd apps/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d          # Postgres (pgvector) on localhost:5432
alembic upgrade head
uvicorn src.main:app --reload --port 8000   # serves GET /health and GET /api/topology
```

Requires `RAPIDAPI_KEY`/`RAPIDAPI_HOST` (TikTok scraping), `OPENAI_API_KEY` (`text-embedding-3-small`, routing + world reference embeddings), and `GEMINI_API_KEY` (`gemini-3.7-flash`, video VLM description via the Batch API) in `apps/backend/.env` — see `.env.example`.

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

**Routing is per-post, not per-account**, and is early-fusion, not multi-vector. `blob.py` assembles one token-budgeted text blob per post — visual description (from the VLM, budgeted highest) + caption + top filtered comments — embedded once (`text-embedding-3-small`) and compared via cosine similarity against all 8 world `reference_embedding`s; highest similarity wins, margin to the runner-up is stored for a future abstain threshold. Still no video-frame embedding — the visual signal is a VLM *text description* of the cover frame, folded into the same text blob, not a separate visual vector. `run_routing.py` batches this (keyset-paginated, one `embed_batch` + one matmul per page) for bulk use; `routing.route_post`/`persist_routing` are the single-post path used by the smoke test.

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

Four pipeline stages are built and functional, each runnable independently via `make`:

- **`make crawl`** (`crawl.py`) — cycles pending `crawl_queries` for a world: search API → candidate handles → ingest posts+videos per account (Account Store) → VLM video-description pass → mark queries done → generate next round's queries. Round 0 queries for a new world are inserted into `crawl_queries` by hand, not via a script — `crawl_queries.world_slug` is a plain string, not an FK, so the ledger survives a worlds reseed.
- **`make enrich`** (`enrich.py`) — backfills comments per post from RapidAPI, persists to `posts.comments` (JSONB).
- **`make route`** (`routing.py`) — embeds posts (OpenAI `text-embedding-3-small`) using the VLM's video description only — caption/comments were dropped from the routing blob to keep the vector purely topical (see `blob.py`) — cosine-matches against each world's `reference_embedding`, upserts into `topology.world_posts`.
- **`make analyze HANDLE=...`** (`run_analyze.py`) — peak detection (`peaks.py`) + embedding clustering (`clustering.py`) + cluster labeling, dumps a CSV per account.

The VLM step is a real batch flow, not synchronous per-video calls: `vision.py` uploads each claimed video via the Files API, waits for it to reach `ACTIVE`, submits every request in one `batches.create` call, and polls `job.done` until Gemini finishes the whole job before collecting results.

**`UPLOAD_LEDGER` (`vision.py`) tracks in-flight Gemini file uploads so cleanup never depends on `files.list`.** The Files API has a 20GB live-storage quota shared across everything uploaded and not yet deleted; on success, each file is deleted by the exact name already in hand (`request.metadata["file_name"]` in `_collect_and_cleanup`, or the `File` object itself in `_await_active`'s failure branches) — no listing involved on the happy path. The gap was always what happens when a run dies *before* reaching one of those points, since nothing durable recorded which files had been uploaded. `UPLOAD_LEDGER` (`out/gemini_uploads.txt`) closes that gap: `_submit_upload` appends each uploaded file's name to it (lock-guarded — `VIDEO_WORKERS` threads write concurrently), and `describe_posts()` calls `_recover_orphaned_uploads()` at the start of every pass, which deletes every name still listed (leftover from a prior crash) and clears the file. On a clean pass the ledger is unlinked at the end, since every entry it holds was deleted on that same pass.

Fail states this addresses, and what happens in each:
- **Process killed/crashed mid-upload** (before a file reaches `ACTIVE` or gets batched) — file has no record anywhere except the ledger. Next `describe_posts()` call reads the ledger and deletes it by name.
- **Process killed/crashed while polling for `ACTIVE`, or during `batches.create`/`job.done` polling** — same: only the ledger knows the file exists at that point.
- **Process killed/crashed after the batch finishes but before `_collect_and_cleanup` runs for every pair** — any file not yet individually deleted is still in the ledger; recovered next pass.
- **A file fails upload outright** (`_submit_upload` catches and returns `None`) — never appended to the ledger in the first place, nothing to recover.
- **A file reaches a terminal non-`ACTIVE` state, or times out stuck in `PROCESSING`** — already deleted synchronously inside `_await_active`; ledger entry becomes stale but harmless (unlinked at the end of a clean pass regardless).
- **`files.list` itself is down** (a real Google-side incident, confirmed 2026-09-03: persistent 500 "Failed to convert server response to JSON" at every page size, reproduced via raw REST bypassing the SDK) — irrelevant now; recovery never calls `list`, only targeted `delete` by name.

When to reach for this vs. an ad hoc `files.list`-based purge: always prefer the ledger path (it's automatic, runs every pass) — a manual list-and-purge is only a fallback for orphans that predate the ledger's existence (nothing recorded their names) or that were uploaded by some other process/script entirely outside `vision.py`.

Cover images are no longer fetched at all — ingest downloads video bytes only (`video_storage.py`); `cover_key`/`cover_status` on `Post` are legacy, unpopulated columns from when a cover-image VLM pass existed. Cluster labeling in `run_analyze.py`/`clustering.py` reads `Post.vlm_json` directly (`product_names`/`topics` helpers in `clustering.py`) rather than parsing a formatted string — this replaced an earlier mismatch where labeling still expected the old 5-line cover-image prompt format that `vision.py` no longer produces.

## Working rules

- Don't build ahead into product-pitch generation or the explore-page endpoint — those depend on decisions not yet finalized.
- Ask before assuming a RapidAPI provider/endpoint, embedding model, or VLM model if one isn't already configured in this repo.
- Keep the scraper wrapper as a standalone, directly-testable module — it should not require the database to be running to verify it works.
- Ingest and enrich are deliberately decoupled — don't recouple comment/video fetching back into the initial scrape call.
- Routing stays early-fusion (one blob, one vector) and now visual-only — don't introduce per-modality vectors, weighted fusion, or add caption/comments back into the routing blob without an explicit decision to do so.
- No cover-image fetching or cover-image VLM path — the VLM only ever describes the full downloaded video. Don't reintroduce cover-image code without an explicit decision to do so.

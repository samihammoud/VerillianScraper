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
make ingest HANDLES=h1,h2 COUNT=100    # stage 1 — video list + cover bytes only, paginated
make enrich                            # stage 2 — backfill comments + VLM cover descriptions for whatever still lacks them
make route                             # stage 3 — batch-embed + route every post in the Account Store to a world
make analyze HANDLE=handle             # stage 4 — peak detection + embedding clustering + cue labeling for one account, dumps a CSV to out/
make smoke-test                        # run_smoke_test.py — ingest a fixed 4-account roster, route, dump a CSV to out/
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

Requires `RAPIDAPI_KEY`/`RAPIDAPI_HOST` (TikTok scraping), `OPENAI_API_KEY` (`text-embedding-3-small`, routing + world reference embeddings), and `GEMINI_API_KEY` (`gemini-3.6-flash`, cover-image VLM description) in `apps/backend/.env` — see `.env.example`.

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
- **Stage 2 — `enrich.py`**: backfills `comments` and `visual_description` on whatever posts still lack them, via two independent claim-query + `ThreadPoolExecutor` passes (different quotas/failure domains: RapidAPI vs. Gemini). No queue table — "needs enrichment" is a derivable fact from the row itself (`comments IS NULL`, partial-indexed), and a Postgres advisory lock keeps overlapping runs from double-spending quota.

**Routing is per-post, not per-account**, and is early-fusion, not multi-vector. `blob.py` assembles one token-budgeted text blob per post — visual description (from the VLM, budgeted highest) + caption + top filtered comments — embedded once (`text-embedding-3-small`) and compared via cosine similarity against all 8 world `reference_embedding`s; highest similarity wins, margin to the runner-up is stored for a future abstain threshold. Still no video-frame embedding — the visual signal is a VLM *text description* of the cover frame, folded into the same text blob, not a separate visual vector. `run_routing.py` batches this (keyset-paginated, one `embed_batch` + one matmul per page) for bulk use; `routing.route_post`/`persist_routing` are the single-post path used by the smoke test.

**Peak + cluster analysis exists (stage 4, `run_analyze.py`) but stops short of LLM pitches.** Per account: `peaks.py` finds view-count outliers vs. that account's own post history (median+MAD, log1p'd, top-10% fallback for small samples), `clustering.py` groups those peaks by cosine similarity over the *already-computed* routing embedding (connected components at a threshold — no re-embedding, no sklearn), and each cluster is labeled from the VLM's parsed `PRODUCTS`/`CATEGORY CUES` fields (`vision.parse_visual_description`). Output is a CSV per account, not a stored pitch — there is no LLM synthesis step yet.

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

Dedup/content-hash logic beyond the existing external-id uniqueness check, frontier priority scoring, per-world API budgets, autonomous world creation, true video-frame embeddings (as opposed to the VLM text description already folded into the routing blob), LLM product-pitch generation/storage, the crawl queue, the explore-page endpoint, Instagram support (TikTok only so far). These belong to later phases and get added once the core loop (ingest → enrich → route → analyze) has been run against enough real accounts to validate world descriptions, routing quality, and the peak/cluster heuristics.

### Current phase: Phase 5 — validate routing/clustering quality on real data, then LLM pitch synthesis

Ingest, enrich, routing, and peak/cluster analysis are all built and runnable end-to-end (`make ingest` → `make enrich` → `make route` → `make analyze`), plus a topology API + 3D viewer. What's not yet validated or built:

- Real-data validation of world separation (`seed_worlds.py`'s `print_similarity_matrix`) and the clustering similarity threshold (`clustering.DEFAULT_SIMILARITY_THRESHOLD = 0.70` is an unvalidated starting guess — inspect the printed off-diagonal distribution against real accounts before trusting it).
- The margin/abstain threshold on routing confidence (`WorldPost.margin` is stored but nothing consumes it yet).
- LLM product-pitch generation from cluster evidence (semantic + engagement patterns) — the next real feature to build once the above is trusted.

## Working rules

- Don't build the crawl queue, explore-page integration, or LLM pitch generation until routing/clustering quality has been validated against real accounts — see "Current phase."
- Ask before assuming a RapidAPI provider/endpoint, embedding model, or VLM model if one isn't already configured in this repo.
- Ingest (stage 1) and enrich (stage 2) are deliberately decoupled — don't recouple comment/visual fetching back into the initial scrape call.
- Routing stays early-fusion (one blob, one vector) — don't introduce per-modality vectors or weighted fusion without an explicit decision to do so.

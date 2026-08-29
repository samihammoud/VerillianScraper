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
```

These `make` targets are thin wrappers around the equivalent root `npm run <script> --workspaces --if-present` commands in `package.json`.

No individual app currently has its own `package.json`, build/test/lint scripts, or dependency manifest (e.g. `apps/backend/testing.py` has no `pyproject.toml`/`requirements.txt` yet), so the commands above are no-ops until an app defines them. Set up each app's own tooling (and single-test invocation) as it's built out, and document it here once it exists.

## Architecture

### Repo layout

- `apps/*` — one directory per application/service, wired as npm workspace members from the repo root.
- Root `package.json` / `Makefile` only orchestrate workspace-level scripts; no shared library packages exist yet.

### Project

A scraper that crawls TikTok/Instagram accounts, classifies their posts into pre-defined semantic "worlds," and uses accumulated post data per world to surface product ideas — content that looks like it's trending or sellable, based on semantic clustering + engagement, eventually pitched via an LLM.

### Target architecture (mostly built — see "Current phase" below for what's left)

**Two separate stores:**

- **Account Store** (Postgres) — raw scraped data. Accounts + posts, untouched, as scraped. System of record.
- **Topology Store** (Postgres + pgvector) — worlds (pre-defined, migrated in by hand — not discovered by the system) + individual post embeddings, each tagged with which world it routed into. Lean, queried constantly for similarity.

**Worlds are pre-populated, not discovered.** 6–8 worlds, manually defined with a short description each, embedded once into a `reference_embedding`, used as fixed routing targets. No autonomous world creation, no clustering-based world discovery.

**Routing is per-post, not per-account.** Each post gets its own embedding (caption + comments as text input for now — no video-frame embedding yet) and gets compared via cosine similarity against every world's `reference_embedding`. Highest similarity wins. An account's posts can land in different worlds; there is no single "account vector."

**Full target loop (future phases):**

```
explore-page API → candidate accounts → crawl_queue
   → pull next pending account
   → for each post: scrape → store raw (Account Store) → embed → route to world → store (Topology Store)
→ account fully processed → gather evidence (semantic + engagement patterns) → LLM pitches products → store pitch
→ pull leads from account metadata (similar accounts, hashtags) + topology → push into crawl_queue
→ repeat
```

### Explicitly out of scope, do not build preemptively

Dedup/content-hash logic, frontier priority scoring, per-world API budgets, autonomous world creation, video-frame embeddings, product-pitch generation, the explore-page endpoint. These belong to later phases and get added once the core loop is proven — building them now creates plumbing around decisions (routing behavior, pitch format) that haven't been validated yet.

### Current status

Three pipeline stages are built and functional, each runnable independently via `make`:

- **`make crawl`** (`apps/backend/src/services/crawl.py`) — cycles pending `crawl_queries` for a world: search API → candidate handles → ingest posts+videos per account (Account Store) → VLM visual-description pass → mark queries done → generate next round's queries. Queries are seeded via `make seed-crawl` (which requires `make seed-worlds` to have run first — `crawl_queries.world_slug` is a plain string, not an FK, so the ledger survives a worlds reseed; see comment at `models.py:88-91`).
- **`make enrich`** (`apps/backend/src/services/enrich.py`) — fetches comments per post from RapidAPI, persists to `posts.comments` (JSONB).
- **`make route`** (`apps/backend/src/services/routing.py`) — embeds posts (OpenAI `text-embedding-3-small`), cosine-matches against each world's `reference_embedding`, upserts into `topology.world_posts`.

These three do not call each other — no stage chains into another. Comment enrichment and topology routing are not wired into the crawl loop.

**In progress:** swapping the crawl loop's VLM step from synchronous per-video calls (`vision.py`'s `describe_posts()`, a 5-way thread pool of blocking `generate_content()` calls) to a real batch flow — upload → JSONL → submit → poll → collect — per the stub comment at `vision.py:43-46`.

## Working rules

- Don't build ahead into product-pitch generation or the explore-page endpoint — those depend on decisions not yet finalized.
- Ask before assuming a RapidAPI provider/endpoint if one isn't already configured in this repo.
- Keep the scraper wrapper as a standalone, directly-testable module — it should not require the database to be running to verify it works.

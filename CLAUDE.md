# VerillianScraper

This repository crawls TikTok accounts, stores their posts and downloaded videos, uses Gemini to describe each video, routes described posts into a hand-authored set of semantic worlds, and analyzes the resulting topology. The repository is intentionally organized around those boundaries.

## The actual flow

The normal data flow is:

```text
discover accounts and videos
        |
        v
1. fetch / ingest --------------------> Account Store: accounts, posts, GCS videos
        |
        v
2. VLM describe ---------------------> posts.vlm_json + visual_description
        |
        v
3. route posts ----------------------> Topology Store: topology.world_posts
        |
        v
4. analyze each world ----------------> post_terms, world_term_stats, cluster_profiles
        |
        v
        API and Three.js UI
```

That is the right conceptual sequence. The implementation has two deliberate details:

- `make crawl` is the orchestration loop for discovery, ingest, and a VLM pass. It is not a new fifth data-processing stage. It runs search, fetches each discovered account, persists posts and videos, describes available videos, then generates the next round of search queries.
- Comments are optional enrichment, not a prerequisite for routing. `make enrich` backfills comments independently. Routing currently uses the VLM's visual description only, so comments do not need to be complete before routing.

Each handoff is durable. A failed later stage does not erase earlier work, and the stages can be rerun independently:

1. Ingest writes the account/post rows and stores video bytes in GCS.
2. Vision writes structured and flattened VLM output back to the post row.
3. Routing reads described posts and upserts one topology assignment per post.
4. Analysis reads topology assignments and writes derived world-level results.

## Repository layout

```text
apps/backend/
  data/<world>/crawl/              World crawl prompts and VLM response schemas
  src/
    analysis/
      peaks.py                    Account-level peak detection primitives
      clustering.py               Shared embedding clustering and labels
      topology/
        overview.py               World term rollup and topology analysis
        terms.py                  VLM JSON -> post_terms projection
                                accounts/
                                        patterns.py             World-scoped account peak patterns
        phase9/                   Romance/topology second-pass analyses
    db/                            SQLAlchemy models, sessions, migration support
    routes/                        Read/write API routes; no ranking recomputation
    scripts/                       CLI entry points used by Makefile
    services/                      Operational pipeline services
      ingest.py                   TikTok account/post fetch and GCS video storage
      vision.py                   Gemini file registration and batch VLM pass
      routing.py                  Embedding, world scoring, and persistence helpers
      enrich.py                   Independent comment backfill
      crawl.py                    Discovery -> ingest -> VLM orchestration
      vision_schema.py            Active schema loader and VLM flattening contract
      video_storage.py            GCS non-described/described video lifecycle
      tiktok_client.py            RapidAPI/TikTok client
      persistence.py, mapper.py   Account Store mapping and writes
      embeddings.py, blob.py      OpenAI embedding and routing blob helpers
      query_gen.py, crawl_config.py
                                   Crawl prompts, query generation, configuration
      gcp_auth.py, linalg.py, csv_export.py
                                   Supporting infrastructure
  out/                             Generated CSVs, backups, and analysis caches

apps/ui/                            Vite + React Three Fiber topology viewer
```

`src/services` contains operational services that move data through the pipeline. `src/analysis` contains computation over data that has already been stored. `src/analysis/topology` is specifically for analysis whose unit is a world or the topology assigned to worlds. This distinction is architectural, not just cosmetic: analysis must not fetch TikTok data or call Gemini as a side effect.

The CLI locations remain stable under `src/scripts` so existing commands keep working. The implementation they call makes ownership explicit:

- `run_crawl.py` -> `services/crawl.py`
- `run_enrich.py` -> `services/enrich.py`
- `run_routing.py` -> `services/routing.py`
- `run_analyze.py` -> `analysis/peaks.py` + `analysis/clustering.py`
- `run_overview.py` -> `analysis/topology/overview.py`

## Two stores

### Account Store

Postgres tables `accounts` and `posts` are the system of record for scraped TikTok data. A post can contain raw caption, media URLs, publish time, engagement counts, comments and retry state, structured `vlm_json`, flattened `visual_description`, model and timestamp, Gemini registration state in `gemini_file_name`, and discovery provenance in `discovered_by_world` and `discovered_by_query_id`.

Video bytes are stored in GCS, not in Postgres. New videos land under `videos/non-described/<post_id>.mp4`. Successful or terminal VLM work archives them under `videos/described/`. The TikTok play URL is signed and short-lived, so downloading happens during ingest while it is usable.

### Topology Store

The `topology` schema contains:

- `topology.worlds`: manually authored worlds and their reference embeddings;
- `topology.world_posts`: the current winning world for each routed post, its embedding, cosine score, runner-up margin, and the exact embedded blob;
- `topology.post_terms`: extracted facet edges for world analysis;
- `topology.world_term_stats`: cached ranked terms and lift per world;
- phase-specific derived tables such as `topology.cluster_profiles`, plus pattern notes and term merge/override data.

Worlds are not discovered automatically. Adding or changing a world is a deliberate seed-data change followed by `make seed-world` or `make reseed-worlds`, then a routing pass.

## Stage ownership and invariants

### 1. Fetch and ingest

`services/ingest.py` fetches an account's TikTok video list, maps it into Account Store rows, and downloads new video bytes to GCS. It does not fetch comments or call Gemini. `services/crawl.py` owns search, account discovery, query bookkeeping, and the loop that calls ingest.

The crawl is resumable: query status is committed per query, post insertion is conflict-safe, and downloaded video state is visible in GCS. A missing video can be repaired with `make backfill-videos`.

Cover images are legacy columns and are not populated. The VLM describes the full downloaded video. Do not reintroduce a cover-image VLM path without an explicit design decision.

### 2. VLM description

`services/vision.py` registers GCS URIs with Gemini, waits for registrations to be active, submits an inline batch, and correlates every response through its own metadata. It writes both `vlm_json` and `visual_description` to the post. It never trusts response ordering.

The VLM is world-blind at claim time: routing has not happened yet. The active schema is selected once per process by `services/vision_schema.py` using `ACTIVE_SCHEMA_WORLD`, which loads:

```text
apps/backend/data/<world>/crawl/vlm_schema.json
apps/backend/data/<world>/crawl/vlm_system_instruction.txt
```

All fields that do not apply to a video must allow `not_applicable` or null. When a schema field is intended for topology analysis, add its explicit projection to `analysis/topology/terms.py`; schema changes do not automatically appear in overview output.

Gemini registration is separate from GCS access. The Gemini authorization key and Gemini batch service principal need object-viewer access to the bucket; the project's service account is used by this application for its own GCS calls.

### 3. Route posts to worlds

`services/routing.py` and `scripts/run_routing.py` route one post at a time in bulk pages. The current routing contract is early fusion with one text vector:

```text
VLM visual_description -> blob.py -> text-embedding-3-small
                      -> cosine against every competing world reference vector
                      -> winning world + margin in topology.world_posts
```

The routing blob is visual-only by deliberate design. Do not add caption, comments, per-modality vectors, or weighted fusion without an explicit decision. Posts without a usable description are skipped for a later pass. Routing is idempotent and may move a post to a different world on a later run.

`ai-romance-subworld` is not part of the embedding argmax. A post that first wins `romance` is split using `vlm_json.synthetic.presenter` and certainty: AI-generated people go to the subworld, real people stay in romance, and unclear or low-certainty cases are held out of topology.

### 4. Analyze topology within worlds

Everything under `analysis/topology` reads already-routed data. It does not perform account discovery, ingest, VLM calls, or routing.

- `overview.py` extracts terms, canonicalizes free text, clusters eligible facets, computes support and lift, and rebuilds the cached `world_term_stats` rows for a world.
- `terms.py` is the explicit projection from `vlm_json` to `post_terms`. Closed enums are raw facets; free-text facets are normalized and clustered.
- `phase9/` contains romance-oriented premise, hook, punchline, recency, stratification, and cluster-profile analysis. These passes are optional and only produce meaningful output when the active VLM data contains their fields.
- `topology/accounts/patterns.py` exposes world-scoped account peak patterns to
        the API. This is distinct from account-wide analysis: the endpoint selects
        accounts represented in a world, then reports their repeated peak patterns.
- `analysis/peaks.py` and `analysis/clustering.py` provide reusable pure computation for account-level peak analysis and topology clustering.

The normal topology analysis sequence is `make overview WORLD=...`, followed by `make overview-deep WORLD=...` when the phase-9 second passes are wanted. The overview baseline and term population must use the same described-post population. A large median-lift assertion usually means analysis ran before VLM description completed; fix the upstream data rather than weakening the assertion.

## Commands

```bash
make install
make build
make test
make lint

make crawl WORLD=pets ROUNDS=1       # discover -> ingest -> VLM -> next queries
make enrich                          # optional comments-only backfill
make backfill-videos WORLD=pets      # restore missing GCS videos, then describe
make redescribe WORLD=romance        # refetch and describe after schema/misattribution issue
make route                           # route all described posts
make route-new                       # route only posts without a topology row
make analyze HANDLE=somehandle       # account peaks + clusters -> out/*.csv
make overview WORLD=pets             # world term rollup -> topology cache tables
make overview-deep WORLD=romance     # overview plus phase-9 second passes
make ui                              # topology viewer
make serve                           # FastAPI on port 8000
make dev                             # backend and UI together
```

`make reset-data` removes accounts, posts, and routed posts but preserves hand-authored worlds. `make reseed-worlds` truncates and recreates worlds, which changes world UUIDs; reroute afterwards. `make redescribe WIPE=1` is destructive but writes an `out/vlm_backup_<world>_<timestamp>.jsonl` first.

Backend setup:

```bash
cd apps/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d
alembic upgrade head
uvicorn src.main:app --reload --port 8000
```

Required environment variables are documented in `apps/backend/.env.example`: RapidAPI credentials, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, and `GCS_BUCKET`. The Gemini key must be an authorization key bound to the configured service account. The GCS service account path is loaded explicitly by `gcp_auth.py`; it is not assumed to be exported into the process environment.

## Explicit non-goals

Do not build ahead of the current architecture. The following remain out of scope: autonomous world creation, Instagram support, frontier priority scoring, per-world API budgets, content hashing beyond the existing external-id constraint, true video-frame embeddings, LLM product-pitch generation and storage, and an explore-page API.

## Working rules

- Preserve the ingest/VLM split. A VLM failure must not lose a paid scrape.
- Keep scraper clients independently testable without requiring a database.
- Treat `vlm_json` and `analysis/topology/terms.py` as a paired contract.
- Keep routing visual-only and early-fusion unless the design is explicitly changed.
- Keep analysis pure over stored data; do not hide external API calls in topology analysis modules.
- Do not commit, reset, or discard user changes while working in this repo.
- Long-running commands on macOS must be detached with `nohup caffeinate -i`, redirected to a log, and checked for reparenting to launchd.

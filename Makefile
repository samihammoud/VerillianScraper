.PHONY: install build test lint clean reset-data reseed-worlds seed-world enrich route crawl backfill-videos redescribe analyze overview overview-deep ui serve dev

BACKEND := apps/backend
# Relative to $(BACKEND) on purpose: every recipe using it cd's there first.
PY := .venv/bin/python
COUNT ?= 15
WORLD ?= pets
ROUNDS ?= 1

install:
	npm install

build:
	npm run build --workspaces --if-present

test:
	npm run test --workspaces --if-present

lint:
	npm run lint --workspaces --if-present

clean:
	rm -rf node_modules apps/*/node_modules apps/*/dist

# Wipes scraped + derived data only (accounts, posts, topology.world_posts).
# Leaves topology.worlds alone — that's hand-authored reference data, not test
# data; see `reseed-worlds` for the deliberate-act path that touches it.
reset-data:
	cd $(BACKEND) && docker compose exec -T postgres psql -U verillian -d verillian \
		-c "TRUNCATE posts, accounts, topology.world_posts;"

# Re-seeds topology.worlds from scratch (world_posts cascades since it FKs worlds).
# Deliberate, not part of a routine test reset — world UUIDs churn on re-seed, and
# existing world_posts rows would otherwise point at stale worlds. Run `make route`
# afterward to re-route everything against the new world set.
reseed-worlds:
	cd $(BACKEND) && docker compose exec -T postgres psql -U verillian -d verillian \
		-c "TRUNCATE topology.worlds CASCADE;"
	cd $(BACKEND) && $(PY) -m src.scripts.seed_worlds

# Additive: adds any WORLDS entries not already in topology.worlds. Never
# touches existing rows/UUIDs — the safe way to add a new world.
seed-world:
	cd $(BACKEND) && $(PY) -m src.scripts.seed_worlds

# Bulk pipeline, independently runnable/re-runnable stages. Stage 1 (video
# list + bytes) runs only via `make crawl` now — accounts are discovered
# through search, not passed in by handle.
# stage 2 — comments + VLM descriptions, backfilled over whatever still lacks them
enrich:
	cd $(BACKEND) && $(PY) -m src.scripts.run_enrich

# stage 3 — batch-embed + route to worlds
route:
	cd $(BACKEND) && $(PY) -m src.scripts.run_routing

# same, but skips posts that already have a world_posts row — after a crawl that
# only added posts, re-embedding the whole corpus buys nothing
route-new:
	cd $(BACKEND) && $(PY) -m src.scripts.run_routing new

# stage 4 — peak analysis for one account: pure compute over Postgres, no external API
analyze:
	cd $(BACKEND) && $(PY) -m src.scripts.run_analyze "$(HANDLE)"

# phase 7 — per-world term rollup (what's winning in this world). Run after `make route`.
overview:
	cd $(BACKEND) && $(PY) -m src.scripts.run_overview $(WORLD)

# phase 9 — the full romance overview strategy (CLAUDEphase9romanceoverview.md).
# Layers 1-3 (premise/hook/punchline clustering) already run inside `overview`
# itself; these are the two second passes over the post_terms it writes.
# Runs on any world, but only the romance VLM schema has the relationship/
# pacing enums cluster_profiles reports on — elsewhere its distributions are
# empty. `stratify.py` is deliberately not chained here: it needs a stratum
# argument you choose per question, e.g.
#   $(PY) -m src.services.phase9.stratify romance premise_cluster estimated_duration_sec:lt15
overview-deep: overview
	cd $(BACKEND) && $(PY) -m src.services.phase9.cluster_profiles $(WORLD)
	cd $(BACKEND) && $(PY) -m src.services.phase9.recency_quadrant $(WORLD)
	cd $(BACKEND) && $(PY) -m src.services.phase9.recency_quadrant $(WORLD) hook_cluster

# Crawl loop: search API -> candidate accounts -> ingest -> VLM -> next round's queries.
# Round 0 queries aren't seeded by a script — insert them into crawl_queries
# by hand (world_slug, round_no=0, query_text, intent='seed', status='pending')
# before the first run for a new world.
crawl:
	cd $(BACKEND) && $(PY) -m src.scripts.run_crawl $(WORLD) $(ROUNDS)

# Re-fetch videos for posts whose download failed at ingest, then describe them.
# Scoped by discovered_by_world (not routing — these posts can't have routed).
# Refuses to run while a crawl holds the advisory lock.
backfill-videos:
	cd $(BACKEND) && $(PY) -m src.scripts.backfill_videos $(WORLD)

# Re-run the VLM over one world after its response schema changed. Re-fetches
# each post's video (they're deleted on successful description, and the play
# URL is long dead) and only then clears vlm_json — a post whose video can't
# be recovered keeps its old description. DRY=1 to see scope first; ALL=1 to
# include posts that already carry the current schema.
redescribe:
	cd $(BACKEND) && $(PY) -m src.scripts.redescribe_world $(WORLD) $(if $(WIPE),--wipe-first,) $(if $(DRY),--dry-run,)

ui:
	cd apps/ui && npm run dev

serve:
	cd $(BACKEND) && .venv/bin/uvicorn src.main:app --reload --port 8000

# runs backend + ui together; Ctrl-C kills both
dev:
	$(MAKE) -j2 serve ui

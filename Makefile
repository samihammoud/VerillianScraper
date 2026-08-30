.PHONY: install build test lint clean reset-data reseed-worlds ingest enrich route seed-crawl crawl smoke-test analyze ui serve dev

BACKEND := apps/backend
PY := $(BACKEND)/.venv/bin/python
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

# Bulk pipeline, independently runnable/re-runnable stages:
# stage 1 — video list + cover bytes only, paginated
ingest:
	@test -n "$(HANDLES)" || { echo "usage: make ingest HANDLES=h1,h2 [COUNT=40]"; exit 1; }
	cd $(BACKEND) && $(PY) -m src.scripts.run_ingest "$(HANDLES)" $(COUNT)

# stage 2 — comments + VLM descriptions, backfilled over whatever still lacks them
enrich:
	cd $(BACKEND) && $(PY) -m src.scripts.run_enrich

# stage 3 — batch-embed + route to worlds
route:
	cd $(BACKEND) && $(PY) -m src.scripts.run_routing

smoke-test:
	cd $(BACKEND) && $(PY) -m src.scripts.run_smoke_test

# stage 4 — peak analysis for one account: pure compute over Postgres, no external API
analyze:
	cd $(BACKEND) && $(PY) -m src.scripts.run_analyze "$(HANDLE)"

# Crawl loop: search API -> candidate accounts -> ingest -> VLM -> next round's queries.
seed-crawl:
	cd $(BACKEND) && $(PY) -m src.scripts.seed_crawl $(WORLD)

crawl:
	cd $(BACKEND) && $(PY) -m src.scripts.run_crawl $(WORLD) $(ROUNDS)

ui:
	cd apps/ui && npm run dev

serve:
	cd $(BACKEND) && .venv/bin/uvicorn src.main:app --reload --port 8000

# runs backend + ui together; Ctrl-C kills both
dev:
	$(MAKE) -j2 serve ui

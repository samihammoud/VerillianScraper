.PHONY: install build test lint clean ingest enrich route seed-crawl crawl smoke-test

# apps/backend is not an npm workspace member — its targets live here directly.
BACKEND = cd apps/backend && .venv/bin/python -m
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

ingest:
	@test -n "$(HANDLES)" || { echo "usage: make ingest HANDLES=h1,h2 [COUNT=40]"; exit 1; }
	$(BACKEND) src.scripts.run_ingest $(HANDLES) $(COUNT)

enrich:
	$(BACKEND) src.scripts.run_enrich

route:
	$(BACKEND) src.scripts.run_routing

smoke-test:
	$(BACKEND) src.scripts.run_smoke_test

seed-crawl:
	$(BACKEND) src.scripts.seed_crawl $(WORLD)

crawl:
	$(BACKEND) src.scripts.run_crawl $(WORLD) $(ROUNDS)

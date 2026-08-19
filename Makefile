.PHONY: install build test lint clean

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

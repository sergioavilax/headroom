# Headroom — developer entry points. `make help` lists targets.
.DEFAULT_GOAL := help
.PHONY: help up down logs ps test lint typecheck fmt migrate seed ui-check ui-e2e

help: ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

up: ## Build and start the stack (db, dynamodb, gateway); waits for healthy, then migrates
	docker compose up -d --build --wait
	@# Since Phase 2 the gateway needs a schema to authenticate against, so `up` is
	@# only honestly "up" once the migrations are applied. Run inside the container so
	@# it uses the compose DATABASE_URL and needs nothing installed on the host.
	docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
	docker compose ps

down: ## Stop the stack (keeps the db volume; add -v by hand to wipe it)
	docker compose down

logs: ## Tail service logs
	docker compose logs -f

ps: ## Show service status
	docker compose ps

test: ## Run the test suite — keyless; `live` tests are excluded by default
	uv run pytest

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format Python
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy (strict)
	uv run mypy

migrate: ## Apply migrations/*.sql in filename order (from the host, against DATABASE_URL)
	uv run python -m headroom.db.migrate

seed: ## Fill the local stack with traffic worth looking at (needs HEADROOM_ADMIN_TOKEN)
	@# Through /admin/* and /v1/* only — no SQL, no back door, no spend. Pass
	@# SPREAD=180 to pace the run so the dashboard's charts get several buckets.
	uv run python scripts/seed_demo.py $(if $(SPREAD),--spread-s $(SPREAD),)

ui-check: ## eslint + tsc + the dashboard's unit tests, in a container (no host Node needed)
	@# The `check` stage of ui/Dockerfile carries devDependencies and the sources; the
	@# runtime stage does not. Docker's layer cache makes the second run fast.
	docker build --target check -t headroom-ui-check ./ui
	docker run --rm headroom-ui-check npm run check

ui-e2e: ## The Playwright smoke, against a stub gateway (hermetic; no compose, no keys)
	docker build --target e2e -t headroom-ui-e2e ./ui
	docker run --rm headroom-ui-e2e npm run e2e

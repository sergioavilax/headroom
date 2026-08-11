# Headroom — developer entry points. `make help` lists targets.
.DEFAULT_GOAL := help
.PHONY: help up down logs ps test lint typecheck fmt migrate seed ui-check ui-e2e \
        rollup lambda-build tf-check chaos-smoke

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

# --- Phase 9 ---------------------------------------------------------------------------

rollup: ## Aggregate yesterday and today into daily_rollups — the Lambda's job, run locally
	@# The same code path the scheduled Lambda takes; only the source of DATABASE_URL
	@# differs. Pass DAY=2026-08-11 to backfill one day, or DAYS=7 for a week.
	uv run python -m headroom.rollup $(if $(DAY),--day $(DAY),)$(if $(DAYS),--days $(DAYS),)

lambda-build: ## Assemble the rollup Lambda's deployment directory (Terraform zips it)
	uv run python deploy/aws/lambda/build.py

tf-check: ## terraform fmt + validate over both deploy roots — no AWS credentials needed
	@# `-backend=false` so this creates no local state and touches no account. It does
	@# need the registry, to fetch the providers the lock file pins.
	terraform fmt -check -recursive deploy/
	terraform -chdir=deploy/aws/data init -backend=false -input=false -no-color >/dev/null
	terraform -chdir=deploy/aws/data validate -no-color
	terraform -chdir=deploy/aws/compute init -backend=false -input=false -no-color >/dev/null
	terraform -chdir=deploy/aws/compute validate -no-color

chaos-smoke: ## Drive the P6 fault vocabulary at a running gateway (BASE_URL=… KEY=hk_…)
	@# The gate's "chaos test's keyless subset against the deployed stack", as a command.
	@# Costs $0.00: every fault is injected into the MockProvider over HTTP.
	uv run python scripts/chaos_smoke.py --base-url $(BASE_URL) --key $(KEY)

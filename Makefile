# Headroom — developer entry points. `make help` lists targets.
.DEFAULT_GOAL := help
.PHONY: help up down logs ps test lint typecheck fmt migrate

help: ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

up: ## Build and start the stack (db, dynamodb, gateway); waits for healthy
	docker compose up -d --build --wait
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

migrate: ## Apply migrations/*.sql in filename order (no-op until Phase 2)
	uv run python -m headroom.db.migrate

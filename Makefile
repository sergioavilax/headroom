# Headroom — developer entry points. `make help` lists targets.
.DEFAULT_GOAL := help
.PHONY: help up down logs ps test lint typecheck fmt migrate seed ui-check ui-e2e \
        rollup lambda-build tf-check chaos-smoke helm-check k8s-config load-loop

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

# --- Phase 10 --------------------------------------------------------------------------

helm-check: ## helm lint + template + kubeconform over the chart — no cluster, no credentials
	@# What CI runs, and the whole of what a machine can honestly say about a chart it
	@# will never install. The default render is deliberately cloud-free, so this needs
	@# no AWS account and no kubeconfig; the second render exercises the AWS shape with
	@# throwaway values, because the LoadBalancer branch is the one a default render
	@# never reaches and it is the one with a guard in it.
	helm lint deploy/k8s/headroom
	helm template headroom deploy/k8s/headroom \
	  | kubeconform -strict -summary -kubernetes-version $(KUBE_VERSION)
	helm template headroom deploy/k8s/headroom \
	  --set gateway.service.type=LoadBalancer \
	  --set gateway.service.loadBalancerSourceRanges={203.0.113.7/32} \
	  --set vllm.enabled=true --set vllm.targetIP=100.64.0.1 \
	  --set autoscaling.enabled=true \
	  | kubeconform -strict -summary -kubernetes-version $(KUBE_VERSION)

#: The version kubeconform validates against. Bumping it is a deliberate act: a schema
#: from a newer Kubernetes accepts fields this chart's `kubeVersion` floor does not
#: promise are there.
KUBE_VERSION ?= 1.31.0

k8s-config: ## Render deploy/k8s/{eksctl/cluster.yaml,values.aws.yaml} from the data layer's outputs
	@# Reads local Terraform state and makes no AWS call. HOME_CIDR is required for the
	@# values file (it is the only address the gateway's load balancer will admit) and
	@# optional for the eksctl config, which carries no machine-specific fact.
	uv run python deploy/k8s/render_config.py \
	  $(if $(HOME_CIDR),--home-cidr $(HOME_CIDR),) \
	  $(if $(VLLM_TARGET_IP),--vllm-target-ip $(VLLM_TARGET_IP),) \
	  $(if $(GATEWAY_TAG),--gateway-tag $(GATEWAY_TAG),) \
	  $(if $(UI_TAG),--ui-tag $(UI_TAG),)

load-loop: ## Measure dropped requests against a running gateway (BASE_URL=… KEY=hk_…)
	@# The instrument behind "a rolling helm upgrade with zero dropped requests". Costs
	@# $0.00: mock- models by default. Ctrl-C prints the summary; exit 1 means something
	@# was dropped. DURATION=600 STREAM=1 for the upgrade capture.
	uv run python scripts/load_loop.py --base-url $(BASE_URL) --key $(KEY) \
	  $(if $(DURATION),--duration-s $(DURATION),) $(if $(STREAM),--stream,) \
	  $(if $(MODEL),--model $(MODEL),) $(if $(DIALECT),--dialect $(DIALECT),) \
	  $(if $(OUT),--out $(OUT),)

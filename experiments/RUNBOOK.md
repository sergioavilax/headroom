# Phase 8 — the operator's runbook

Every command that spends money is here, in dependency order, with its expected cost stated
**before** it. Nothing in this file was run by Claude Code: BUILD_PLAN §0.2 invariant 2's
discipline — *the human runs the thing that costs* — applied to spend rather than to
infrastructure.

Read `experiments/PRE_REGISTRATION.md` first. It is the specification these commands
execute, it was committed before any of this data existed, and where the two disagree the
document is right.

## What is already done, and what is left

| | state | cost to finish |
|---|---|---|
| **H3** — failover under load | **complete and adjudicated.** No GPU session needed (H-067) | **$0.00** |
| **H1** — the safety curve | **the finding is already in** from the free half; the paraphrase family adds the savings side | **~$0.30** |
| **H2** — suite through the gateway | machinery ready, pre-flight ready, **not run** | **~$8.20** |

**H1's headline does not depend on the paid step.** τ₀ — the pre-registered recommended
threshold — is computed over Family A ∪ Family B, and Family B alone already carries
silent wrong answers at every point of the grid. Adding Family A can only add probes, so
"no safe threshold exists" is decided. What the $0.30 buys is the *other* axis: how many
legitimate hits a cache would give up, which is what turns a warning into a trade-off.

---

## 0. Prerequisites

```bash
cd ~/code/headroom
make up                                    # postgres + dynamodb + gateway + ui, migrated
export HEADROOM_ADMIN_TOKEN=…              # leading space, per invariant 3
```

Both stacks coexist by design (H-006): Backline holds 5432 / 8000 / 3000, Headroom holds
5433 / 8080 / 8001 / 3001. Nothing needs stopping.

> **Capture before the next `make test`.** The Postgres contract suite truncates the control
> plane and `usage_ledger` references it, so a run's rows go with it (H-029). Every analysis
> step below writes its artifact immediately for exactly this reason.

---

## 1. H1 — the paraphrase batch  ·  **~$0.30, hard stop $1.00**

### 1a. Project the cost first — free

```bash
uv run python -m experiments.h1.generate --dry-run
```

Prints the suite hash, how many of the 130 questions still need paraphrases, the model's
dated rate out of `config/models.yaml`, and the **worst case**. Expect the actual to land
near a third of it: the estimate assumes three bytes per token and the full `max_tokens` of
output, which is H-034's direction of error.

### 1b. Generate — **paid**

```bash
ANTHROPIC_API_KEY=… uv run python -m experiments.h1.generate
```

- The stop is **inside the harness**: before each call it prices that call's worst case and
  refuses to send if landed + worst case would cross `$1.00`. It reads *committed* spend,
  not landed — §0.2 rule 5 applied to this experiment's own money.
- **Resumable.** Completed questions are skipped, so a stop, a crash, or a closed laptop
  costs nothing already bought. Re-run the same command.
- A candidate that fails the mechanical checks is regenerated **here**, up to three rounds,
  before the artifact exists (risk register item 3). Anything still failing lands in
  `unresolved` and the build will refuse until it is fixed:
  ```bash
  uv run python -m experiments.h1.generate --only royalty_math-004,contract_terms-011
  ```

### 1c. The spot-check — **your gate, not the harness's**

`experiments/artifacts/h1_paraphrases.json` carries the batch. Twenty probes drawn by a
seeded RNG are named in the artifact's `spot_check.sample` once the corpus is built; read
them against the rubric in `experiments/h1/rubric.py` and ask one question of each:

> Does this ask for **exactly** the same thing — same entity, same period, same figure, same
> scope — in different words?

Record your approval by filling `spot_check.approved_by` and `approved_at` in
`experiments/artifacts/h1_corpus.json` after step 1d. `REPORT.md` states the corpus as
approved or as outstanding; it does not assume.

### 1d. Build the golden artifact — free

Needs Backline on disk (for its own scorer, H-061) and the `embed` extra:

```bash
BACKLINE_REPO=~/code/backline PYTHONPATH=~/code/backline \
  uv run --extra embed python -m experiments.h1.build
```

It refuses on an incomplete batch, re-checks every committed paraphrase against the checks
as they are committed *now*, and aborts if any rendered ground-truth answer fails Backline's
own scorer for its own question.

### 1e. Sweep, render, verify — free

```bash
uv run python -m experiments.h1.sweep
uv run python -m experiments.h1.figure
uv run pytest tests/test_experiments_h1.py -q
```

The last line matters: `test_the_committed_curve_is_the_one_this_corpus_produces` recomputes
the committed curve from the committed corpus, so a stale result file fails rather than
being published.

---

## 2. H2 — Backline's suite through the gateway  ·  **~$8.20, stops at $12**

### 2a. The tenant — free, and its configuration is the experiment

```bash
GW=http://localhost:8080
AUTH="Authorization: Bearer $HEADROOM_ADMIN_TOKEN"

TENANT=$(curl -sS -X POST $GW/admin/tenants -H "$AUTH" -H 'content-type: application/json' \
  -d '{"name":"h2-gateway-overhead"}' | jq -r .id)

# Unrestricted: Backline calls four models — planner and judge (sonnet), utility and
# router (haiku). A scope that missed one would fail mid-suite as a 403.
KEY=$(curl -sS -X POST $GW/admin/keys -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"backline-suite\"}" | jq -r .key)

# The backstop, deliberately ABOVE Backline's own $12 stop (H-066): it can only fire if
# Backline's accounting is wrong, which is when a second opinion is worth having.
curl -sS -X PUT $GW/admin/budgets/$TENANT -H "$AUTH" -H 'content-type: application/json' \
  -d '{"usd":"15.00","window":"monthly"}' | jq .

# Caching stays OFF — the shipped default, and H-047's requirement. Do not enable it.
curl -sS $GW/admin/cache/$TENANT -H "$AUTH" | jq '.mode'    # must print "disabled"
```

### 2b. Pre-flight — free

```bash
uv run python -m experiments.h2.preflight
```

Refuses on anything that would invalidate the run: caching on, a rate limit that could shed
a suite request, a budget cap low enough to 402 mid-run, an unhealthy provider, or a
failover chain on the `claude-` route.

### 2c. Pre-flight smoke — **~$0.02**, and risk register item 2 requires it

```bash
H2_VIRTUAL_KEY=$KEY uv run python -m experiments.h2.preflight --smoke
```

One real Anthropic call carrying a **tool block**, through the gateway. A5 is verified
keylessly and has never been verified against the real API through this path, and the $8 run
is a tool-heavy agent suite. If the reply has no `tool_use` block, stop — that is the
failure the smoke exists to find, for two cents.

### 2d. Point Backline at the gateway — **zero changes to Backline**

Assumption **A2**, verified: `anthropic` 0.120.2 reads `ANTHROPIC_BASE_URL` when `base_url`
is `None`, and Backline's `AnthropicProvider` passes `None`. So the whole integration is two
environment variables, and Backline's scoring is untouched.

```bash
cd ~/code/backline
make up                                     # its own stack, on its own ports

export ANTHROPIC_BASE_URL=http://localhost:8080   # <- Headroom
export ANTHROPIC_API_KEY=$KEY                     # <- the hk_… virtual key, NOT the real one
```

The real Anthropic key lives in Headroom's environment and nowhere else in this run. Confirm
before spending:

```bash
echo "$ANTHROPIC_API_KEY" | cut -c1-3          # must print: hk_
```

### 2e. The run — **~$8.09 expected, hard stop $12.00**

```bash
uv run python -m evals run --suite core --model claude-sonnet-5 --budget 12.00
```

**Why $12 and not §0.6's $10** (H-066): Backline's own pre-run projection for this suite is
**$11.27** — computed, not quoted — and its runner refuses to start when the projection
exceeds `--budget` unless `--yes` is passed. `--budget 10.00` therefore buys either a refusal
or a `--yes` that switches the projection guard off entirely, which is worse than moving the
number. The stop is $12; the *expectation* is $8.09; the gap is the §0.6 contingency bucket,
and PHASE_LOG records what was actually spent.

**Never pass `--yes` reflexively** (`AWS_DEPLOY_PLAN.md` §9). If the projection has moved
above $12, that is a fact worth reading before spending.

If infra errors appear (`errors.n > 0`), one heal pass is allowed and its use is reported:

```bash
uv run python -m evals run --suite core --model claude-sonnet-5 --budget 12.00 \
  --resume <run-id> --retry-errors
```

**One heal pass, then publish what happened.** Nothing is re-rolled for a friendlier draw.

### 2f. Export and adjudicate — free, and do it before the next `make test`

```bash
cd ~/code/headroom
docker compose exec -T db psql -U headroom -d headroom -At -c "
  SELECT json_agg(row_to_json(r) ORDER BY r.started_at) FROM (
    SELECT request_id, tenant_id, model, provider, streamed, outcome, status_code,
           upstream_status, error_source, error_reason, failover_hops, failover_from,
           failover_error, cache_disposition, cost_status, usd_cost::text, ttft_ms,
           total_ms, upstream_latency_ms, passthrough_overhead_ms, started_at
    FROM usage_ledger WHERE tenant_id = '$TENANT' ORDER BY started_at) r
" > docs/evidence/p8-experiments/h2-ledger-rows.json

uv run python -m experiments.h2.analyze \
  --rows docs/evidence/p8-experiments/h2-ledger-rows.json \
  --summary ~/code/backline/data/evals/<run-id>/summary.json
```

Read the first line of its output first. If `cache disabled (H-047)` is not `HOLDS`, the
overhead figure is a hit-rate figure and the run has to be repeated — that is what the count
is for.

Also on the record, per Backline §A5.5, with its known variance failure modes pre-declared:

```bash
cd ~/code/backline
uv run python -m evals report --summary data/evals/<run-id>/summary.json
uv run python -m evals gate   --summary data/evals/<run-id>/summary.json
```

### 2g. The keyless companion — free, and it is the honest overhead number

```bash
cd ~/code/headroom
uv run python -m experiments.h2.bench
```

Already run and committed; re-run it after any change to the admission path. It measures
`upstream_latency_ms` against the MockProvider, where the provider costs ~0 and what remains
is the gateway's own work (H-065).

---

## 3. H3 — nothing to run

Adjudicated in this PR from evidence that already exists (H-067):

- **the mock chain** — `experiments/results/h3_chaos.json`, three intensities, regenerate any
  time with `uv run python -m experiments.h3.chaos`, $0.00;
- **the two-GPU kill** — `experiments/results/h3_livekill.json`, from the 492 ledger rows the
  operator's 2026-08-10 run left behind, exported to
  `docs/evidence/p8-experiments/h3-livekill-ledger-rows.json` so the analysis survives the
  next `make test`.

If a future session wants a fresh recording, PHASE_LOG → Phase 7 → *The watched kill demo*
has the commands and `docs/evidence/p7-dashboard/README.md` has the capture list. Nothing in
Phase 8 needs one.

---

## 4. Money, totalled

| step | expected | hard stop | where the stop lives |
|---|---:|---:|---|
| H1 dry run | $0.00 | — | — |
| H1 generation | ~$0.30 | **$1.00** | inside `experiments/h1/generate.py` |
| H2 pre-flight smoke | ~$0.02 | — | one call |
| H2 suite run | ~$8.09 | **$12.00** | Backline's `--budget`, reading committed spend |
| H2 backstop | — | **$15.00** | Headroom's own budget gate on the H2 tenant |
| H3, all sweeps, all benches, the figure | $0.00 | — | — |
| **total** | **~$8.41** | **≤ $13** | against the **$20** project cap (§0.6) |

Spend across P0–P7 was $0.00 measured plus the P1/P3 live smokes (< $0.01), so the project
cap holds with room even if every stop above is reached.

# Headroom

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)

**An LLM gateway and control plane.** Headroom sits between applications and model
providers — Anthropic-dialect and OpenAI-dialect, cloud APIs and self-hosted vLLM —
and every request flows through it. Because everything flows through it, it does the
things every AI-native company needs and either builds badly or buys: virtual keys
and per-tenant budgets that actually enforce under concurrency, token-bucket rate
limits on atomic primitives, exact + semantic response caching, provider failover
with jittered backoff, and per-tenant/per-route/per-model cost attribution with a
live dashboard.

> *Headroom* (audio): the space between your peak level and clipping. A gateway
> whose whole job is keeping tenants under their limits.

**🚧 Under construction — see [BUILD_PLAN.md](BUILD_PLAN.md).** The plan is the
governing document: one phase per build session, one PR per phase, a human gate
closing each one, every judgment call logged in
[docs/DECISIONS.md](docs/DECISIONS.md). This README is a stub; the real one — with
the measured semantic-cache safety curve, the gateway-overhead number, and the
architecture diagram — is Phase 11.

Progress: **Phase 0** bootstrap · **Phase 1** proxy core · **Phase 2** tenancy ·
**Phase 3** metering · **Phase 4** the budget gate ← here.

What exists today: the **proxy core** (Phase 1) — `POST /v1/messages` and
`POST /v1/chat/completions`, streaming and non-streaming, passthrough per dialect —
**tenancy** (Phase 2): tenants, virtual keys, and an admin API — **metering**
(Phase 3): every request becomes a priced, attributed ledger row — and the
**budget gate** (Phase 4): per-tenant caps enforced by a single atomic DynamoDB
conditional write before a request can reach a provider.

```bash
make up      # postgres+pgvector, dynamodb-local, the gateway — waits healthy, migrates
make test    # keyless; `live` tests are excluded by default
make lint typecheck
curl localhost:8080/healthz    # {"status":"ok"}
```

Host ports: gateway `8080`, Postgres `5433`, DynamoDB Local `8001` — chosen to
coexist with anything already on 5432/8000, all overridable in `.env`.

## A working demo, with no key and no network

Every `/v1/*` request needs a virtual key, and keys are minted through the admin API,
which needs a root token. Set one first — in the gitignored `.env`, never in git:

```bash
echo "HEADROOM_ADMIN_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
make up
```

Then create a tenant, mint it a key, and use it. The `mock-` models are served by the
built-in MockProvider, so this costs nothing and talks to nobody:

```bash
export ADMIN="Authorization: Bearer $(grep HEADROOM_ADMIN_TOKEN .env | cut -d= -f2)"

curl -sS -X POST localhost:8080/admin/tenants -H "$ADMIN" \
     -H 'content-type: application/json' -d '{"name":"acme"}'
# {"id":"<tenant-id>", "name":"acme", "active":true, ...}

curl -sS -X POST localhost:8080/admin/keys -H "$ADMIN" \
     -H 'content-type: application/json' \
     -d '{"tenant_id":"<tenant-id>","name":"laptop","allowed_models":["mock-*"]}'
# {"id":"<key-id>", "key_prefix":"hk_XXXXXXXX", ..., "key":"hk_…"}   <- the ONLY time

curl -sS -X POST localhost:8080/v1/messages -H 'Authorization: Bearer hk_…' \
     -H 'content-type: application/json' \
     -d '{"model":"mock-model-1","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}'
```

**The plaintext key is returned exactly once, by `POST /admin/keys`, and cannot be
recovered afterwards.** Only its SHA-256 and an 11-character display prefix are stored;
lose it and mint a new one. Revocation is `DELETE /admin/keys/{id}` — the key stops
working on the very next request in that gateway process, and within **5 seconds**
(`AUTH_CACHE_TTL_S`) in any other. Nothing is ever deleted: keys are revoked and tenants
deactivated, because the cost ledger points at their ids forever.

`401` means the gateway does not know who you are (no key, malformed, unknown, revoked,
inactive tenant). `403` means it knows exactly who you are and the key is not scoped to
that model or provider. The reasoning for all of it is in
[docs/DECISIONS.md](docs/DECISIONS.md) H-017 … H-022.

## The cost ledger

Every request that authenticated and named a model becomes one row in `usage_ledger`,
priced at the rates that were in effect **on the day it arrived**:

```bash
curl -sS -H "$ADMIN" localhost:8080/admin/usage           # the ledger, newest first
curl -sS -H "$ADMIN" localhost:8080/admin/usage/totals    # spend per tenant
curl -sS -H "$ADMIN" 'localhost:8080/admin/usage/totals?by_model=true'
curl -sS -H "$ADMIN" localhost:8080/admin/usage/hr_…      # one request, by its id
```

Filters: `tenant_id`, `key_id`, `model`, `provider`, `outcome`, `since`, `until`,
`limit`, `offset`. The surface is read-only; every other verb is a 405.

Four things about it are deliberate, and each has an entry in
[docs/DECISIONS.md](docs/DECISIONS.md):

**Prices are a dated history, not a number** (H-023). `config/models.yaml` gives each
model a list of `(effective_from, usd_per_mtok_in, usd_per_mtok_out)` rows, and the
committed file already contains a real boundary: Anthropic published Claude Sonnet 5 at
an introductory $2/$10 per MTok through **2026-08-31**, $3/$15 after — so the identical
request costs different money on either side of that midnight. Rates are *quoted
strings*; an unquoted `3.00` is a YAML float and the loader refuses it by name.

**A row keeps the price it was billed at** (H-024). The rates are copied into the row,
so editing the price file — or a vendor publishing new rates — can never re-bill a
request that already happened. This is Backline's D-017 scar turned into a column
layout.

**The meter reads the usage block; it never counts the text** (H-025, and Phase 1's live
smoke). A reasoning model can answer in eleven visible characters and bill sixty-three
tokens, fifty-seven of them chain-of-thought that appears nowhere in the content stream.
`output_tokens` is reasoning-inclusive, always, from the provider's own report.

**`NULL` and `0` are different facts**, and `cost_status` says which — `priced`,
`partial`, `unpriced_model`, `usage_unknown`, `not_billable`. An upstream 429 provably
cost nothing (`0`). A stream cut mid-answer cost *something nobody can know* (`NULL`).
A meter that wrote `0.00` for both would look identical and be wrong.

Two consequences worth knowing before you read a total:

- **Anonymous 401s are not in the ledger.** They have no tenant to attribute, and the
  structured log line already records them. A tenant's row count is not their request
  count.
- **OpenAI-dialect *streamed* requests are only meterable if the caller asked for
  usage.** Send `"stream_options": {"include_usage": true}` and the counts arrive;
  without it the row is honestly `usage_unknown`, because Headroom will not rewrite a
  caller's request body to close the gap (H-028). `unpriced_requests` on every total
  says how many rows a sum could not include.

Money is `Decimal` from the YAML file through the arithmetic to `NUMERIC(24, 12)` and
back out **as a string** — JSON has one numeric type and it is a double.

## Budgets that hold under concurrency

A tenant is uncapped until you give it a cap. Then every request is admitted — or
refused — by **one atomic DynamoDB conditional write, before it can reach a provider**:

```bash
curl -sS -X PUT localhost:8080/admin/budgets/$TENANT -H "$ADMIN" \
     -H 'content-type: application/json' -d '{"usd": "25.00", "window": "monthly"}'

curl -sS -H "$ADMIN" localhost:8080/admin/budgets/$TENANT
# {"usd":"25.000000000000","spent":"0.000011500000","reserved":"0.000000000000",
#  "remaining":"24.999988500000","committed":"0.000011500000","window_id":"2026-08", …}
```

`window` is `monthly` (calendar month, UTC) or `total` (lifetime). `usd` is a **quoted
string** — a JSON number is a double, and the API refuses one by name.

**The design, in one sentence.** Admission reserves the request's *worst case* — its
`max_tokens` ceiling plus the size of the body it sent, at the model's dated price — and
the check and the deduction are the same operation:

```
ConditionExpression: remaining_picos >= :estimate
UpdateExpression:    SET remaining_picos = remaining_picos - :estimate, …
```

Completion settles the hold to the actual cost, handing the difference back. Nothing is
cached, ever: not the balance, not the cap, not whether a tenant has one.

That shape is Backline's **D-019** scar as a product feature. There, a gate checked
spend, then added spend, in two operations; under concurrency every request passed the
check before any had recorded anything, and the budget was blown.
`tests/test_budget_stampede.py` fires 64 concurrent requests at a cap sized for 5, on
DynamoDB Local, and asserts settled spend never exceeds it — then reruns the identical
stampede against two deliberately broken gates to prove the test can catch what it
claims to. The numbers are in [docs/PHASE_LOG.md](docs/PHASE_LOG.md).

Three more things worth knowing:

- **A refusal is `402`**, in the caller's own dialect (Anthropic `billing_error`, OpenAI
  `insufficient_quota`), with `headroom.reason: budget_exceeded` and the tenant's own
  figures in the message. Not `429` — that means *slow down*, every SDK retries it, and
  a budget refusal does not heal inside its window.
- **A crashed process cannot strand budget.** Holds expire after 15 minutes and are
  released — not charged — and the sweep runs on the *refusal* path, so a dead process's
  hold can never be the reason a live request is turned away.
- **The budget and the invoice deliberately disagree on one class of request** (H-031).
  When the cost is genuinely unknown — a timeout, a cut stream — the ledger writes
  `NULL` because it states facts, and the budget keeps the reservation because it states
  bounds. Both figures are on the same row.

Argued in [docs/DECISIONS.md](docs/DECISIONS.md) H-030 … H-034.

Running the local vLLM backends: [docs/vllm.md](docs/vllm.md).

MIT licensed.

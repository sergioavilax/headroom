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

## Rate limits that cannot be raced either

The same discipline, one primitive over. Limits are **requests per minute and tokens per
minute, per key and per tenant**, and a scope is unlimited until you say otherwise:

```bash
curl -sS -X PUT localhost:8080/admin/limits/tenant/$TENANT -H "$ADMIN" \
     -H 'content-type: application/json' -d '{"requests_per_min": 60, "tokens_per_min": 100000}'

curl -sS -H "$ADMIN" localhost:8080/admin/limits/tenant/$TENANT
# {"requests_per_min":60,"tokens_per_min":100000,
#  "buckets":[{"dimension":"requests","available":57,"reset_after_s":3, …}, …]}
```

`PUT` **replaces**: an omitted dimension is *unlimited*, not *unchanged*. `DELETE` clears
the limits and empties the buckets — the incident-response route.

**A token bucket cannot be stored as a count of tokens.** The obvious item — `tokens`
plus `refilled_at` — has to be read, refilled in application code, checked, and written
back, which is D-019 again in a different noun. So the bucket is stored as **a time**:
one number, the moment it will next be full. Admission becomes a bare comparison, and
therefore one conditional write:

```
ConditionExpression: tat <= :now + capacity_ns - :charge_ns
UpdateExpression:    SET tat = tat + :charge_ns
```

`tests/test_rate_limit_hammer.py` fires 64 concurrent requests at a bucket holding 5, on
DynamoDB Local, and asserts exactly 5 are served — then reruns it against **three**
broken limiters. The first over-admits and proves the hammer works. The other two *pass
the hammer* and fail elsewhere, which is the more useful lesson: a fixed-window counter
is perfectly atomic and admits twice the limit across every minute boundary, and a GCRA
without its clamp is perfectly atomic and admits an hour's worth of traffic after an hour
of quiet. **Atomicity is necessary and it is not sufficient.** The numbers are in
[docs/PHASE_LOG.md](docs/PHASE_LOG.md).

Three more things worth knowing:

- **A refusal is `429` with `retry-after`** — the case that status was invented for, and
  the exact opposite of the budget's `402`: a rate limit heals with time and the amount
  of time is known, so the honest thing is to say so.
- **Headroom's 429 is distinguishable from a provider's**, which Phase 6's failover logic
  will need: `x-headroom-error-source: gateway`, `x-headroom-ratelimit-scope`, and
  `headroom.reason: rate_limited`. The header is the load-bearing marker, because the
  whole `x-headroom-*` namespace is stripped from every upstream response — an upstream
  cannot claim to be the gateway.
- **Nothing settles.** A budget is a stock and a rate limit is a flow: an over-charge
  against a bucket is erased by the bucket's own refill, so there is no compensating
  release anywhere on the path — and therefore no operation whose absence breaks an
  invariant.

Argued in [docs/DECISIONS.md](docs/DECISIONS.md) H-035 … H-039.

## The cache that is allowed to say no

Two layers behind one interface. **Exact**: a canonical hash of the request. **Semantic**:
`bge-small-en-v1.5` on CPU, pgvector cosine search over the tenant's own namespace, hit
above a per-tenant threshold. Caching is **off for every tenant until somebody switches it
on** — the column default, not a convention:

```bash
curl -sS -X PUT localhost:8080/admin/cache/$TENANT -H "$ADMIN" \
     -H 'content-type: application/json' \
     -d '{"mode": "semantic", "similarity_threshold": 0.9, "ttl_s": 86400}'

curl -sS -H "$ADMIN" localhost:8080/admin/cache/$TENANT
# {"mode":"semantic","similarity_threshold":0.9,"effective_ttl_s":86400,
#  "embedding_model":"BAAI/bge-small-en-v1.5","entries":12,"semantic_entries":12, …}
```

A hit carries its own provenance, so an answer can always be traced to the question it was
actually produced for:

```
x-headroom-cache: cache_hit_semantic
x-headroom-cache-source: hr_9f2c…          <- the request that populated the entry
x-headroom-cache-similarity: 0.98012
x-headroom-cache-age: 41
```

**The interesting part of a cache is what it refuses.** Invariant 6 exists because
Backline's D-021 served content that did not belong to the question asked, and one bad
write here is served forever. So:

- **A truncated answer is never stored.** `stop_reason: max_tokens` / `finish_reason:
  length` is a *complete stream* of an *incomplete answer*, and it is the case that looks
  perfectly healthy from the outside — 200, well-formed body, `message_stop` present.
  Neither is a cut stream, a stream that simply stops, an upstream error, a timeout, or a
  response carrying a tool call.
- **A request with tools is never cached, even when no tool has been called.** The same
  words with tools available may legitimately produce a tool call instead of prose.
- **Single-turn, text-only, `temperature ≤ 0.2`, one completion.** Conservative on purpose:
  a false negative costs a cache miss, a false positive costs a wrong answer served with
  confidence, repeatedly.
- **A reasoning model's answer is cacheable *exactly* and never *semantically*** — replay
  hands the caller the original chain of thought, and reasoning performed on a different
  question's text has no business being served against this one.

`tests/test_cache_poison.py` drives every one of those through the real gateway and
asserts the cache is still empty.

**No entry ever crosses a tenant**, and the isolation is two mechanisms rather than one:
the tenant salts the exact key *and* leads every index and predicate.
`tests/test_cache_isolation.py` removes both — one patch, because both are downstream of
one function — and asserts the leak really happens, so the tests protecting the property
are known to be capable of failing.

**A hit is not an upstream call wearing a hat.** Its ledger row has a NULL
`upstream_status`, a NULL `provider`, no token counts, `usd_cost` of `0` with
`not_billable` beside it, and the saving in a column of its own (`cache_avoided_usd`)
where it cannot be mistaken for spend. Timings are honest: `ttft_ms` is real and small,
`passthrough_overhead_ms` is NULL because there was no upstream byte to measure from.

**Replay is byte-identical**, which is a stricter claim than the live path can make. The
transport is part of the key, so an entry is replayed rather than converted, and no code
exists that assembles a message from frames or synthesises frames from a message.

**The threshold is per tenant because [§P8.H1](BUILD_PLAN.md) is going to sweep it.**
The shipped default is **0.90**, and it was measured rather than picked: on the committed
corpus (`tests/fixtures/semantic_corpus.json` — 12 questions, 24 paraphrases, real
`bge-small` vectors), a paraphrase scores at worst **0.9237** against its own question and
at best **0.8511** against any other. 0.90 sits in that gap, closer to the top, because a
false hit and a false miss are not equally bad. That corpus is §P8.H1 in miniature: same
templates-crossed-with-entities shape, same provenance-as-answer-key, and
`tests/test_cache_semantic.py` already replays the admission decision across 0.70 → 0.99
offline — which is the mechanism that will make the headline experiment cost nothing
beyond generating the paraphrases.

Argued in [docs/DECISIONS.md](docs/DECISIONS.md) H-040 … H-047.

## Failover that refuses to serve a Frankenstein answer

A route may name same-dialect fallbacks, and a request that cannot be served by the
provider it was routed to is replayed against the next one — transparently, once, inside
the same admission.

```yaml
routes:
  openai:
    - prefix: ""
      provider: vllm_a
      fallbacks: [vllm_b]      # one 4090 each; kill one, the other serves
      max_attempts: 3          # optional, 1..5 — wraps: a, b, a
```

**Failover is opt-in per route.** A rule with no `fallbacks` and no `max_attempts` makes
exactly one attempt, with no retry, no backoff, and no circuit breaker anywhere on its
path — bit for bit what it did before this existed. That is why adding this changed
nothing for the `claude-` route that spends real money.

**The line that decides everything is the first byte out.** A fault *before* it — a
timeout, a connection failure, an upstream 429 or 5xx, even a non-streamed body that dies
mid-read — is replayed against the fallback and the caller never knows. A fault *after* it
is **never** retried, because splicing a second provider's answer onto the first one's
fragment produces a stream that is well formed, terminates cleanly, and that every SDK
returns as one complete message. There is nothing in it that says two models wrote it.
`tests/test_failover_boundary.py` runs that naive implementation and measures what it
serves — *"The capital of France is The capital of Germany is Berlin."*, two
`message_start` frames, one `message_stop`, no error anywhere — beside the shipped
gateway's answer to the identical fault, which is a terminal error event the caller's SDK
raises on.

**What never triggers a hop matters as much as what does.** Headroom's own 429 (rate
limit) and 402 (budget) are raised before the failover executor exists in the call path,
so no provider in the chain is called at all — failing over on your own rate limit moves
a burst instead of shedding it, and failing over on your own budget refusal spends the
money somewhere else. An upstream 4xx that is not 429 is forwarded rather than retried:
the next provider would say the same thing one round trip later.

**Backoff is paid to a provider that already failed, not to a fresh one.** Nothing about
`vllm_a` being down suggests `vllm_b` needs a moment, so moving down a chain costs no
latency at all; coming *back* to one that already failed this request pays full jitter over
50 ms, doubling, capped at 2 s — worst case 150 ms across three attempts, published by
`BackoffPolicy.worst_case_s` and asserted on a recorded clock rather than a real one.

**A circuit breaker takes a sick provider out of rotation and probes it back in.** Rolling
window of 20, trips at a 0.5 failure ratio once there are 5 samples, 10-second cooldown,
one probe at a time, and the window is cleared when the probe succeeds. It never skips the
*last* candidate in a chain: refusing the only remaining upstream would turn a provider's
outage into the gateway's own. Health lives in memory, per process, on purpose — a breaker
is a record of what *this* task can reach, not a fact about the world. `GET
/admin/providers` reports state, window, failure ratio, latency percentiles, and the
chains each provider sits in; `DELETE /admin/providers/{name}/health` closes a breaker
immediately, for the moment an operator has just fixed something.

**One request, one row, one reservation — however many providers it took.** The ledger row
names who served (`provider`), how many candidates were passed over (`failover_hops`), and
which one first and why (`failover_from`, `failover_error`); the log line carries the whole
trail as `["vllm_a:upstream_status_529", "vllm_b:ok"]`. A hop consumes no extra rate-limit
unit and takes no second budget hold, because admission happens above the executor and
settlement below it — asserted as arithmetic on the tenant's counters, not as a claim about
the code.

You can see all of it with no key, no network, and no GPU:

```bash
# the primary serves — no failover headers at all
curl -sS -D- -o /dev/null localhost:8080/v1/messages -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"model":"mock-model-1","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}'

# now break the primary for one request: the fallback answers, and the response says so
curl -sS -D- -o /dev/null localhost:8080/v1/messages -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' -H 'x-headroom-mock-script: fault-529@mock' \
  -d '{"model":"mock-model-1","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}'
#   x-headroom-failover-hops: 1
#   x-headroom-failover-from: mock
```

`fault-529` (any status), `fault-timeout`, `fault-connect`, and `fault-cut` are built into
the MockProvider so a *running* gateway can be broken on purpose; the `@name` suffix aims
one at a single instance.

Argued in [docs/DECISIONS.md](docs/DECISIONS.md) H-048 … H-053.

Running the local vLLM backends — including the two-instance topology this chain assumes:
[docs/vllm.md](docs/vllm.md).

MIT licensed.

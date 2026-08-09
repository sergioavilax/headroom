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

Progress: **Phase 0** bootstrap · **Phase 1** proxy core · **Phase 2** tenancy ← here.

What exists today: the **proxy core** (Phase 1) — `POST /v1/messages` and
`POST /v1/chat/completions`, streaming and non-streaming, passthrough per dialect — and
**tenancy** (Phase 2): tenants, virtual keys, and an admin API.

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

Running the local vLLM backends: [docs/vllm.md](docs/vllm.md).

MIT licensed.

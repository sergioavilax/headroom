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

Phase 0 (bootstrap) is what exists today:

```bash
make up      # postgres+pgvector, dynamodb-local, the gateway — waits for healthy
make test    # keyless; `live` tests are excluded by default
make lint typecheck
curl localhost:8080/healthz    # {"status":"ok"}
```

Host ports: gateway `8080`, Postgres `5433`, DynamoDB Local `8001` — chosen to
coexist with anything already on 5432/8000, all overridable in `.env`.

MIT licensed.

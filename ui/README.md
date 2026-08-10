# The console

Headroom's operator dashboard. Next.js, dark only, true black on cool zinc, mono numerals.

**It is a client of `/admin/*` and nothing else.** No database URL, no DynamoDB endpoint,
no client for either — every figure it renders is one the gateway's own admin API
published, which is what keeps the ledger's rules (NULL is not zero; a `partial` row is a
bound; a hit's token counts are absent rather than copied) decided in exactly one place.
A view that needs a number the API does not publish causes the API to publish it, with
tests, in the same change. See [`H-054`](../docs/DECISIONS.md).

## Running it

```bash
#  export HEADROOM_ADMIN_TOKEN=…      # leading space: keeps it out of shell history
make up                               # gateway on :8080, console on :3001
make seed                             # traffic worth looking at, through the public API
open http://localhost:3001            # sign in with the same token
```

The console holds **no secret in its environment** — not even by reference. The operator
types the root admin token into the sign-in screen; this server exchanges it for an
`httpOnly`, `SameSite=Strict` session cookie and attaches it to every `/admin/*` call from
then on. The token never crosses into client code (`lib/session.ts` and `lib/admin.ts`
import `server-only`, so doing so is a build error), and there is nothing in a bundle, a
task definition, or a `docker inspect` to leak. [`H-055`](../docs/DECISIONS.md).

If the gateway runs on the *host* rather than in compose — which the two-GPU kill demo
does, so `localhost:8010` and `:8011` mean the vLLM instances — point the console at it:

```bash
HEADROOM_GATEWAY_URL=http://host.docker.internal:8090 docker compose up -d ui
```

## Checks

No host Node is required; both targets run in a container built from this directory.

```bash
make ui-check      # eslint + tsc --noEmit + the unit tests
make ui-e2e        # the Playwright smoke, against a stub gateway
```

With a local Node 24 the same things are `npm run check` and `npm run e2e`. Unit tests run
on **Node's own test runner** with native TypeScript stripping, so the test layer costs
zero dependencies; the browser smoke runs against `tests/stub/gateway.mjs` — a Node stub of
the admin API with deliberately interesting fixtures — and against the **standalone**
server the shipped image runs, not `next start`. [`H-058`](../docs/DECISIONS.md).

## Views

| Route | What it answers |
|---|---|
| `/` | What has the gateway cost, what is it serving, what did the cache save, is anything unwell |
| `/live` | Requests arriving, **by the upstream that served each one** — the kill demo, on screen |
| `/requests` | The ledger as an explorer: filters, and one request's whole story in a drawer |
| `/tenants` | Tenants and virtual keys. Revoke and deactivate; nothing is ever deleted |
| `/limits` | Budgets as channel strips with headroom, rate limits as buckets |
| `/cache` | Dispositions, savings, policy — and which question each semantic hit answered |
| `/providers` | Health, breaker state, latency percentiles, and the chains each provider sits in |

## Layout

```
app/            one directory per view, plus api/ — the session and the admin proxy
components/     charts.tsx (every chart, inline SVG), ui.tsx, shell.tsx, signin.tsx
lib/            proxy rules · session · typed API client · polling · money · series colour
tests/unit/     node --test, zero dependencies
tests/e2e/      Playwright, against tests/stub/gateway.mjs
```

Three decisions shape the rest: it [polls rather than
streams](../docs/DECISIONS.md) (H-056), its [charts are hand-rolled
SVG](../docs/DECISIONS.md) (H-057) to a mark spec the design language depends on, and its
[tests need no database](../docs/DECISIONS.md) (H-058).

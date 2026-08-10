# P7 — the dashboard, and the kill demo watched through it

BUILD_PLAN §P7's gate, in its own words:

> **Gate:** dashboard renders a seeded compose environment truthfully (numbers
> cross-checked against psql); Playwright smoke; **the P6 kill demo re-run once
> *watching the dashboard* — that's the hero GIF.**

Two of those three close without hardware and are recorded in `docs/PHASE_LOG.md` as
verbatim output: the Playwright smoke runs in CI on every pull request against a stub of
the admin API, and the seeded-stack cross-check is `make seed` plus a handful of `psql`
queries. **This directory is for the third**, which needs two 4090s and a `docker kill`,
and therefore cannot be re-run by a stranger with a clone.

**Status: awaiting the operator's run.** Everything it depends on is in place: the console
ships in compose (`make up` brings it up on :3001), `make seed` fills it, and the P6 chain
is unchanged — the same `config/routing.yaml`, the same two instances on 8010 and 8011,
the same `docker kill`. What is new is that there is now something to point a camera at.

## What to capture

Drop the following into this directory as the demo is run. Filenames are suggestions; the
README below them is not optional — a capture with no provenance is a picture.

| File | What it is |
|---|---|
| `00-overview.png` | The Overview against the seeded stack: spend, requests, cache savings, provider tiles |
| `01-preflight.png` | **Live traffic**, before the kill — every bar in the primary's colour, both breakers `closed` |
| `02-kill.txt` | The `docker kill` and its timestamp, so the screenshots either side of it are bracketed |
| `03-shift.png` | **Live traffic**, during the kill — the stack changing colour mid-chart, `Failed over` climbing, `Caller-visible 5xx` still **0** |
| `04-breaker-open.png` | **Providers**, with `vllm_a` `open`, its cooldown counting down, and the chain shown beside it |
| `05-failover-rows.png` | **Providers → Recent failovers**, or **Requests** filtered to the hops: `passed over vllm_a · upstream_unavailable → served by vllm_b` |
| `06-request-detail.png` | One failed-over request's drawer — cost, budget hold, cache disposition, and the hop, on one screen |
| `07-recovered.png` | **Providers** after the instance is restarted: `closed`, one probe admitted, hops back to 0 |
| `08-psql-crosscheck.txt` | The gate's *truthfully* clause: the console's figures beside the same numbers straight out of `psql` |
| `hero.gif` | **The artifact.** The kill, from a steady stack to the shift to the recovery, in one loop |
| `console-tour/*.png` | One screenshot per view against the seeded stack (Overview, Live, Requests, Tenants & keys, Limits & budgets, Cache, Providers) |

The hero GIF is the one that matters. It is the only thing in this repo that shows the
whole system doing its job at once: a request arriving, a provider dying, traffic moving
to a second GPU, a breaker tripping and re-closing, and a caller who never saw an error.

## Running it

`docs/PHASE_LOG.md` → **Phase 7 → The watched kill demo** has the commands in order, with
what correct output looks like at each step. Read that; do not improvise the order,
because the kill takes down a container that spends minutes reloading a 27B checkpoint.

Two things worth knowing before starting, both of which have bitten a previous phase:

- **Capture before the next `make test`.** The Postgres half of the tenant-store contract
  suite truncates the control plane, and `usage_ledger` references it — so the rows behind
  every screenshot go with it (H-029's caveat, unchanged).
- **The gateway runs on the host for this demo**, so that `localhost:8010` and `:8011`
  mean the two vLLM instances. The console therefore needs
  `HEADROOM_GATEWAY_URL=http://host.docker.internal:8090` rather than the compose default;
  the runbook sets it.

Cost: **$0.00**. Both models are the operator's own, on the operator's own cards.

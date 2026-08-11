# Phase 9 — AWS: the capture list

**Provenance discipline, same as P6, P7, and P8.** Every file here is labelled with what
produced it, when, and against what. A screenshot with no provenance is a picture; a
screenshot with a request id, a timestamp, and the command that made it is evidence.

**Evidence lives in this repo and nowhere else** (BUILD_PLAN §0.2 invariant 9). No S3
bucket, and certainly not one inside a Terraform module — Backline's teardown ate one of
those, which is why the invariant is worded the way it is. Capture everything below
**before** `terraform destroy`; the console and the CloudWatch console are both gone the
moment compute is.

Commands are `deploy/aws/README.md`'s, by step number.

---

## The capture list

| File | What it shows | Where it comes from |
|---|---|---|
| `01-data-outputs.txt` | The data layer applied: RDS endpoint, both table names, both ECR URLs, the three secret ARNs | `terraform -chdir=deploy/aws/data output` (§2) |
| `02-cost-allocation-tags.png` | The four tag keys **Active** in Billing → Cost allocation tags, dated | §1, the console half |
| `03-images-pushed.txt` | Both `docker push` digests and sizes | §3 |
| `04-migrations.txt` | `applied 7 migration(s): …` from the one-off ECS task's log, including `0005_response_cache` creating `vector` on RDS | §7 |
| `05-live-request-headers.txt` | `HTTP/1.1 200`, the `x-headroom-request-id`, and **no `x-headroom-failover-*`** | §8b |
| `06-live-ledger-row.json` | That request id's row: tokens, cost, the rates it was billed at, `cache_disposition: cache_disabled`, `passthrough_overhead_ms` | §8b |
| `07-chaos-smoke.txt` | Nine `ok` lines and `{"checks": 9, "failed": 0}` against the ALB | §8c |
| `08-rollup-invoke.json` | The Lambda's own return value: two days, the newer one carrying the smoke's traffic | §8d |
| `09-rollup-api.json` | The same numbers back through `GET /admin/usage/rollups` | §8d |
| `10-console-history.png` | The **History** view: the day's bar, and the *Last rollup* tile with a fresh stamp | §8d, in a browser |
| `11-console-overview.png` | The Overview against the deployed stack — spend, requests, provider tiles | §8, in a browser |
| `12-alarms.json` | Four alarms, each with the SNS topic as its action | §8e |
| `13-alarm-fired.png` | `headroom-budget-refusals` in **ALARM** after a deliberate 402 | §8e |
| `14-compute-outputs.txt` | The compute layer's outputs, for the record | §9 |
| `15-destroy.txt` | `Destroy complete! Resources: N destroyed.` | §10 |
| `16-data-plan-after-destroy.txt` | **`No changes. Your infrastructure matches the configuration.`** | §10 |
| `17-empty-checks.txt` | Every per-service query from §11, with its output | §11 |
| `18-billing.png` | Cost Explorer filtered to `Project=headroom`, split by `Layer` | 24h after §1 |

---

## The three that carry the gate

**`05` + `06` — the live streamed request.** One real Anthropic call, through the ALB,
through the gateway, priced into the ledger. Two things make it worth the dollar: the
absence of a failover header (the primary served it, and a request the primary served has
no story to tell), and the row carrying `usd_per_mtok_in`/`usd_per_mtok_out` — H-024's
guarantee, on AWS, with a real invoice line behind it.

**`07` — the chaos subset.** Zero caller-visible 5xx for pre-first-token faults, and a
mid-stream cut arriving as a terminal error event with exactly one `message_start`. That
last one is the splice test from the outside: two `message_start` frames would mean two
providers wrote one answer, which is what H-048 exists to make impossible.

**`08` + `09` + `10` — the Lambda, end to end.** The function's own return value, the same
numbers through the API, and the console rendering them. Fire it twice before `10`: the
numbers must not move, because the rollup replaces a day rather than accumulating into it.

## And the one that is about money rather than about the product

**`16` — the data layer's plan after compute is destroyed.** `No changes.` is the whole
two-root design in one line: the EKS window starts from a database, two DynamoDB tables,
and two container images that a `terraform destroy` did not touch. Phase 10's estimate
depends on it being true, and this is where it is checked rather than assumed.

## Costs, recorded rather than remembered

`18-billing.png` closes A7's loop. §0.6 projects **$5–8** for this phase and
`deploy/aws/README.md` projects **$3–4** from list price; the actual is what goes in
`docs/PHASE_LOG.md`'s spend line, whichever way it lands. Cost Explorer needs up to 24
hours after tag activation, so this one arrives after everything else — which is why it is
last on the list rather than missing from it.

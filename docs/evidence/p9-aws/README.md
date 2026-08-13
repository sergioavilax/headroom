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
| `13-alarm-fired.png` | **`headroom-provider-down` in ALARM** — fired by the §8c faults, not staged. See below | §8e |
| `14-compute-outputs.txt` | The compute layer's outputs, for the record | §9 |
| `15-destroy.txt` | `Destroy complete! Resources: N destroyed.` | §10 |
| `16-data-plan-after-destroy.txt` | **`No changes. Your infrastructure matches the configuration.`** | §10 |
| `17-empty-checks.txt` | Every per-service query from §11, with its output | §11 |
| `18-billing.png` | Cost Explorer filtered to `Project=headroom`, split by `Layer` | **not captured** — see below |

---

---

## What the run produced — the list above, marked

The operator ran the whole runbook on **2026-08-10/11** (UTC stamps on every artifact;
local evening of the 10th). Sixteen of the eighteen items are in this directory. The two
that are not, and one substitution, are recorded here rather than left to be noticed.

**`13` is not the file this list asked for, and it is the better one.** The list asked for
`headroom-budget-refusals` in ALARM after a deliberately staged 402 — a tenant given a cap
of `$0.000001` and then made to hit it. What the run captured instead is
**`headroom-provider-down` in ALARM, which nobody staged**: the §8c chaos faults put three
provider failures through the gateway inside one five-minute window, the metric filter
counted them off the ordinary request log, and the alarm crossed its threshold on its own.

That is stronger evidence, on the axis the alarms are actually about. A staged 402 proves
the plumbing: an alarm wired to a metric wired to a filter, driven by an input written to
drive it. The provider-down alarm proves the **detection**: a fault injected for an
entirely different purpose — testing failover, three steps earlier — was noticed by an
alarm nobody was pointing at it, off a log line the gateway has emitted since Phase 1 with
no code added to be observed (H-079's whole claim). The staged version is a test of the
test. This one is the thing firing because the thing it watches for happened. **H-084.**

The two that remain open:

- **`02-cost-allocation-tags.png` — not captured, because there was nothing true to
  photograph.** Only `Project` activated; `Layer`, `Phase`, and `ManagedBy` each answered
  `ValidationException: tag key missing` for the whole session, and had not appeared in
  the Billing console by the end of it. Billing's tag-key discovery lags the resources by
  hours and can be next-day. A screenshot of three keys that are not there yet is not
  evidence of a lagged activation; it is a picture of an empty screen. It lands with `18`,
  and the honest state is written up in the runbook §1 and in **H-080 as amended**. What
  *is* verified — and is the half that cannot be retrofitted — is that the four keys are
  on the resources from their first second: `default_tags` on both providers, asserted
  keylessly by `test_every_resource_carries_the_cost_allocation_tags`.
- **`18-billing.png`** — **not captured, by decision on 2026-08-12, and the absence is the
  finding.** It was to be Cost Explorer filtered to `Project=headroom` and split by `Layer`.
  `Layer` activated at **16:54 UTC on 2026-08-11**, after this phase was over, and cost
  allocation tags label spend only from activation forward — so the capture would have been a
  `Project`-filtered view with nothing to split it by, which is a picture of the problem rather
  than evidence of the spend. Worse, Phase 9 and Phase 10 billed on the **same UTC day**, and
  the `Phase` tag that exists to tell them apart activated at the same 16:54: no filter in Cost
  Explorer separates this phase's money from the next one's. The rule this directory set for
  itself was that an absent capture whose absence *is* the finding gets said in those words, so:
  **it is the finding, and this is it.** The mechanism is **H-102**; the numbers that do exist
  are in `../p10-eks/23-billing.txt` and in the Phase 10 close.

Two smaller things a careful reader will notice in the captures, said out loud:

- **`12` shows all four alarms `OK` while `13` shows one in `ALARM`.** `13` is 20:05 local
  and `12` is 20:13: the provider-down alarm had already recovered and gone back to OK,
  which is `ok_actions` doing its job. Neither file is wrong; they are eight minutes apart.
- **`17-empty-checks.txt` has two cosmetic blemishes** — the ECS block's closing brace is
  missing from the capture, and the interface-endpoint query printed nothing rather than an
  empty table. The substance is unaffected and is the point of the file: no clusters, no
  services, no load balancers, no target groups, no functions, no rules, no log groups, no
  alarms, no topics — and exactly `headroom-db`, `headroom_buckets`/`headroom_budgets`, and
  `headroom/gateway`/`headroom/ui` still standing.

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

`18-billing.png` was to close A7's loop. §0.6 projects **$5–8** for this phase and
`deploy/aws/README.md` projects **$3–4** from list price. The run made the wait longer than
expected: three of the four keys had not been *offered* for activation by the end of the
session, so `18`'s clock started later than the apply did — and, as it turned out, later than
the phase itself.

**How it actually closed (2026-08-12): the projection is neither confirmed nor falsified, and
that is a fact about the tags rather than about the spend.** This phase and Phase 10 billed on
the same UTC day and no tag can separate them, so there is no Phase 9 figure to put beside the
$3–4. What is known is that the data layer — the projection's dominant line — billed **$0.3527**
across that whole day, consistent with the ≈$0.53/day projected and *not* a measurement of this
phase alone. A7 is closed in the Phase 10 entry with the numbers that do exist; the reason this
one does not is **H-102**.

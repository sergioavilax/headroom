# Phase 10 — Kubernetes: the capture list

**Provenance discipline, same as P6, P7, P8, and P9.** Every file here is labelled with
what produced it, when, and against what. A screenshot with no provenance is a picture; a
screenshot with a request id, a timestamp, and the command that made it is evidence.

**Evidence lives in this repo and nowhere else** (BUILD_PLAN §0.2 invariant 9). No S3
bucket, and certainly not one inside a Terraform module — Backline's teardown ate one of
those. Capture everything below **before** `helm uninstall`; a cluster's entire observable
surface disappears with it, and unlike Phase 9's stack this one cannot be re-applied from
a state file.

Commands are `deploy/k8s/README.md`'s, by section number.

---

## The capture list, marked

The list was written before the window. This column says what is actually in this
directory, and **the rows that read "not captured" say so in those words** — a capture
list that quietly renumbered itself around its own gaps would be a filing cabinet, not
evidence. The substitutions and the misses are argued below the table, not left to be
noticed.

| File | What it shows | §  | Status |
|---|---|---|---|
| `01-cluster-created.txt` | `eksctl create cluster` ending in `is ready`, with its two CloudFormation stacks | 4 | **not captured** — the cluster's existence is instead attested by everything that ran on it, and its *absence* by `20`/`22` |
| `02-nodes.txt` | Two nodes `Ready` with public IPs, the IRSA annotation, the Kubernetes version | 4 | **not captured** — the two nodes are named in `day1-pods.txt`'s `NODE` column (`ip-10-42-4-212`, `ip-10-42-22-43`) |
| `03-helm-install.txt` | `helm upgrade --install` completing, and the rendered kinds — with **no `kind: Secret`** | 6 | **not captured** — `11-helm-history.txt` carries the four revisions instead |
| `04-pods-svc.txt` | Two gateway pods on **different nodes**, the console, the egress pod, the LB hostname | 6, 12 | **substituted** → `day1-pods.txt` + `day1-svc.txt` (and `day2-pods-*.txt`) |
| `05-migrate-job.txt` | The pre-install hook's log: `up to date` against the schema Phase 9 put on RDS | 6 | **not captured** — the Job's *completion* is in `day1-pods.txt` (`headroom-migrate-hprps 0/1 Completed`) and in `10-rollout.txt`; the log line itself is gone with the cluster |
| `day1-live-headers.txt` | `HTTP/1.1 200`, `x-headroom-request-id: hr_a12bb390…`, and **no `x-headroom-failover-*`** | 7b | ✅ (listed as `06-`) |
| `day1-live-ledger-row.json` | That request id's row: tokens, cost, the rates billed at, `cache_disposition`, `passthrough_overhead_ms` | 7b | ✅ (listed as `07-`) |
| `day1-chaos.txt` | Nine `ok` lines and `{"checks": 9, "failed": 0}` against the cluster | 7c | ✅ (listed as `08-`) |
| `09-load-loop-run1-1drop.json` | The first measurement, `preStopSleepSeconds: 5` — **`dropped: 1`**, one per replaced pod, `max_gap_ms` 394 ms so no outage window | 8 | ✅ |
| `09b-load-loop-run2-sleep15-2drops.json` | The same with the sleep tripled to 15 s — **`dropped: 2`**, one per replaced pod again. The knob does not move the number, which is the diagnosis (H-091) | 8 | ✅ |
| `09c-load-loop-run3-drain.json` | The same measurement against the lame-duck drain: **`dropped: 0`**, `incidents: []` | 8 | ✅ |
| `10-rollout.txt` | The pod transitions: a third pod Ready before an old one is touched. *This is the namespace's event stream* — it is also what row `18` asked for | 8, 12 | ✅ |
| `11-helm-history.txt` | Four revisions, the second onward carrying new image tags | 8 | ✅ |
| `12-console-overview.png` | The Overview, served by a pod in the cluster | 9 | **not captured** — see `24`/`25`, which are the console from the cluster on a harder view |
| `13-console-requests.png` | The Requests view: the live Anthropic row beside the mock traffic | 9 | **not captured** — same |
| `14-tailnet-path.txt` | The egress pod's log, and a curl from *inside the cluster* reaching a 4090 on the operator's desk | 11 | ✅ |
| `15-failover-loop.json` | The kill demo measured rather than watched: **92/92, `dropped: 0`** on the vLLM chain across a `docker kill` | 11 | ✅ |
| `15a-failover-loop-run1-timeout15.json` | The same run at the mock-tuned default timeout: **14 legitimate 12–16 s completions scored `dropped`**. The instrument lesson (H-092), kept | 11 | ✅ |
| `16-failover-ledger.json` | 40 rows, all `200`: 22 on `vllm_a` at `failover_hops: 0`, 18 flipped to `vllm_b` at `hops: 1, from: vllm_a` — 10 `breaker_open`, 8 `upstream_unavailable` | 11 | ✅ |
| `17-provider-health.json` | `/admin/providers`: `vllm_a` with 25 total failures and `last_error: upstream_unavailable`, re-admitted (`closed`) after the restart | 11 | ✅ |
| `18-events.txt` | The namespace's events across the whole window | 12 | **not captured under this name** — `10-rollout.txt` *is* the event stream |
| `19-uninstall.txt` | `helm uninstall`, and the load-balancer list going empty **before** the cluster is deleted | 13 | **not captured** — the property it exists to prove is in `20` (both `elbv2` and `elb` lists empty, with the VPC still standing) |
| `20-empty-checks.txt` | Every per-service query from §15, with its output | 15 | ✅ |
| `21-data-destroy.txt` | `No changes.` on the data layer, then `Destroy complete! Resources: 26 destroyed.` | 16 | ✅ |
| `22-final-empty-checks.txt` | RDS, snapshots, DynamoDB, ECR, secrets, VPC — all empty | 16 | ✅ |
| `23-billing.png` | Cost Explorer, `Project=headroom`, grouped by service — **Total costs $3.07**, the tag-attributable half of a $3.5556 bill | 17 | ✅ |
| `23-billing.txt` | The CLI behind it: `Layer`-grouped and `Project`-filtered, then `SERVICE`-grouped unfiltered, daily across 2026-08-09 → 12 | 17 | ✅ (added — the console view alone cannot show the gap) |
| `24-live-flip.png` | **Live traffic** from the cluster's own console: 104 requests, **66 failed over, 0 caller-visible 5xx**, the stack's colour moving `vllm_a` → `vllm_b`, every flipped row naming the hop that happened | 9, 11 | ✅ |
| `25-breaker-open.png` | The same view mid-kill: **`vllm_a` `open`**, `4/5` breakers closed, `failures 10/20`, `probes in 0s`, `last error: upstream_timeout` | 9, 11 | ✅ |
| `day2-pods-overnight.txt` | The unattended run: every pod `AGE 10h`, **`RESTARTS 0`** | — | ✅ (added by the window) |
| `day2-pods-final.txt` | After run 3's rollout: two fresh gateway pods, the console and vLLM egress still at 13 h / 12 h | — | ✅ (added by the window) |

**On the numbering.** `24` and `25` were captured as `18-` and `19-`, which the runbook
had already reserved for the event stream and the uninstall check. They were renumbered
out of that range rather than quietly taking the numbers, so that a reader following
`deploy/k8s/README.md` §12 and §13 to a file does not find a screenshot of something else.
`23` was held for the billing capture, which arrived on 2026-08-12 and took the number.

### And the two this phase inherits

Both belong to `docs/evidence/p9-aws/` and were left open at the Phase 9 close, blocked on
Billing's tag-key discovery. **H-080 as amended** puts the retry at the start of this
phase's first session, which is `deploy/k8s/README.md` §1:

| File | What it shows | Status |
|---|---|---|
| `../p9-aws/02-cost-allocation-tags.png` | The four tag keys **Active** in Billing → Cost allocation tags, dated | **not in the repo.** §1 was run; no capture landed |
| `../p9-aws/18-billing.png` | Cost Explorer filtered to `Project=headroom`, split by `Layer` — Phase 9's own spend, finally attributable | **not captured.** Phase 9's spend predates the activation of `Layer`, so the capture would show the same un-attributable picture as `23`: no split to make. The absence *is* the finding — **H-102** |

A screenshot of three keys that have not appeared yet is a picture of an empty screen, not
evidence of a lagged activation. If they still have not surfaced by §17, that is itself the
finding and it goes in the phase log in those words rather than being quietly dropped —
and that is exactly where it now is.

---

## The five things this set has to prove

§P10's evidence window names five, and they are not equally easy to fake — which is why
each has a file that could have said otherwise.

**1. The chart deploys the same gateway.** `day1-live-headers.txt` +
`day1-live-ledger-row.json`, with `day1-pods.txt` showing the migration Job `Completed`
before either. `hr_a12bb3905a424a1e8ec6c06cf73bc7aa` is a real streamed request to
`claude-haiku-4-5` through a Network Load Balancer, billed at
`usd_per_mtok_in: 1.00 / out: 5.00` from `price_effective_from: 2026-08-08` for
`usd_cost: 0.000050000000` on 15 in / 7 out — the same ledger row shape Phase 9 captured
on ECS, through a third kind of load balancer, with the rates it was billed at copied onto
it (H-024). `passthrough_overhead_ms: 0.0175` against `upstream_latency_ms: 1006.18`.

The one thing this pair does *not* carry is the migration hook's `up to date` log line,
which `05` was for. What survives is that the Job reached `Completed` and that the request
above then wrote a ledger row to the Phase 9 database — which is the same claim by a
longer route.

**2. A rolling upgrade drops nothing — eventually, and the road there is the evidence.**
`09`, `09b`, `09c`, kept in that order on purpose. The classifier is what makes any of them
worth reading: `scripts/load_loop.py` scores a request `shed` only on positive evidence that
the gateway meant it — a 402 or a 429 carrying `x-headroom-error-source: gateway` (H-032,
H-038) — and everything else falls to `dropped`, including a connection with no status line
at all and a 200 whose stream never reached `message_stop`. A zero from an instrument whose
unknown case was "probably fine" would not be worth printing. `max_gap_ms` is beside it
because a rollout that dropped nothing and was unreachable for nine seconds has an error
count of zero and is still an outage.

The first two runs are here because the instrument worked. It found one dropped request per
replaced pod — a client writing onto a keep-alive connection at the instant uvicorn closed
it, which the preStop sleep cannot reach and tripling the sleep proved it cannot reach. `09c`
is the same measurement against the fix that finding produced: **8342 requests, 8342 ok,
`incidents: []`**, on a rollout where both the pods going away and the pods arriving were
draining pods. **A set that contained only `09c` would be a set nobody could check**, and the
two runs that read non-zero are the reason the third one means something.

The residual H-091 names — a connection idle for the whole drain window and first reused in
the milliseconds after SIGTERM — was not observed in run 3. It is not thereby ruled out; one
600-second run at four in flight is not a proof of absence, and the decision record still
says so.

**3. The dashboard is served from the cluster.** `24` + `25`, with `day1-pods.txt` naming
the node each pod is on. The address bar says `localhost` because the console is a ClusterIP
service reached by `kubectl port-forward` — deliberately, because a second load balancer
costs $0.54/day to put a hostname in a screenshot, and RBAC is a better door than an
IP allow-list in front of a cleartext listener. The pods are in the cluster either way and
`day1-pods.txt` says so.

These are **not** the two views the list asked for, and they are the harder pair: Live
traffic during the kill, rather than Overview and Requests at rest. See below.

**4. Failover works from `us-east-1` to a desk.** `14` + `15` + `15a` + `16` + `17`, and
`24`/`25` are the same event seen from the console. `14` is the part that is new — a pod in
AWS opening a TCP connection to a container at home over a tailnet, answered by the 4090's
own `/v1/models` listing `cyankiwi/Qwen3.6-27B-AWQ-INT4`, with nothing advertised into the
cluster and no change to `headroom/`. `15` makes the P6/P7 demo a *measurement*: the same
three outcomes as `09`, on the vLLM chain, across a `docker kill` — **92 requests, 92 ok**.
`16` and `17` are the ledger and the breaker saying the same thing from inside.

**5. The cluster is provably gone.** `20` + `21` + `22`, in that order and for that reason.
`19` was the file that would have shown the load balancer going before the cluster did; what
`20` shows instead is the *outcome* that ordering exists to produce — `elbv2` and `elb` both
empty, no target groups, no instances, no auto-scaling group, **no `available` EBS volume**,
no `eksctl-headroom` role, no OIDC provider — while the VPC, the two data-layer security
groups and the RDS instance are all still standing, which is what says the cluster left
without taking the data layer with it. The three Lambda ENIs in `available` state are the
Phase 9 rollup function's and are named as expected in §15. `21`'s `No changes.` is the
other half: the cluster came and went without touching the network the data layer owns.

## And the one that is about money rather than the product

**`23` closes A7, and it landed on 2026-08-12.** §0.4 pre-registered *"EKS + Helm on 2 small
nodes for 3 days lands ≈ $20–25"*; `deploy/k8s/README.md`'s table projects **$17–19** from
list price *for three days*. The window was **not** three days (see below), so the
comparison A7 asked for is against a shorter denominator and the phase log says which.

The actual: **$3.5556** for the window, all of it on one UTC day, at a rate of **≈$6.10/day**
against the **$5.58/day** estimated. The window total is small **because the window was
fourteen hours** — compression, not efficiency, and the phase log and README both say so in
those words.

**The two numbers this directory carries are different on purpose.** `23-billing.png` reads
**$3.07** — that is everything the `Project=headroom` tag can see. `23-billing.txt`'s
service-grouped read, less the account's pre-existing S3 baseline, is **$3.5556** — that is
everything Headroom actually cost. The **$0.4850** between them is spend this project made
that no tag can find, and **72.4%** of what *is* tagged has no `Layer` value at all, on a
window where every resource was configured to carry one. **H-102** is that finding, and it is
why the CLI output is committed beside the screenshot rather than the screenshot alone.

`../p9-aws/18-billing.png` was **not captured**, for the same reason: Phase 9's spend predates
the `Layer` activation, so there is no split for it to show.

---

## What the run produced

The operator ran the whole runbook on **2026-08-10/11**. Twenty-two files are in this
directory. The misses, the substitutions and the one thing that is not a miss at all are
recorded here rather than left to be noticed.

**The window was compressed, deliberately, and it is a deviation.** §P10 says *"Evidence
window: three days"*. The cluster was created at ≈23:00 on 2026-08-10 (`11-helm-history.txt`
revision 1: `Mon Aug 10 23:29:40 2026`) and deleted at ≈13:00 on 2026-08-11
(`20-empty-checks.txt`, then `21`) — **about fourteen hours of cluster uptime**, inside a
calendar window of a bit over a day. The reasoning is in `docs/PHASE_LOG.md` and in H-096:
three days was a **cost-and-scope ceiling, not an evidence requirement**, and every claim on
the list that could be captured was captured inside it. The one thing three days was
genuinely for — *does this survive being left alone* — is `day2-pods-overnight.txt`:
`AGE 10h`, `RESTARTS 0`, every pod, unattended overnight.

**`24`/`25` are not the console captures the list asked for, and they are the better pair.**
The list asked for Overview and Requests at rest (`12`, `13`). What the window produced is
**Live traffic during the kill** — a view that is only worth capturing while something is
going wrong, and there was a fifteen-minute window in which something was. `25` catches
`vllm_a` **`open`** with `failures 10/20` and `probes in 0s`; `24` catches the arc completed:
104 requests, **66 served by a fallback, `CALLER-VISIBLE 5XX: 0`**, and a recent-requests
table where every flipped row names *why* — `← vllm_a · breaker_open` or
`← vllm_a · upstream_unavailable`. That is the console proving §P10's fourth claim and its
third one at the same time, from a pod on a node named in `day1-pods.txt`.

Overview and Requests at rest are, by comparison, two screenshots of numbers that `16` and
`17` already carry as JSON. The trade is stated rather than hidden: **this set has no
capture of the Overview view on EKS at all**, and if the claim being made were "every
console view works against a cluster", these two would not establish it.

**Five rows read "not captured", and four of them are the same kind of loss.** `01`, `02`,
`03` and `05` are all *console output from the install*, and all four were lost the same
way: a working shell whose scrollback was not redirected to a file at the time, in a
window that then compressed. Each is partly recoverable from what did land — the nodes from
`day1-pods.txt`'s `NODE` column, the revisions from `11-helm-history.txt`, the Job from its
`Completed` line — and none is recoverable in full, because the cluster is gone. `19` is
the fifth and is different: the *property* survived into `20`.

The honest summary is that the install itself is the thinnest-evidenced step of this phase.
Nothing downstream of it is: everything from §7 onward has a file.

**`15a` and `09`/`09b` are here on purpose, and they are the point.** Two of the four
measurement files in this set read *worse* than the claim. `15a` scored fourteen legitimate
27B completions as `dropped` because the loop inherited a mock-tuned 15-second timeout
(H-092); `09` and `09b` found a real drop per replaced pod that a preStop sleep could not
reach (H-091). Both were kept, both produced a fix, and both are why the zeros beside them
are worth reading.

**One measurement is not in this directory, and it is the third of that kind.** An earlier
attempt at §8 read **8122 requests, 8122 dropped**, every one of them
`Illegal header value b'Bearer '` — a fresh terminal in which `$MOCK_KEY` had never been
exported. Nothing reached the cluster; the run measured the client. It is recorded rather
than filed because a JSON file that reads like a total outage and is not one is precisely
the artifact this discipline exists to prevent, and `scripts/load_loop.py` now refuses to
start on an empty key (H-095).

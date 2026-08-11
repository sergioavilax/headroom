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

## The capture list

| File | What it shows | Where it comes from |
|---|---|---|
| `01-cluster-created.txt` | `eksctl create cluster` ending in `is ready`, with the two CloudFormation stacks it built | §4 |
| `02-nodes.txt` | Two nodes `Ready` with public IPs, the IRSA annotation on `headroom-gateway`, and the Kubernetes version this ran on | §4 |
| `03-helm-install.txt` | `helm upgrade --install` completing, and the rendered kinds — with **no `kind: Secret`** among them | §6 |
| `04-pods-svc.txt` | `kubectl get pods,svc,deploy,hpa,pdb -o wide`: two gateway pods on **different nodes**, the console, the tailscale egress pod, and the load balancer's hostname | §6, §12 |
| `05-migrate-job.txt` | The pre-install hook's log: `up to date` against the schema Phase 9 put on RDS | §6 |
| `06-live-request-headers.txt` | `HTTP/1.1 200`, the `x-headroom-request-id`, and **no `x-headroom-failover-*`** | §7b |
| `07-live-ledger-row.json` | That request id's row: tokens, cost, the rates it was billed at, `cache_disposition`, `passthrough_overhead_ms` | §7b |
| `08-chaos-smoke.txt` | Nine `ok` lines and `{"checks": 9, "failed": 0}` against the cluster | §7c |
| `09-load-loop-run1-1drop.json` | The first measurement, `preStopSleepSeconds: 5` — **`dropped: 1`**, one per replaced pod, `max_gap_ms` in the low hundreds so no outage window | §8 |
| `09b-load-loop-run2-sleep15-2drops.json` | The same with the sleep tripled to 15 s — **`dropped: 2`**, one per replaced pod again. The knob does not move the number, which is the diagnosis (H-091) | §8 |
| `09c-load-loop-run3-drain.json` | The same measurement against the lame-duck drain: **`dropped: 0`** with an empty `incidents` list, or the residual H-091 refuses to rule out | §8 |
| `10-rollout.txt` | `kubectl rollout status` and the pod transitions: a third pod Ready before an old one is touched | §8 |
| `11-helm-history.txt` | Two revisions, the second carrying the new image tag | §8 |
| `12-console-overview.png` | The Overview, served by a pod in the cluster | §9 |
| `13-console-requests.png` | The Requests view: the live Anthropic row beside the mock traffic | §9 |
| `14-tailnet-path.txt` | The tailscale egress pod's log, and a curl from *inside the cluster* reaching a 4090 on the operator's desk | §11 |
| `15-failover-loop.json` | The kill demo measured rather than watched: `dropped: 0` on the vLLM chain across a `docker kill` | §11 |
| `16-failover-ledger.json` | Ledger rows flipping to `provider: vllm_b`, `failover_hops: 1`, `failover_from: vllm_a` | §11 |
| `17-provider-health.json` | `/admin/providers` with `vllm_a` `open` and its cooldown, then re-admitted after the restart | §11 |
| `18-events.txt` | The namespace's events across the whole window | §12 |
| `19-uninstall.txt` | `helm uninstall`, and the load-balancer list going empty **before** the cluster is deleted | §13 |
| `20-empty-checks.txt` | Every per-service query from §15, with its output | §15 |
| `21-data-destroy.txt` | `No changes.` on the data layer, then `Destroy complete` | §16 |
| `22-final-empty-checks.txt` | RDS, snapshots, DynamoDB, ECR, secrets, VPC — all empty | §16 |
| `23-billing.png` | Cost Explorer, `Project=headroom`, grouped by `Layer`, across the window | §17 |

### And the two this phase inherits

Both belong to `docs/evidence/p9-aws/` and were left open at the Phase 9 close, blocked on
Billing's tag-key discovery. **H-080 as amended** puts the retry at the start of this
phase's first session, which is `deploy/k8s/README.md` §1:

| File | What it shows | Where it comes from |
|---|---|---|
| `../p9-aws/02-cost-allocation-tags.png` | The four tag keys **Active** in Billing → Cost allocation tags, dated | §1, once they are all Active |
| `../p9-aws/18-billing.png` | Cost Explorer filtered to `Project=headroom`, split by `Layer` — Phase 9's own spend, finally attributable | §17, beside this phase's `23` |

A screenshot of three keys that have not appeared yet is a picture of an empty screen, not
evidence of a lagged activation. If they still have not surfaced by §17, that is itself the
finding and it goes in the phase log in those words rather than being quietly dropped.

---

## The five things this set has to prove

§P10's evidence window names five, and they are not equally easy to fake — which is why
each has a file that could have said otherwise.

**1. The chart deploys the same gateway.** `05` + `06` + `07`. The migration hook reading
`up to date` is the load-bearing line in `05`: it means the chart is pointed at the RDS
instance Phase 9 migrated, and that the runner in the image is the one this repo ships.
`07` is the same ledger row shape Phase 9 captured on ECS, through a third kind of load
balancer, with the rates it was billed at copied onto it (H-024).

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
is the same measurement against the fix that finding produced. **A set that contained only
`09c` would be a set nobody could check**, and the two runs that read non-zero are the reason
the third one means something.

**3. The dashboard is served from the cluster.** `12` + `13`, with `04` naming the node
each pod is on. The address bar says `localhost` because the console is a ClusterIP service
reached by `kubectl port-forward` — deliberately, because a second load balancer costs
$0.54/day to put a hostname in a screenshot, and RBAC is a better door than an IP allow-list
in front of a cleartext listener. The pods are in the cluster either way and `04` says so.

**4. Failover works from `us-east-1` to a desk.** `14` + `15` + `16` + `17`. `14` is the
part that is new — a pod in AWS opening a TCP connection to a container at home over a
tailnet, with nothing advertised into the cluster and no change to `headroom/`. `15` makes
the P6/P7 demo a *measurement*: the same three outcomes as `09`, on the vLLM chain, across
a `docker kill`. `16` and `17` are the ledger and the breaker saying the same thing from
inside.

**5. The cluster is provably gone.** `19` + `20` + `21` + `22`, in that order and for that
reason. `19` exists because deleting an EKS cluster while a `Service` of type LoadBalancer
still exists orphans the load balancer — it survives, it bills, and nothing is left that
knows it exists. `21`'s `No changes.` is the other half: the cluster came and went without
touching the network the data layer owns, which is what a per-service check cannot say.

## And the one that is about money rather than the product

**`23` closes A7.** §0.4 pre-registered *"EKS + Helm on 2 small nodes for 3 days lands
≈ $20–25"*; `deploy/k8s/README.md`'s table projects **$17–19** from list price. Both
outcomes are publishable and the table in `docs/PHASE_LOG.md` gets the actual, whichever
way it lands. What is not acceptable is an estimate with no actual beside it — which is
what the Phase 9 spend line has been carrying since its close, and which `../p9-aws/18-billing.png`
finally settles.

---

## What the run produced

*Filled in after the operator's window, in the same shape as `docs/evidence/p9-aws/README.md`'s
own "What the run produced" section: every item marked, every substitution argued in the
open, and every number re-derived here rather than filed. A capture list nobody recomputes
is a filing cabinet.*

*Not yet run. `docs/PHASE_LOG.md` records the state of this list at the end of the phase.*

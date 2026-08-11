# Evidence

BUILD_PLAN §0.2 invariant 9: **evidence lives in the repo, outside every blast radius.**
No "evidence bucket" inside a Terraform module — the Backline teardown ate one. Screenshots,
terminal captures, curves, and reports commit here and to `experiments/results/`, where a
`terraform destroy` cannot reach them.

One directory per artefact, named for the phase that produced it, each with its own README
saying what was run, when, on what, and what the output means. A screenshot with no
provenance is a picture.

| Directory | Phase | What it holds |
|---|---|---|
| [`p6-failover/`](p6-failover/) | 6 | The two-GPU kill demo: one vLLM per 4090, one killed mid-flight, the ledger rows that recorded it |
| [`p7-dashboard/`](p7-dashboard/) | 7 | The same kill, **watched** — the console rendering the shift from one GPU to the other, and a screenshot of every view against a seeded stack |
| [`p8-experiments/`](p8-experiments/) | 8 | The artifacts H2's parity claim rests on — Backline's own summaries and its gate baseline, committed here so the claim does not depend on a sibling checkout — and the H3 kill run's ledger rows |
| [`p9-aws/`](p9-aws/) | 9 | The ECS/RDS/DynamoDB/Lambda stack, applied and destroyed the same day: a live streamed request through the ALB, the chaos subset, the rollup fired by hand, an alarm firing unprompted, and the per-service empty checks |
| [`p10-eks/`](p10-eks/) | 10 | The three-day EKS window: a rolling `helm upgrade` measured to zero dropped requests, the dashboard served from the cluster, the two-vLLM failover reached over tailscale, and the teardown |

Keyless evidence — anything reproducible from `make up` and the test suite — belongs in
`docs/PHASE_LOG.md` as verbatim output instead. This directory is for the things that
needed hardware, money, or a cloud account, and therefore cannot be re-run by a stranger
with a clone.

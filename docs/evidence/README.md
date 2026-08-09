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

Keyless evidence — anything reproducible from `make up` and the test suite — belongs in
`docs/PHASE_LOG.md` as verbatim output instead. This directory is for the things that
needed hardware, money, or a cloud account, and therefore cannot be re-run by a stranger
with a clone.

# P6 — the two-GPU kill demo

BUILD_PLAN §P6's second proof, and the rehearsal for §P8.H3's on-camera chaos run:

> **The live demo (evidence, not a scored claim):** OpenAI-dialect chain across the
> operator's two vLLM instances — one per 4090 — send a sustained stream of requests,
> `kill` one vLLM mid-run, watch the dashboard show traffic shift to the second GPU with
> zero failed requests, screenshot it, bring the instance back, watch the breaker re-admit
> it. Total API cost: $0.00.

**Status: awaiting the operator's run.** Everything it depends on is in place and verified
— the chain ships in `config/routing.yaml`, both instances were pre-flighted on 2026-08-09
(assumption **A6**), and the GPU pinning that makes "one per card" true rather than
aspirational is now VERIFIED in [`../../vllm.md`](../../vllm.md). The keyless half of the
same behaviour runs in CI on every pull request
(`tests/test_failover_chaos.py`, `tests/test_failover_boundary.py`).

The dashboard half arrives in Phase 7; §P7's gate re-runs this demo *watching* it, and that
is the hero GIF. This run is the ledger-and-log version of the same evidence.

## What to capture

Drop the following into this directory as the demo is run. Filenames are suggestions; the
README below them is not optional — a capture with no provenance is a picture.

| File | What it is |
|---|---|
| `01-preflight.txt` | `nvidia-smi` + `/v1/models` on both ports, before anything is killed |
| `02-served-by-primary.txt` | a request served normally, with no failover headers |
| `03-kill.txt` | the `docker kill` and its timestamp |
| `04-failed-over.txt` | the next request: 200, `x-headroom-failover-hops: 1`, `-from: vllm_a` |
| `05-ledger.txt` | the `usage_ledger` rows, showing `failover_hops` and `failover_from` |
| `06-log-line.json` | one structured request log line naming **both** providers |
| `07-breaker.json` | `GET /admin/providers` with `vllm_a` open and its cooldown counting down |
| `08-recovered.txt` | the instance restarted, the probe admitted, `failover_hops` back to 0 |

## Running it

The exact commands, in order, with what correct output looks like at each step, are in
`docs/PHASE_LOG.md` under **Phase 6 → The live demo**. Read that; do not improvise the
order, because step 3 is destructive to a container that takes minutes to reload a 27B
checkpoint.

Cost: **$0.00**. Both models are the operator's own, on the operator's own cards.

# Phase 8 — results

Three pre-registered experiments, adjudicated against
[`experiments/PRE_REGISTRATION.md`](../PRE_REGISTRATION.md), which was committed at
`aa134f4` before any of this data existed.

**Status: two of three closed, one partial and already decided.**

| | verdict | cost |
|---|---|---|
| **H1** — the semantic-cache safety curve | **no safe threshold exists on this corpus.** BUILD_PLAN §P8.H1's second branch | **$0.00 so far** |
| **H2** — the suite through the gateway | **not run** — machinery, pre-flight and analysis ready; the operator's step | ~$8.20 |
| **H3** — failover under load | **two clauses hold; the third's aggregate bound is exceeded by 0.09 s and the mechanism under it holds exactly** | $0.00 |

Everything below is recomputed from committed artifacts by
`tests/test_experiments_h1.py`, `…_h2.py` and `…_h3.py` on every pull request. A number
here that no longer follows from its input turns the suite red.

---

# H1 — the semantic-cache safety curve

> everyone ships semantic caching; almost nobody measures how often it silently returns the
> wrong answer, because measuring that requires a large question set with exact ground
> truth. Backline is one. — BUILD_PLAN §P8.H1

## The finding

**On 130 Backline questions with exact ground truth, a semantic cache at the industry-default
0.90 threshold answers 75% of never-before-seen questions from a neighbouring entry, and 94%
of those answers are provably wrong.** There is no threshold anywhere in the pre-registered
range 0.70–0.99 at which the wrong answers stop: at 0.99 — a bar so high that six sevenths of
the savings are gone — 18 of 130 questions still receive a confidently wrong answer.

**τ₀, the pre-registered recommended threshold, does not exist.** The rule was fixed before
the curve (H-063): the lowest grid point whose silent-wrong-answer count is zero and stays
zero above it. No grid point qualifies. That is BUILD_PLAN's own second branch, quoted from
the plan and binding as written:

> If the curve has no usable knee (poison appears before meaningful savings), the finding is
> "semantic caching is unsafe for this workload class, here's the threshold-by-threshold
> proof."

Here is the threshold-by-threshold proof.

## The curve

130 novel-question probes (Family B, leave-one-out: each question asked against a cache
holding the other 129). Modelled saving is `hits × $0.0593`, Backline's measured $/query
including judge, fixed in the pre-registration.

**Primary — the prompt as the gateway embeds it:**

| threshold | served from cache | silently wrong | benign collision | miss | modelled saving |
|---:|---:|---:|---:|---:|---:|
| 0.70 | 130 | **123** | 7 | 0 | $7.71 |
| 0.80 | 127 | **121** | 6 | 3 | $7.53 |
| 0.85 | 117 | **111** | 6 | 13 | $6.94 |
| **0.90** (shipped default) | **98** | **92** | 6 | 32 | $5.81 |
| 0.95 | 32 | **32** | 0 | 98 | $1.90 |
| 0.99 | 18 | **18** | 0 | 112 | $1.07 |

**Secondary — question only, the answer-format tail stripped** (H-060's pre-registered
sensitivity check):

| threshold | served from cache | silently wrong | miss |
|---:|---:|---:|---:|
| 0.70 | 121 | 114 | 9 |
| 0.85 | 52 | 51 | 78 |
| **0.90** | **31** | **31** | 99 |
| 0.99 | 12 | 12 | 118 |

![the safety curve](h1_curve.svg)

Every point of both curves is in [`h1_curve.json`](h1_curve.json); every probe's top-3
neighbours and its classification are in [`h1_probes.json`](h1_probes.json).

## Why — the mechanism, not a mystery

The highest similarity at which the cache serves a provably wrong answer is **0.999539**.
That pair is:

```
asked   Scan every statement for period 2026-02 for reporting anomalies — duplicates,
        unknown ISRCs, currency mismatches, negative units, period bleed, suspicious
        territory spikes, and dashboard divergence. …
served  Scan every statement for period 2026-04 for reporting anomalies — duplicates,
        unknown ISRCs, currency mismatches, negative units, period bleed, suspicious
        territory spikes, and dashboard divergence. …

the caller receives   5 findings, on line ids 80023526, 89000001-4
the true answer is    2 findings, on line ids 100013815, 109000001
```

Two questions differing in **one period token**. Genuinely unrelated questions in the same
corpus sit at **0.578**, so the embedding is not broken — it is working exactly as designed.
Entity-substituted templated questions are maximally similar in form and maximally different
in answer, and a cosine threshold cannot see the difference because the difference is seven
characters wide.

That is not a property of Backline. It is a property of **every application that templates
its prompts**, which is most of them.

## The boilerplate effect, which is why H-060 was pre-registered

Stripping the shared answer-format tail drops the 0.90 hit rate from **75% to 24%**. The
eight instruction tails shared across 130 questions raise the floor under every pairwise
similarity — and they raise it most between questions that share an answer *type*, which is
to say between the near-misses.

Reporting only the tail-stripped curve would have understated the danger threefold. Naming
which curve was primary **before** either existed is the reason that is a finding here rather
than an argument.

## Per category, at the shipped 0.90 default

| category | n | miss | benign | **silently wrong** | closest wrong neighbour |
|---|---:|---:|---:|---:|---:|
| reconciliation | 15 | 0 | 0 | **15** | 0.9995 |
| royalty_math | 25 | 2 | 1 | **22** | 0.9533 |
| contract_terms | 20 | 3 | 2 | **15** | 0.9620 |
| recoupment_state | 15 | 0 | 3 | **12** | 0.9602 |
| catalog_lookup | 15 | 5 | 0 | **10** | 0.9956 |
| cross_collateral | 8 | 2 | 0 | **6** | 0.9106 |
| multi_step | 12 | 6 | 0 | **6** | 0.9926 |
| abstention | 10 | 6 | 0 | **4** | 0.9337 |
| sql_analytics | 10 | 8 | 0 | **2** | 0.9489 |

Every category is poisoned. `sql_analytics` fares best because its questions name distinct
statement ids in prose; `reconciliation` fares worst because its questions are one template
with a date in it.

## What this measures, and what it does not

- **It measures the cache, not the model** (H-059). Every seeded entry is ground truth from
  Backline's answer key, so a wrong answer here is the cache resolving to the wrong question
  and nothing else. A real cache also holds the model's own errors; Backline has published
  that rate separately (93.3) and the two compose.
- **A right answer for the wrong reason is not counted as correct** (H-061). Seven of the 130
  hits at 0.70 are *benign collisions* — a different question whose answer happens to be
  equivalent under Backline's own scorer, mostly abstentions sharing the `ABSTAIN` token.
  They are reported in their own column and never folded into "correct".
- **Family B is the unfavourable case, and it is stated as such.** These are questions the
  cache has never seen, so no hit can be correct. Family A — the paraphrase probes, where
  the right answer *is* in the cache — is the favourable case and measures the savings
  a cache would deliver. It needs the ~$0.30 paid step in
  [`RUNBOOK.md`](../RUNBOOK.md) §1 and **cannot change the τ₀ verdict**: adding probes cannot
  remove Family B's poison from any grid point.
- **Three of Backline's 133 questions are excluded**, mechanically and for an intrinsic
  reason: the prompt-injection canaries carry no T1 answer key, so they cannot be seeded from
  ground truth and have no defined equivalence (amendment A1).

## What a reader should take from it

Not "never use a semantic cache". The honest reading is narrower and more useful:

**A cosine threshold is the wrong control surface for a workload whose questions differ by an
entity or a period.** The similarity of a dangerous near-miss (0.9995) sits *above* the
similarity of many legitimate paraphrases, so no single number separates them. What would
work is not a higher threshold but a different mechanism — requiring the extracted entities
and periods to match exactly before a semantic hit is allowed, which is a filter rather than
a bar. Headroom does not ship that today, and this measurement is why it is the first thing
its cache should grow.

Meanwhile the shipped default of `0.90` is documented as measured on 12 questions and
expected to move. It has now moved: on 130 questions with ground truth, **0.90 is not safe
for this workload class, and neither is any other value in the range.**

---

# H2 — gateway overhead and the third parity treatment

**Not run.** The machinery, the pre-flight and the analysis are complete and tested; the run
itself spends ~$8.20 and is the operator's, with commands in [`RUNBOOK.md`](../RUNBOOK.md) §2.

One half is already measured, keylessly and for free.

## The gateway's admission cost — measured where the provider costs nothing

H-065's secondary, and the honest answer to *"why is a gateway in Python defensible"*. The
ledger cannot separate the gateway's own work from the provider's — `upstream_latency_ms`
contains both, with no mark between them — so the measurement runs against the MockProvider,
which answers in microseconds. What remains is authentication, routing, the rate limiter, the
cache lookup and the budget reservation.

2,000 requests through the full pipeline, per configuration:

| configuration | p50 | p95 | p99 |
|---|---:|---:|---:|
| in-memory stores | **0.207 ms** | 0.329 ms | 0.477 ms |
| DynamoDB Local behind the limiter and the budget gate | **1.441 ms** | 2.288 ms | 4.633 ms |
| **what the two atomic conditional writes cost** | **1.233 ms** | 1.959 ms | — |

Against Backline's measured **12,678 ms** p50 per question, the gateway's admission path is
**0.011%** of a request. The two DynamoDB conditional writes that make the budget gate and
the rate limiter unraceable — the thing Phases 4 and 4b are about — cost **1.2 ms**, which is
0.01% of the same request and is the first time this repo has priced them.

Caveat, recorded with the number: this excludes TLS and DNS setup to a real upstream. httpx
amortises those over a keep-alive pool, and a caller talking directly to the provider pays
them too.

Data: [`h2_bench.json`](h2_bench.json).

## What the paid run will report, and against what

Fixed before the run, in the pre-registration:

- **score parity** — `|overall − 93.3| ≤ 3.0` against the direct-local reference, which
  differs from this treatment by exactly one thing, the gateway hop (H-064). With the AWS
  (92.5) and sweep (91.6) runs as context and the strict `evals gate` on the record;
- **the limitation, stated before the result** — there is no same-day paired control, so
  between-day drift sits inside the residual. The claim H2 can support is about a *bound*,
  not an effect size;
- **`passthrough_overhead_ms` p50 < 50 ms** — H-051's column, named in Phase 6, reported first
  because it was promised and reported with its weakness attached: it is expected to be
  sub-millisecond, so passing by four orders of magnitude is a weak test;
- **the cache-disabled proof** — the count of rows whose `cache_disposition` is not
  `cache_disabled`, which must be zero or the overhead figure is a hit-rate figure (H-047);
- **the two-meter cross-check** — Headroom's ledger against Backline's own accounting over the
  same traffic: tokens must agree exactly, cost to within $0.01. A disagreement is a finding
  about one of the two meters, reported rather than reconciled away.

---

# H3 — failover under load

Adjudicated from evidence that already existed (H-067): the chaos suite CI has run since
Phase 6, and the 492 ledger rows the operator's 2026-08-10 two-GPU kill left in the compose
volume. **No new GPU session was required**; what was missing was never a picture but the
numbers behind one.

## Clause 1 — zero caller-visible 5xx for pre-first-token faults

**HOLDS, on both halves.**

*The mock chain*, three deterministic fault intensities cycling 529 / 429 / 500 / timeout /
connect-error through a two-provider chain:

| intensity | requests | faults | statuses | caller-visible 5xx | hops | primary breaker |
|---|---:|---:|---|---:|---:|---|
| light (25%) | 20 | 5 | all 200 | **0** | 5 | closed |
| heavy (50%) | 20 | 10 | all 200 | **0** | 18 | open |
| brutal (100%) | 20 | 20 | all 200 | **0** | 20 | open |

*The two-GPU kill*: **270 requests** on the `vllm_a → vllm_b` chain over **77 minutes**, one
every 4.65 s, across **two** kill-and-restore cycles. Every one returned **200**. 129 served
by the primary, 141 by the fallback — 58 after a real connection refusal, 82 skipped by an
open breaker, one after a timeout.

**Zero caller-visible errors while a GPU was dead, twice.**

## Clause 2 — recovery within a stated bound

**The pre-registered aggregate bound is exceeded on 2 of 39 probe gaps, by at most 0.09 s.
The mechanism it was a proxy for holds exactly, 37/37.** Both are published; neither was
adjusted.

The bound was arithmetic from H-052's published constants (§H3.2): re-admission happens on
the first request arriving more than `COOLDOWN_S = 10 s` after the last attempt, so under a
load of one request every `T` seconds it is observed within `10 + T`. With the median load
interval of 4.65 s that is **14.65 s**. Observed: median **14.07 s**, maximum **14.74 s** —
two gaps over, by 0.09 s and 0.02 s.

The diagnosis is in the data. The premise "one request every T seconds" idealises a loop
whose intervals actually spanned **4.01 s to 19.99 s**; a 4.74 s gap presenting after the
cooldown produces a 14.74 s probe gap and the breaker did nothing wrong. Checked directly,
without any aggregate: **every one of the 37 probes was the first request to arrive at or
after the previous attempt plus the cooldown.**

The cooldown is visible in the raw data, which is the part worth seeing. Once the breaker
trips, only the half-open probe reaches the dead provider, so the spacing between real
attempts *is* the cadence:

```
before the breaker trips   4.7  4.8  4.7  4.6  4.8  4.8  4.6  4.7   (the load's own interval)
after it trips            14.1 14.1 14.1 14.2 14.3 14.3 14.2 14.1   (cooldown + one interval)
```

| outage | duration | requests | real attempts | skipped by the breaker | back on the primary after |
|---|---:|---:|---:|---:|---:|
| 06:19:55 → 06:25:34 | 339 s | 72 | 30 | 42 | **4.8 s** |
| 07:24:01 → 07:30:21 | 380 s | 69 | 29 | 40 | **4.65 s** |

Both recoveries took exactly one load interval after the last hop: one probe went through,
succeeded, closed the breaker, and the next request was served by `vllm_a` again.

## Clause 3 — mid-stream faults surface as terminal error events, 100%

**HOLDS.** Four cut points, including one *after* the last text delta where the fragment
reads as a finished answer and only the missing terminal marker gives it away:

| cut after | terminal `error` event | `message_stop` present | fallback spliced in |
|---:|---|---|---|
| 1 chunk | yes | no | no |
| 4 chunks | yes | no | no |
| 8 chunks | yes | no | no |
| 12 chunks | yes | no | no |

**0 silent truncations, 0 splices.** §P8.H3's falsification condition is *any* silent
truncation reaching a caller; none did.

The live half cannot check this — a ledger records that a stream was cut, not what frames the
caller saw — and the analysis says so rather than claiming it. There were no mid-stream cuts
on the vLLM chain in this window anyway; the six in the same ledger belong to `make seed`'s
mock traffic and are counted separately.

Data: [`h3_chaos.json`](h3_chaos.json), [`h3_livekill.json`](h3_livekill.json), and the raw
rows at `docs/evidence/p8-experiments/h3-livekill-ledger-rows.json`.

---

## Spend

| | |
|---|---|
| H1, this report | **$0.00** — the corpus was embedded on the operator's CPU and the sweep is arithmetic |
| H2 | **$0.00** — not yet run |
| H3 | **$0.00** — mock chain, and the operator's own GPUs |
| **Phase 8 to date** | **$0.00** against the §0.6 caps of $1 / $10 / $6 |

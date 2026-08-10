# Phase 8 — pre-registration

BUILD_PLAN §0.2 invariant 8:

> **Pre-registration.** Every experiment in Phase 8 has its hypothesis, metrics, and
> falsification conditions written in this plan *before* data exists. Both outcomes are
> publishable. Readings are never re-taken until they flatter.

BUILD_PLAN §P8 already carries the hypotheses. This file is the **executable** half: the
places where §P8 leaves a choice open, made here, with reasons, and committed before the
data that would tempt anyone to choose differently. Where §P8 or `docs/DECISIONS.md`
already fixes something, this file quotes it rather than restating it in different words.

**Nothing below may be edited after the measurement it governs exists.** An amendment is
a new section at the bottom, in a PR, dated, with the reason — and only ever *before* the
data it affects (the H-047 precedent, made in Phase 5 for exactly this reason).

---

## 0. Provenance, and what was already known when this was written

| | |
|---|---|
| Written | 2026-08-10, Phase 8, branch `claude/p8-experiments` |
| Committed | **before** any H1 corpus, any H1 sweep, any H2 run, and any H3 analysis |
| Gateway | this branch's parent, `01ff2fc` |
| Suite under measurement | Backline `evals/suites/core.json`, `suite_hash 6eef41c6706f309a`, `world_seed 20260805`, 133 questions |

**One disclosure, because the alternative is a document that is quietly less honest than
it looks.** H3's live data *already exists*: the operator ran the two-GPU kill demo on
2026-08-10 and 492 ledger rows survive in the compose volume. While scoping this phase
the coarse shape of those rows was visible — the row count, and the distribution of
`outcome` values. It was therefore not possible to write H3's section in ignorance of the
data, and pretending otherwise would be the exact failure mode invariant 8 exists to
prevent.

What follows from that, and is the actual protection: **every bound in §H3 below is
derived from a number published in `docs/DECISIONS.md` H-052 or from BUILD_PLAN §P8.H3's
own text, both of which predate the data by two phases.** No bound in §H3 was chosen by
looking at the rows, and §H3.5 names the one quantity where that claim would be
unverifiable and states what was done instead. H1 and H2 carry no such caveat: neither
corpus nor run exists yet.

---

## 1. H1 — the semantic-cache safety curve

> **The gap in the world:** everyone ships semantic caching; almost nobody measures how
> often it silently returns the wrong answer, because measuring that requires a large
> question set with exact ground truth. Backline is one. — BUILD_PLAN §P8.H1

### H1.1 The corpus, and where its answers come from

The golden artifact is `experiments/artifacts/h1_corpus.json`, content-hashed, committed,
and regenerated only by a deliberate, reviewable diff.

**Source questions.** All 133 questions of Backline's committed `core` suite, read from
`evals/suites/core.json` with its `suite_hash` recorded in the artifact. Nothing is
selected, filtered, or dropped: a suite subset chosen by the experimenter is a suite
chosen to produce a number.

**Every prompt splits into a question and a protocol.** Backline's prompts are
`<question body>` + `\n\n` + `<answer-format instruction>` — verified mechanically:
all 133 split at their first blank line, into 133 bodies and **8 distinct instruction
tails** (`End your reply with a line exactly \`ANSWER: $<amount>\` (USD).` and seven
others). The split is part of the artifact and is asserted by a keyless test, because
everything below depends on it.

**Seed content is the answer key — ground truth — not a prior model run.** Argued in
**H-059**. The consequence that matters here: every seeded cache entry is *correct for
its own question by construction*, so any wrongness in a hit is attributable to the cache
alone. Seeding from a scored model run would blend two error sources — the model was
wrong, and the cache served the wrong entry — and H1 would then be measuring their sum
while claiming to measure the second.

**A seeded entry's body is synthesised deterministically from the key**, in Backline's own
`ANSWER:` protocol, by code with no model in it. It is a real Anthropic-dialect response
body so that the gateway's own store and replay path handle it unchanged.

### H1.2 The paraphrase artifact

Per BUILD_PLAN §P8.H1's QA chain, in order, all of it before any measurement:

1. **Generation.** `claude-haiku-4-5`, **3 paraphrases per question → 399 probes**, under
   the preserve-every-entity rubric committed at `experiments/h1/rubric.py`. Operator-run,
   with a hard dollar stop inside the generator (§4).
2. **The answer protocol is preserved byte-for-byte.** Only the question body is
   paraphrased; the instruction tail is re-attached verbatim. Argued in **H-060**. A
   paraphrase that rewrote `ANSWER: $<amount>` would be changing what was asked, not how.
3. **Mechanical checks**, applied to every candidate: entity tokens, period tokens,
   numerals and money figures present in the question body must survive; the tail must be
   identical; the paraphrase must not equal the original; length must stay within a stated
   band. A candidate that fails is regenerated **before** the artifact is built.
4. **Operator spot-check.** A sample (n = 20, drawn by a seeded RNG whose seed is in the
   artifact) plus the rubric go to the operator. **The operator's approval is a gate**:
   the sweep does not run until it is recorded in the artifact's provenance block.
5. **Risk register item 3, in force:** *a bad paraphrase batch is regenerated BEFORE any
   measurement, never after.* Once the sweep has run, the artifact is frozen; a later
   objection to the batch is a new artifact and a new, separately reported run.

### H1.3 What gets embedded

**Primary: the full prompt exactly as Backline sends it** — question body *and* instruction
tail. That is the text the shipped gateway would embed (`Dialect.cache_probe` takes the
user turn), so it is the only text whose similarity numbers describe a cache anyone could
actually deploy in front of this suite. It also means the eight shared instruction tails
raise the floor under every pairwise similarity, which is a real property of this workload
and not an artifact to be cleaned away.

**Secondary (sensitivity): the question body alone.** Reported beside the primary, never
in place of it. Free — it is one more embedding pass over the same texts — and it is the
answer to the one objection this design invites ("your similarities are inflated by
boilerplate"). Argued in **H-060**.

Embeddings: `BAAI/bge-small-en-v1.5` (BUILD_PLAN L6), CPU, `normalize_embeddings=True`,
384 dimensions, rounded to 6 decimal places — the P5 corpus's own convention, so a
regenerated artifact stays a reviewable diff.

### H1.4 Two probe families

| | Family | n | Is there a right answer to find? |
|---|---|---:|---|
| **A** | **Paraphrase probes** — each probe is a paraphrase of exactly one seeded question | 399 | **Yes.** Its own question is in the cache. |
| **B** | **Novel-question probes** — each canonical question probed against a cache holding the other **132** | 133 | **No.** Leave-one-out; every hit is a wrong-source hit by construction. |

**Family A is the primary and is the plan's curve.** Family B is pre-registered as a
secondary and is argued in **H-062**: a production cache spends most of its life being
asked questions it has never seen, and for those the only available outcome is a miss or a
wrong-source hit. Family B costs nothing — it needs no paraphrases at all, only the 133
canonical vectors — and it is the floor the primary curve sits on.

### H1.5 The admission decision, and how it is replayed

The decision is **the shipped one**, not a re-implementation: cosine similarity over one
`CacheNamespace`, identical `context_hash` and `embedding_model`, `similarity >= threshold`,
**top-1** — `ResponseCacheStore.search(..., limit=1)`. The sweep replays it offline from
the committed similarity matrix, which is exactly the primitive Phase 5 built for it
(`search(threshold=0.0, limit=k)`, `docs/DECISIONS.md` under `ResponseCacheStore.search`).

- **Top-1 only.** The gateway serves the best match; a k > 1 analysis would measure a
  gateway nobody runs.
- **Ties** (identical cosine to 6 dp) break by ascending question id. Deterministic, and
  recorded so a re-run is bit-identical.
- **Grid:** `0.700 → 0.990` in steps of `0.005` (59 points) for the committed curve and the
  figure. In addition the sweep reports the **exact breakpoints** — the similarity values
  at which any probe's classification changes — because the curve is a step function and
  its steps are the finding, not the grid.
- **Self-matches are excluded in Family B** and impossible in Family A (a paraphrase's text
  is never a seeded text).

### H1.6 Metrics — exact definitions

For one probe at one threshold, with `S` the top-1 entry's source question and `Q` the
probe's own source question:

| Outcome | Condition |
|---|---|
| **miss** | best cosine `< threshold` |
| **correct hit** | hit and `S == Q` |
| **benign collision** | hit, `S != Q`, and `answer(S)` is **equivalent** to `expected(Q)` |
| **silent wrong answer (SWA)** | hit, `S != Q`, and `answer(S)` is **not** equivalent to `expected(Q)` |

**Answer equivalence is Backline's own arithmetic, not a re-implementation.** It is
computed at artifact-build time with `evals.answers` / `evals.scoring` primitives — the
same code that scores the suite — over all 133 × 133 ordered pairs, and the resulting
matrix is committed inside the artifact. CI then replays it with no Backline import and no
Postgres, which is the P5 pattern (compute once where the dependencies live, commit,
replay keylessly). Argued in **H-061**.

Benign collisions are counted and reported **separately and never folded into "correct"**:
a right answer reached for the wrong reason is luck, and a cache that is graded on luck is
graded on the corpus rather than on itself. The commonest source is the 10 `abstention`
questions, whose expected answer is the same `ABSTAIN` token.

Reported per threshold, for each family, for both embeddings:

- `hit_rate = hits / probes`
- `swa_rate_of_probes = SWA / probes` — **the primary poison metric**
- `swa_rate_of_hits = SWA / hits` — the conditional rate, reported beside it because the
  two answer different questions ("how often does the cache lie" vs "how often is a hit a
  lie") and quoting only the flattering one is the failure mode here
- `benign_rate = benign / probes`
- `usd_saved = hits × $0.0593`

**`$0.0593` is fixed here and is not re-derived after the fact.** It is Backline's measured
`$/query` including judge from its **local-control** run `a309dc57` (`deploy/aws/README.md`
parity table) — the lowest of the three published figures ($0.0593 / $0.0602 / $0.0609), so
the modelled saving errs *against* the cache. BUILD_PLAN §P8.H1 says "≈ $0.06/query"; this
is that number, sourced.

### H1.7 The recommended threshold is a rule, not a reading

Fixed now, so the curve cannot be read for a flattering answer:

> **τ₀, the zero-poison floor** — the **lowest** grid threshold at which SWA over
> **Family A ∪ Family B** is zero *and stays zero at every higher grid point*.

Because hit rate is non-increasing in threshold, τ₀ is also the maximum-savings point among
zero-poison thresholds, so one rule settles both questions. If no such threshold exists in
`[0.700, 0.990]`, **there is no τ₀** and the finding is BUILD_PLAN's second branch (below).
Also reported unconditionally, whatever it says: **where the shipped default `0.90` lands on
the curve.** Argued in **H-063**.

### H1.8 Falsification / both-outcomes clause

Quoted from BUILD_PLAN §P8.H1 and binding as written:

> if wrong-hits are zero across the whole range even at 0.70, the finding is "semantic
> caching is safer than feared on entity-dense corpora" and that gets published with equal
> enthusiasm. If the curve has no usable knee (poison appears before meaningful savings),
> the finding is "semantic caching is unsafe for this workload class, here's the
> threshold-by-threshold proof."

The third outcome — a τ₀ exists and is above or below the shipped 0.90 — is published as
found, including the case where it says the shipped default is **wrong**. `0.90` was
measured on 12 questions (`DEFAULT_SIMILARITY_THRESHOLD`'s own docstring says so and says it
is expected to move); moving it is a result, not an embarrassment.

---

## 2. H2 — gateway overhead, and the third parity treatment

### H2.1 Configuration — fixed, and checkable after the fact

**H-047 governs and is quoted in BUILD_PLAN §P8.H2:** *H2 runs against a tenant with
caching disabled entirely; overhead is measured on pure passthrough.* Three checks, all
pre-registered:

1. `GET /admin/cache/{tenant}` reports `mode: disabled` **before** the run — the pre-flight
   refuses otherwise.
2. Every ledger row for the run carries `cache_disposition = cache_disabled`. **The count of
   rows that do not is reported and must be zero.**
3. The tenant has no rate limits configured, so the limiter can never shed a suite request
   and turn a measurement into a retry pattern.

Backline is pointed at the gateway by **`ANTHROPIC_BASE_URL` alone** (assumption A2), with
`ANTHROPIC_API_KEY` set to a Headroom **virtual key**. Verified: `anthropic` 0.120.2 reads
`ANTHROPIC_BASE_URL` when `base_url` is `None`, and Backline's `AnthropicProvider` passes
`None`. **Zero changes to Backline**, and in particular zero changes to its scoring.

Run flags, fixed now, matching the two committed reference runs: `--suite core --model
claude-sonnet-5`, default concurrency 4, default judge (`claude-sonnet-5`), no
`--categories`, no `--gate-subset`.

### H2.2 Score parity — the pre-registered criterion

**Primary:** the through-gateway overall score satisfies **|overall − 93.3| ≤ 3.0**.

`93.3` is Backline's **direct-local** fresh run (`a309dc57`), and it is the right
comparator because it differs from this treatment by exactly one thing: the gateway hop.
`3.0` is BUILD_PLAN §P8.H2's own bound, itself Backline's documented same-model noise floor
(BENCHMARK_NOTES §5.4 measured a 3.2-point same-model spread). Argued in **H-064**.

**Reported as context, never substituted for the primary:** the observed overall against
`92.5` (AWS) and `91.6` (sweep `62865d3c`), and the full per-category table beside all
three, in the shape of `deploy/aws/README.md`'s parity table.

**Secondary, on the record with its known failure modes:** `python -m evals gate` is run and
its verdict reported per Backline §A5.5 — including the fact that the strict gate failed on
*both* runs of the AWS experiment, in different places, and that a legitimate fresh run can
fail it on variance alone (T2 flicker, small-n category swing).

**The limitation, stated before the result:** there is **no same-day paired control**. §A5.5
defines parity as a paired fresh-control-vs-fresh-treatment comparison; a control run would
cost another ~$8 and BUILD_PLAN §0.6 budgets $10 for H2. So between-day drift sits inside
this experiment's residual and cannot be separated from the gateway's effect. That weakens
the claim in a specific, stateable way and it is stated rather than discovered.

### H2.3 Overhead — three numbers, because one would be misleading

Argued in **H-065**.

**Primary (the plan's, and H-051's column): `passthrough_overhead_ms` p50 < 50 ms.**
Defined by `RequestContext` as *first upstream byte → first byte out*. H-051 names this
column as the one §P8.H2 publishes against the pre-registered p50 < 50 ms, and that
decision predates this data. Reported as p50 / p95 / p99 / max over every `outcome = ok`
row of the run.

**Stated now rather than after the fact:** this figure is expected to be *sub-millisecond*
— P1 measured 0.006 ms and P6 measured 0.019 ms against the mock — so meeting a 50 ms target
by four orders of magnitude is a weak test, and reporting it alone would be flattering by
construction. It is reported first because it is the pre-registered metric, and it is
reported with that sentence attached.

**Secondary A — admission cost, keyless, free.** Headroom's ledger cannot separate "gateway
admission work" from "provider time": `upstream_latency_ms` is *request received → first
upstream byte* and contains both. So admission cost is measured where the provider costs
nothing: **`upstream_latency_ms` over N ≥ 2,000 MockProvider requests through the full
pipeline** (auth, routing, rate limiter, cache, budget gate), p50 / p95 / p99. Caveat
recorded now: it excludes TLS/DNS setup to a real upstream, which httpx amortises across a
keep-alive pool and which a direct caller pays too.

**Secondary B — end-to-end latency parity.** Backline's own per-question p50 / p95 through
the gateway against the three reference runs' p50s (12,678 / 12,508 / 13,033 ms). This is
the caller-visible answer, and it carries the loudest caveat: latency across days and model
load is far noisier than the score, and there is no paired control.

### H2.4 Error accounting and the two-meter cross-check

From the ledger, reported whatever it says: the `outcome` distribution, the `error_reason`
distribution, the `cost_status` distribution, `unpriced_requests`, `budget_status`, and
`failover_hops` (which must be **0** — the `claude-` route has no chain, by design). From
Backline: `errors.n`, quarantined infra errors, `n_skipped_budget`, `budget_exhausted`.

**The cross-check, pre-registered as falsifiable:** Headroom and Backline meter the same
traffic independently — Headroom from the usage block it observes in the stream, Backline
from the SDK's own. Backline uses **no** prompt caching (verified: no `cache_control` in its
provider path), so H-026's `partial` caveat cannot arise.

> **Pre-registered:** total input and output tokens agree **exactly**; total cost agrees to
> **within $0.01**. A disagreement is a finding about one of the two meters and is reported
> as such, not reconciled away.

### H2.5 Falsification

Any of these is the result and is published as found: overall outside `[90.3, 96.3]`;
`passthrough_overhead_ms` p50 ≥ 50 ms; a non-zero count of rows whose `cache_disposition`
is not `cache_disabled` (which would invalidate the run's overhead figure outright, per
H-047); a token disagreement between the two meters; or infra errors that survive one heal
pass. **No number is re-rolled for a friendlier draw**; the heal loop
(`--resume <id> --retry-errors`) is available for *infra* errors exactly once, per Backline
D-032, and its use is reported.

---

## 3. H3 — failover under load, formalised

### H3.1 The three clauses, quoted

From BUILD_PLAN §P8.H3, binding as written:

1. **zero caller-visible 5xx for pre-first-token faults at every intensity;**
2. **recovery (breaker re-admission) within a stated bound;**
3. **mid-stream faults surface as terminal error events 100% of the time, never as silent
   truncation.**

> Falsification is any silent truncation reaching a caller — that would be a real bug and a
> real (unflattering, still published) finding.

### H3.2 The stated bound (clause 2), derived from H-052

H-052 publishes the breaker's constants: a **10-second cooldown**, and a half-open state
that admits **exactly one probe**. The transition happens on the request that finds the
cooldown elapsed — there is no scheduler. So the bound is arithmetic, not a choice:

> **Re-admission occurs on the first request issued more than `COOLDOWN_S = 10 s` after the
> provider became reachable again; under a load of one request every `T` seconds,
> re-admission is observed within `10 + T` seconds.**

For the demo's 2-second loop that is **≤ 12 s**. The constant is read from
`headroom/policy/health.py`, not typed into this document, so the two cannot drift.

### H3.3 What is measured, and where it comes from

**(a) The mock chain, three intensities — the reproducible half.** BUILD_PLAN's "scripted
fault schedules at three intensities" is `tests/test_failover_chaos.py`, green in CI since
P6 at 25% / 50% / 100% fault rates. Phase 8 promotes it from a passing test to a *reported*
experiment: a runner emits a committed results artifact carrying the numbers, so REPORT.md
adjudicates figures rather than citing a green tick. Deterministic, keyless, $0.00.

**(b) The two-GPU live kill — the operator's half.** The 492 ledger rows surviving from the
2026-08-10 run, filtered to the `vllm_a` / `vllm_b` chain. Reported: caller-visible 5xx by
outcome, hop counts, `failover_error` distribution (`upstream_unavailable` vs
`breaker_open` — the two are deliberately distinguishable, H-051), the observed load rate,
and the re-admission interval measured against §H3.2's bound.

**Pre-registered as a possible outcome, not a defect:** a caller-visible 5xx whose fault
arrived **after** the first byte is *outside* clause 1 by construction — H-048 makes that
permanent and the README already says the gateway can promise zero *silent* failures, not
zero failures. Such rows are reported in their own line rather than folded into either
column.

### H3.4 Sustained load, and what P6/P7 already satisfy

Stated explicitly, since the phase brief asks for the delta rather than a re-demand:

| §P8.H3 clause | Already satisfied by | Still needed |
|---|---|---|
| three intensities, mock chain | **P6** — `tests/test_failover_chaos.py`, in CI on every PR | a committed *results artifact* (H3.3a) |
| mid-stream faults → terminal error, 100% | **P6** — four cut points asserted, plus the splice sabotage | reported figures |
| the two-GPU live kill | **P6** (ledger + logs) and **P7** (`docs/evidence/p7-dashboard/`, 7 stills + `hero.gif`, kill timestamp, psql cross-check) | the *numbers* behind the pictures |
| **sustained load** | **P7's run** — 492 rows over ~92 minutes, 270 of them on the vLLM chain, driven by the runbook's 2-second loop | characterised, not re-recorded |
| recovery within a bound | H-052's constants; observed in P6's container run and P7's `07-recovered.png` | the bound stated (§H3.2) and the interval measured |

**Conclusion recorded in advance: no new GPU session is required for H3.** The recording
exists; what Phase 8 adds is the adjudication. If the analysis finds the surviving rows
cannot answer a clause, that gap is reported and a re-record is scheduled — it is not
quietly dropped.

### H3.5 The one place the disclosure in §0 bites

Clause 2's bound could in principle have been reverse-engineered from the surviving rows.
It was not: it is `COOLDOWN_S` plus the load interval, both of which are published constants
predating the data (H-052, Phase 6; the 2-second loop, PHASE_LOG Phase 7's runbook). There
is no free parameter in §H3.2 that a reading of the data could have set.

---

## 4. Money — whose stop is whose

BUILD_PLAN §0.6 caps: **$1** H1 paraphrase generation, **$10** the H2 run, **$6** P8
contingency / heal passes, **$20** total live API spend for the whole project. Spend to
date across P0–P7: **$0.00 measured** plus the P1/P3 live smokes (< $0.01). Argued in
**H-066**.

| Stop | Where it lives | Value | What it is for |
|---|---|---|---|
| H1 generation cap | inside `experiments/h1/generate.py` — the generator refuses to issue a call once committed spend would exceed it | **$1.00** | the §0.6 line item, enforced by the harness rather than by the operator's attention |
| H2 run cap | Backline's own `--budget`, which reads **committed** spend (landed + reserved) | **$12.00** | the operative stop. Set **above** Backline's own projection of **$11.27** (computed from its `project_cost`, not quoted) so the runner does not refuse to start and no one reaches for `--yes` — the failure mode `AWS_DEPLOY_PLAN.md` §9 names by hand |
| H2 backstop | Headroom's **own** budget gate on the H2 tenant | **$15.00** monthly | the independent second guard, deliberately set *above* the operative stop: a backstop that fires in normal operation is not a backstop. It can only fire if Backline's accounting is wrong, which is exactly when a second opinion is worth having |
| H3 | — | **$0.00** | mock chain and the operator's own GPUs |

**Expected H2 spend is ~$8.09** (Backline's three measured full runs: $7.88 / $8.01 /
$8.09). The $12 flag is a *stop*, not a plan to spend $12; the gap between §0.6's $10 line
and the $12 stop is covered by the $6 contingency bucket and is logged rather than
absorbed. Worst case for the whole phase — $1 + $12 — is **$13 against the $20 project cap**,
which stays intact.

**No command in this phase that spends money is run by Claude Code.** Every one is handed
to the operator in `experiments/RUNBOOK.md` with its expected cost stated up front
(BUILD_PLAN §0.2 invariant 2's discipline, applied to spend rather than to infrastructure).

---

## 5. Amendments

*(None. An amendment is appended here, dated, in the PR that makes it, and only before the
data it affects — the H-047 precedent.)*

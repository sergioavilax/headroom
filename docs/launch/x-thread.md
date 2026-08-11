# X thread

**The hook is the curve, not the gateway.** Nobody wants to read about another LLM proxy;
a lot of people have shipped a semantic cache this quarter and would like to know what it
is doing to them.

Ten posts. Post 1 has to survive being read alone. Post 10 is the only one with an ask in
it. Every number below is in [`README.md`](../../README.md), pinned by
`tests/test_docs.py`.

**Attach to post 2:** `experiments/results/h1_curve.svg`.
**Attach to post 7:** the console mid-kill, `docs/evidence/p10-eks/24-live-flip.png`.

---

**1/**

Everyone ships semantic caching.

Almost nobody measures how often it silently returns the wrong answer — because measuring
that needs a big question set with exact ground truth.

I had one. So I measured it.

At the industry-default 0.90 threshold, 1 hit in 5 is wrong. 🧵

---

**2/**

The setup: 130 questions, each with an exact expected answer. Seed a cache with all of
them, then ask 520 probes — real paraphrases *and* novel questions — and replay the
admission decision across every threshold from 0.70 to 0.99.

One embedding pass. Zero marginal API cost.

[attach h1_curve.svg]

---

**3/**

At 0.90:

• 98 of 130 **never-before-seen** questions get answered from cache
• **92 of those answers are provably wrong**
• meanwhile 389 of 390 genuine paraphrases hit, 382 correctly

Both halves matter. It looks great on the traffic you tested with.

---

**4/**

"Just raise the threshold."

No:

closest **wrong** answer: **0.999539**
furthest **correct** answer: **0.889850**

The bands overlap. A threshold is one number and these are two distributions. There is no
value in the range with zero wrong hits.

---

**5/**

The 0.9995 pair, in full:

  "Scan every statement for period **2026-02** for reporting anomalies…"
  "Scan every statement for period **2026-04** for reporting anomalies…"

Answer to one: 5 findings.
Answer to the other: 2 findings.

Seven characters apart.

---

**6/**

The embedding isn't broken. It's *right*. Those two prompts really are 99.95% the same
text.

The bug is using a similarity score as an **admission decision**, when the seven
characters it correctly calls negligible are the entire answer.

That's every app that templates its prompts. Which is most of them.

---

**7/**

What actually works isn't a higher bar, it's a different mechanism: require the extracted
entities and periods to match exactly before a semantic hit is allowed. A **filter**, not a
threshold.

I didn't ship that. The measurement is why it's the first thing the cache should grow.

---

**8/**

The thing that produced the measurement is a real gateway: virtual keys, per-tenant
budgets and rate limits on atomic DynamoDB conditional writes, exact + semantic caching,
provider failover, per-request cost attribution, a console.

Python. The overhead question, answered rather than dodged:

---

**9/**

Running the whole 133-question suite *through* the gateway:

• score 93.7 vs 93.3 direct — Δ +0.4 against a pre-registered bound of 3.0
• passthrough overhead p50 **0.0612 ms**
• 0.0249 ms on ECS behind an ALB, 0.0175 ms on EKS behind an NLB
• two independent meters over one $7.54 run: $7.541253 vs $7.540398

[attach 24-live-flip.png]

---

**10/**

Also in there: a rolling helm upgrade measured to 0 dropped requests — after two runs that
read 1 and 2, both committed, because the third one only means something if the instrument
could have said otherwise.

Every number in the README is recomputed from a committed artifact by a test.

github.com/sergioavilax/headroom

---

## Notes for posting

- **Post 1 is the whole thread.** If it does not land, nothing after it is read. Do not
  soften "1 hit in 5 is wrong" into "can be wrong".
- **Do not say "semantic caching is broken."** Post 6 and 7 are the actual claim and they
  are narrower. Anyone who reads it as "never cache" will be corrected in the replies by
  someone quoting post 3, which is fine.
- **Expected reply: "your embedding model is too small."** The answer is post 6 — this is
  not a quality failure, it is a category error, and a better embedder makes the two
  prompts *more* similar, not less. `bge-small-en-v1.5`, CPU, and the corpus and vectors
  are committed so anyone can re-run the sweep.
- **Expected reply: "why Python."** Post 9. The admission path is 0.012% of a request.
- **Expected reply: "did you try k>1 / rerank?"** Honest answer: no. The sweep replays the
  *shipped* admission decision (top-1, cosine ≥ threshold), because a k>1 analysis would
  describe a gateway nobody runs. Named as a limitation, not defended as a choice.

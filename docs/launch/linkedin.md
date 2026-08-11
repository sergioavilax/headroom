# LinkedIn post

Different audience, same finding. X rewards the number; LinkedIn rewards **why an engineer
built the instrument that produced it**. Longer paragraphs, no thread numbering, and the
platform truncates at roughly 200 characters — so the hook has to survive "…see more".

Every figure is in [`README.md`](../../README.md) and pinned by `tests/test_docs.py`.

**Image:** `experiments/results/h1_curve.svg`.

---

Everyone ships semantic caching. Almost nobody measures how often it silently returns the
wrong answer — because measuring that requires a large question set with exact ground
truth.

I had one, so I measured it. At the industry-default similarity threshold of 0.90, one
cache hit in five was a wrong answer served with confidence.

The setup: 130 questions from a benchmark where every question has an exact expected
answer. Seed a cache with all of them, embed 520 probes — genuine paraphrases and novel
questions both — and replay the cache's admission decision across every threshold from
0.70 to 0.99. One embedding pass, no marginal API cost, a full curve.

At 0.90, 98 of 130 never-before-seen questions were answered from a neighbouring entry,
and 92 of those answers were provably wrong. On the same corpus at the same threshold, 389
of 390 real paraphrases hit, 382 of them correctly. Both halves are the finding: a control
surface that looks excellent on the traffic you tested it with and poisons the traffic you
did not is not a safe control surface.

The obvious fix does not work. The closest wrong answer scores 0.999539; the furthest
correct one scores 0.889850. The two bands overlap, so no single cosine number separates
them. The worst pair is two questions differing in one date token — 2026-02 against
2026-04 — with completely different answers.

The embedding is not broken. It is right. Those two prompts really are 99.95% the same
text. The failure is in using that measurement as an admission decision, because the seven
characters it correctly treats as negligible are the entire answer. That is not a property
of my benchmark; it is a property of every application that templates its prompts, which
is most of them.

What would work is not a higher threshold but a different mechanism: requiring the
extracted entities and periods to match exactly before a semantic hit is allowed. A filter
rather than a bar. I have not shipped it, and this measurement is why it is the first thing
the cache should grow.

The instrument is Headroom — an LLM gateway and control plane. Virtual keys, per-tenant
budgets and token-bucket rate limits enforced on atomic DynamoDB conditional writes,
exact + semantic caching, provider failover with a circuit breaker, per-request cost
attribution, and a console. Compose locally, ECS Fargate + RDS + DynamoDB + one Lambda on
AWS, and a Helm chart on EKS — where a rolling upgrade was measured to zero dropped
requests, after two runs that read one and two. All three runs are committed, because the
zero only means something if the instrument could have said otherwise.

The gateway question people actually ask — "isn't Python too slow for this?" — is answered
rather than dodged: running the full 133-question suite through the extra hop scored 93.7
against 93.3 direct, inside a bound fixed before the run, with passthrough overhead of
0.0612 ms at the median and an admission path costing 0.012% of a request.

Every number in that repo's README is recomputed from a committed artifact by a test on
every pull request. A claim that no longer follows from its evidence turns the build red.
That discipline is the part I would want to be judged on.

github.com/sergioavilax/headroom

#LLM #MachineLearning #PlatformEngineering #Python #Kubernetes #AWS

---

## Notes for posting

- **The first two lines are the whole post** on mobile. Do not open with "I've been
  working on…".
- **The comment to leave on your own post** (it doubles reach and it is the most
  interesting thing you cannot fit): the human spot-check caught two systematic generation
  failures the mechanical checks could not see — a two-part ask collapsing to one part, and
  a prohibition inverting into an instruction — and neither was reachable by re-reading the
  sampled probes. Twenty probes, three review rounds, both catches pre-measurement.
- **Do not tag anybody.** The post is about a measurement; tagging turns it into a request.

# Launch kit

Everything needed to put Headroom in front of people, written once so the operator is not
drafting under launch-day pressure. **Nothing here is published by this repo** — each file
is copy for a human to read, edit, and post.

| File | What it is | Where it goes |
|---|---|---|
| [`x-thread.md`](x-thread.md) | The thread. The H1 curve is the hook | X / Twitter |
| [`linkedin.md`](linkedin.md) | The same finding at a hiring manager | LinkedIn |
| [`portfolio-insert.sql`](portfolio-insert.sql) | The project row for sergioavi.la | the portfolio database |
| [`recruiter-followup-v2.md`](recruiter-followup-v2.md) | The follow-up template, v2 — now with Kubernetes on it | email |
| [`blog-the-cache-that-lies-politely.md`](blog-the-cache-that-lies-politely.md) | The third post. The trilogy closes: **zeros → reds → the cache that lies politely** | sergioavi.la/blog |

**One rule for all of it.** Every number in this kit is a number in
[`README.md`](../../README.md), and every number there is recomputed from a committed
artifact by [`tests/test_docs.py`](../../tests/test_docs.py). If a draft below needs a
figure that is not in the README, the honest move is to add it to the README with its
artifact — not to type it here.

---

## GitHub "About", to paste

**Description** (one line, ≤ 350 characters — GitHub truncates the card at about 120, so
the first clause has to carry it):

```
An LLM gateway and control plane — virtual keys, atomic budgets and rate limits, exact + semantic caching, provider failover, per-tenant cost metering. Ships the measurement almost nobody publishes: how often a semantic cache silently returns the wrong answer.
```

**Website:** `https://sergioavi.la`

**Topics** (GitHub allows 20; these are lowercase, hyphenated, and real):

```
llm  llm-gateway  ai-gateway  api-gateway  semantic-cache  rate-limiting  fastapi
python  postgresql  pgvector  dynamodb  aws  terraform  kubernetes  helm  eks
observability  finops  vllm  anthropic
```

**Checkboxes:** Releases off, Packages off, Deployments off, Environments off — none of
them are used, and an empty sidebar section reads as an abandoned one.

---

## The order to do it in

1. **Merge the Phase 11 PR**, so the README a stranger lands on is the one the thread
   points at.
2. **Set the About block** above. The card is what a link preview shows.
3. **Post the blog first**, so the thread and the LinkedIn post have somewhere to send
   people who want the long version.
4. **X thread**, then **LinkedIn** — a few hours apart, not simultaneously. The audiences
   overlap less than it feels like they do.
5. **The portfolio row** whenever; it is the durable one.
6. **The recruiter template** is not a launch step. It is what gets sent to the threads
   already in the inbox, once there is a link worth sending.

## What not to claim

Stated here because launch copy is exactly where an honest repo starts overselling:

- **Not** "semantic caching is broken". The finding is that *a cosine threshold is the
  wrong control surface for templated prompts*, and the repo says what would work instead.
- **Not** "faster than a gateway in Go". The overhead number says the gateway's own cost is
  negligible against inference; it says nothing about a comparison nobody ran.
- **Not** "battle-tested" / "production-ready". One operator, one network, one run per
  experiment row, a fourteen-hour cluster window. The README's Limits section is the
  honest version and the copy should not outrun it.
- **Not** a parity *win*. Δ +0.4 is inside the noise bound, in the gateway's favour, with
  no paired control. It means "the hop did not cost accuracy", not "the hop helped".

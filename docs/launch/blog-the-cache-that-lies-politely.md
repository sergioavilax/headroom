# The cache that lies politely

**Draft, for sergioavi.la/blog.** Third of three. BUILD_PLAN §P11 names the arc — *zeros →
reds → the cache that lies politely* — so the closing callback below assumes the first two
posts exist and that the operator will adjust the one paragraph that refers to them. Every
figure is in [`README.md`](../../README.md) and pinned by `tests/test_docs.py`; nothing
here needs re-deriving, and nothing here should acquire a number that is not there.

Suggested subtitle: *I measured how often a semantic cache returns the wrong answer. The
answer is one hit in five, and no threshold fixes it.*

Reading time ≈ 8 minutes.

---

Semantic caching is the easiest win in an LLM application. You embed the question, you
search for a near-enough previous question, and if the cosine similarity clears some bar
you hand back the old answer. It costs nothing, it is four hours of work, and the first
time you watch a 12-second request return in 3 milliseconds you feel like you have got away
with something.

You have. The question is what.

Here is the thing that bothered me. A regular cache has a binary failure mode: the key
matches or it does not. A semantic cache has a *graded* one — it can be a little bit wrong
— and nothing about a wrong answer looks wrong. There is no exception, no 500, no
truncated body. The caller gets a well-formed, confident, complete response to a question
somebody else asked. It looks exactly like a hit, because it is one.

So how often does that happen? Everybody I asked had a feeling about it. Nobody had a
number, and I understood why: getting one requires a large set of questions where you
already know the right answer, which is not a thing most people have lying around.

I had one lying around.

## The instrument

The previous project in this series is a benchmark: 133 questions about royalty accounting,
each with an exact expected answer and a scorer that decides whether a response matches it.
It was built to measure a model. It turns out to be a very good tool for measuring a
*cache*, because it can tell you not just that a hit happened but whether the answer the
caller received was the right one.

The design is almost embarrassingly cheap. Seed a cache with 130 canonical questions and
their known-correct answers. Then take 520 probes and ask each one. Embed everything
exactly once, record the full similarity matrix, and then **replay the cache's admission
decision offline** across the whole threshold range — 0.70 to 0.99, 59 steps. One
embedding pass buys the entire curve; the sweep itself costs nothing at all.

The probes come in two families, and both are the point:

- **Paraphrases** — 390 of them, three per question, each a genuine restatement of a
  question that *is* in the cache. This is the favourable case: there is a right answer to
  find, and a good cache should find it.
- **Novel questions** — each of the 130 canonical questions asked against a cache holding
  the other 129. This is the unfavourable case: the question is not in the cache at all, so
  **every hit is a wrong-source hit by construction.**

The second family is the one that produces the finding, and it is free — it needs no
generated paraphrases at all. That mattered: the headline existed before a dollar was
spent.

## The number

At the threshold most people ship — **0.90**, the value that shows up in tutorials, in
default configs, and in mine — here is what happens to 130 questions the cache has never
seen:

**98 of them get answered. 92 of those answers are provably wrong.**

Not "possibly stale". Not "slightly off". The caller asked one question and received the
correct, complete, well-formatted answer to a different one, and the answer key says so.

Now the other half, because a finding with only its bad news in it is not a finding. On the
same corpus at the same threshold, **389 of 390 genuine paraphrases hit, 382 of them
correctly.** The cache is *excellent* at the job you would test it on. It is the traffic
you did not think to test — the questions nobody has asked before, which is to say most of
them — where it quietly makes things up.

That is the shape worth internalising. A control surface that looks superb on your test set
and poisons your production set is not a control surface with a bug. It is a trap with a
good demo.

## "So raise the threshold"

This is the first thing everybody says, including me, and it is where the measurement earns
its keep. Two numbers:

```
highest similarity at which the cache serves a provably WRONG answer   0.999539
lowest  similarity at which the cache serves a          CORRECT answer 0.889850
```

Those two bands **overlap**, across almost the entire useful range. A threshold is a single
number, and these are two distributions sitting on top of each other. Put the bar above
0.9995 and you have thrown away every legitimate hit along with the bad ones. Put it
anywhere a paraphrase can still land and the worst wrong answer in the corpus lands with
it.

I had pre-registered a rule for picking a recommended threshold before I drew the curve:
the lowest grid point whose wrong-answer count is zero and stays zero above it. **No grid
point qualifies.** Not one, in either embedding space.

## Why — and it is not what you think

The worst pair in the corpus scores **0.999539**. Here it is:

```
asked   Scan every statement for period 2026-02 for reporting anomalies — duplicates,
        unknown ISRCs, currency mismatches, negative units, period bleed, …
served  Scan every statement for period 2026-04 for reporting anomalies — duplicates,
        unknown ISRCs, currency mismatches, negative units, period bleed, …

the caller receives   5 findings, on line ids 80023526, 89000001-4
the true answer is    2 findings, on line ids 100013815, 109000001
```

Two questions differing in one period token. Seven characters.

The instinct is to blame the embedding model — it's too small, it's the wrong one, use a
bigger one. That instinct is exactly backwards, and it is the most important paragraph in
this post.

**The embedding is not broken. It is right.** Those two prompts genuinely are 99.95% the
same text. A similarity model that returned anything else would be the defective one. Ask
it "how similar are these two strings" and 0.9995 is the correct answer.

The failure is in the *use*. We took a measurement of textual similarity and promoted it to
an **admission decision** about semantic equivalence — and the seven characters the
measurement correctly treats as negligible are, for this workload, the entire answer.

And note what a bigger embedding model does here. It makes the two prompts score *closer*,
not further apart, because they really are nearly identical. The failure gets worse with a
better model, which is a strong sign you are not looking at a quality problem.

This is not a property of my benchmark. It is a property of **any application that
templates its prompts** — one question shape crossed with a customer id, an account, a
month, a region, a SKU. Which is most of them. The more industrialised your prompting is,
the more your near-misses look like your paraphrases.

## What would actually work

Not a higher bar. A different mechanism.

Extract the entities and periods from both the incoming question and the candidate entry,
and require them to **match exactly** before a semantic hit is allowed. Similarity decides
*whether these are the same shape of question*; an exact match on the entities decides
*whether they are about the same thing*. It is a filter, not a threshold, and it fails in
the safe direction: an entity you failed to extract costs you a cache miss, which is a
request you were going to make anyway.

I have not shipped it. The gateway that produced this measurement still has a plain cosine
threshold with a documented default of 0.90, and the README now says in as many words that
this default is **not safe for this workload class, and neither is any other value in the
range.** I left the number where it was rather than raising it, because raising it is
precisely the move the finding says does not work, and quietly bumping a constant would
have been a way of pretending I had fixed something.

Building the fix inside the pull request that measured the problem is also how a finding
turns into a pitch. It is named as the first thing the cache should grow, and it is not in
this release.

## The part I did not expect to write about

The 390 paraphrases were generated by a model under a rubric — preserve every entity,
period and figure; vary only surface form — and then checked mechanically: do the entities
survive, do the periods survive, do the figures survive. Belt and braces.

Then I read twenty of them by hand, because a corpus you have not looked at is a corpus you
are trusting.

It failed twice.

The first time, a question that asked for a *rate* **and** a citation came back as a
paraphrase asking only for the citation. A two-part ask silently compressed to one part.
Every mechanical check passed — all the entities were there, all the periods, all the
figures. Making that check mechanical and re-running it over the whole batch turned up
**25 collapsed candidates across 17 questions**. A one-in-three rate, in a batch I had
otherwise been about to measure.

The second time, a question ending `Do not submit a batch.` had become a paraphrase saying
*"submitting them individually rather than as a batch"* — a bare prohibition turned into an
instruction to do the alternative. Same story: mechanically clean, semantically inverted.
The audit found **13 inverted or dropped prohibitions across 7 questions**, the whole
reconciliation family.

Here is the bit that keeps me up. In the second case, I forced a redraw of the failing
probe and got back a clean one — *and the identical inversion in a different probe of the
same question.* If I had simply re-read the twenty I had sampled, I would have approved it.
Neither audit was reachable from the sample. The human clause of that QA chain did not find
the 38 bad probes; it found the **kind**, twice, and the mechanical clause then found the
rest.

Both catches happened before any measurement existed, which was the rule going in: a bad
batch gets regenerated *before* the sweep, never after. The whole saga cost about $0.21
more than a clean single pass would have. I would pay it again without thinking.

## The other half of the repo

The instrument is a real gateway, because the measurement needed a real cache to measure.
[Headroom](https://github.com/sergioavilax/headroom) sits between an application and its
model providers: two dialects, real SSE streaming, virtual keys, per-tenant budgets and
token-bucket rate limits enforced as **single atomic conditional writes**, exact and
semantic caching, provider failover with a circuit breaker that refuses to splice two
providers' answers into one response, and per-request cost attribution with a console.

Two numbers from it, since they are the ones people ask about:

Running the entire 133-question benchmark *through* the extra hop scored **93.7 against
93.3 direct** — a delta of +0.4 against a bound of 3.0 fixed before the run — with
passthrough overhead of **0.0612 ms** at the median. And two completely independent meters
over the same $7.54 of traffic, mine reading the usage block off the stream and the
benchmark's reading it from the SDK, agreed to **twelve decimal places**.

The answer to "isn't Python too slow to put in front of a model" is that the gateway's
whole admission path — authentication, routing, four token buckets, a cache lookup, an
atomic budget reservation — is **0.012% of a request**.

## The thread this closes

The first post in this series was about zeros: a score that was zero because the thing
being measured was broken, and what it took to be sure. The second was about reds: a suite
left failing on purpose, because clearing it would have meant publishing a number off an
input I had just proved was bad.

This one is about the failure that is neither. No zero, no red, no exception, no alert. A
200 with a well-formed body, returned in three milliseconds, answering somebody else's
question — and the only reason I can tell you how often it happens is that I happened to
have 130 questions whose answers I already knew.

Every number in that repo's README is recomputed from a committed artifact by a test on
every pull request. A claim that stops following from its evidence turns the build red. It
is the only defence I know against a front door that was true once, and it is the habit I
would most like somebody to steal from this.

---

## Notes for the operator

- **The one paragraph to edit** is "The thread this closes" — it characterises the first
  two posts from BUILD_PLAN's shorthand rather than from having read them.
- **Images:** `experiments/results/h1_curve.svg` after "The number";
  `docs/evidence/p10-eks/24-live-flip.png` in "The other half of the repo" if the post
  wants one there.
- **Do not add numbers.** If a figure is worth putting in the post it is worth putting in
  the README with its artifact first, where the test can see it.
- **The likely pushback**, in order: *your embedder is too small* (answered in "Why — and it
  is not what you think"), *you should rerank / use k>1* (fair, not run, and named as a
  limitation in REPORT.md — the sweep replays the *shipped* top-1 decision because a k>1
  analysis would describe a gateway nobody runs), and *this is just cache invalidation*
  (no: an invalidation bug serves a stale answer to the right question; this serves a fresh
  answer to the wrong one).

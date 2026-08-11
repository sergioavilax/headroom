# Recruiter follow-up, v2

**What changed from v1:** Kubernetes is on it. The gap analysis that started this project
said the portfolio proved *product* (Backline) and *deployment* (the AWS parity run) but
not *platform*, and named **DynamoDB + Lambda** and **Kubernetes** as the two recurring
listing requirements with nothing behind them. Both are now closed with evidence rather
than with a line on a CV, and this template exists so that fact reaches the threads that
are already open.

Three lengths, because the right one depends on how warm the thread is. All of them link
to one thing.

---

## A — the reply to a live thread (short; this is the one to use)

> Hi <name>,
>
> Quick follow-up on <role / company>: I've just finished the project I mentioned, and it
> closes the two gaps we talked about.
>
> **Headroom** — github.com/sergioavilax/headroom — is an LLM gateway and control plane:
> virtual keys, per-tenant budgets and rate limits enforced on atomic DynamoDB conditional
> writes, exact + semantic response caching, provider failover with a circuit breaker, and
> per-request cost attribution. It's deployed three ways — Docker Compose, ECS Fargate with
> RDS + DynamoDB + a Lambda, and a Helm chart on EKS using those same managed services
> rather than rebuilding them as pods.
>
> The part I'd point at first isn't the gateway though. It's the measurement it exists to
> produce: at the industry-default similarity threshold, a semantic cache answered 98 of
> 130 never-before-seen questions from a neighbouring entry, and 92 of those answers were
> provably wrong. Full curve, committed corpus, and the reason no threshold fixes it are
> in the README.
>
> Happy to walk through any of it.
>
> Sergio

---

## B — the cold-ish reintroduction (medium)

> Hi <name>,
>
> We spoke in <month> about <role>. You mentioned <the thing they cared about>, and I said
> I'd come back when I had something to show rather than describe.
>
> **Headroom** (github.com/sergioavilax/headroom) is an LLM gateway and control plane — the
> layer between an application and its model providers. Two dialects, real SSE streaming,
> virtual keys, per-tenant budgets and token-bucket rate limits, exact + semantic caching,
> provider failover, and per-tenant cost attribution with a live console.
>
> Three things in it are the reason I'm sending it rather than the feature list:
>
> **1. The budget gate and the rate limiter are single atomic conditional writes**, and the
> test suite races them with 64 concurrent requests on every pull request — beside two
> deliberately broken implementations that *must* fail the same test. One of those broken
> versions is perfectly atomic and still lets 7.6× the budget through, because it compares
> the wrong number. Atomicity is necessary and it is not sufficient, and that is asserted
> rather than asserted-about.
>
> **2. It answers "what does the extra hop cost" with a number.** The full 133-question
> benchmark re-run through the gateway scored 93.7 against 93.3 direct — inside a bound
> fixed before the run — with passthrough overhead of 0.0612 ms at the median, and two
> independent meters over the same $7.54 of traffic agreeing to twelve decimal places.
>
> **3. It ships a finding.** At the default 0.90 similarity threshold, its own semantic
> cache answered 98 of 130 never-before-seen questions from a neighbouring entry and 92 of
> those answers were provably wrong — and no threshold in the range fixes it, because the
> closest wrong answer scores 0.9995 and the furthest correct one 0.8899. Both curves, the
> corpus, and the mechanism are committed.
>
> Deployment: Docker Compose locally, ECS Fargate + RDS + DynamoDB + one Lambda on AWS
> (applied, smoked, destroyed the same day), and a Helm chart on EKS where a rolling
> upgrade was measured to zero dropped requests — after two runs that read one and two,
> both kept, because the zero only means something if the instrument could have said
> otherwise.
>
> Every number in the README is recomputed from a committed artifact by a test. Happy to
> talk through any part of it, or to walk it end to end on a call.
>
> Sergio

---

## C — the one-liner, for a DM or a form field

> LLM gateway + control plane, deployed on ECS and on EKS, that ships the measurement
> almost nobody publishes: at the default threshold a semantic cache answered 98 of 130
> unseen questions from the wrong entry, 92 of them provably wrong.
> github.com/sergioavilax/headroom

---

## What to say when they ask

**"Is it in production?"** No — one operator, one network, and the cloud phases were
applied, measured, and destroyed the same day on purpose. What it has instead is evidence
in the repo for every claim, and a Limits section that says exactly what one run per
experiment row does and does not support. That is a more useful thing to review than a
staging URL.

**"How long did it take?"** About thirty-five focused hours across eleven phases, each with
a written gate, a PR, and a log entry with the gate's output verbatim. The plan was
committed before the code.

**"Kubernetes — how deep?"** A Helm chart with a values schema that refuses unknown keys
and two render-time guards (it will not publish an admin API to `0.0.0.0/0`), a migration
pre-install hook, an IRSA role asserted to grant exactly what the ECS task role granted and
nothing more, node-capacity arithmetic in the test suite so a pod cannot silently sit
`Pending` — and a real cluster window that found a real bug: a rolling upgrade dropping one
in-flight request per replaced pod, diagnosed as a keep-alive race that no amount of
`preStop` sleep can reach, fixed with a lame-duck drain, and verified back to zero.

**"What would you do differently?"** Run a same-day paired control for the parity
experiment. It is named in the README as the single highest-value follow-up in the repo,
and it is missing for a stated reason — it would have cost another $8 against a $10 line.

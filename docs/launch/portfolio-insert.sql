-- Headroom — the project row for sergioavi.la.
--
-- ── READ THIS BEFORE RUNNING IT ────────────────────────────────────────────────────
-- The portfolio's schema does not live in this repo and is not referenced by any
-- document in it, so the column list below is a **stated assumption**, not a copy of a
-- known table. Check it before running:
--
--     \d projects
--
-- and adjust the column names. The values are the part that matters; every figure in
-- them is one the README carries and `tests/test_docs.py` recomputes from a committed
-- artifact, so nothing here needs re-deriving — but nothing here is checked by this
-- repo either, because a database in another project is outside every test's reach.
--
-- Written as an UPSERT on `slug` so re-running it after an edit updates the row rather
-- than creating a second one. If `slug` is not unique in your schema, add the
-- constraint before running this, or turn it into a plain UPDATE.
-- ───────────────────────────────────────────────────────────────────────────────────

INSERT INTO projects (
    slug,
    title,
    tagline,
    summary,
    body,
    tech,
    repo_url,
    live_url,
    featured,
    sort_order,
    published_at
) VALUES (
    'headroom',
    'Headroom',
    'An LLM gateway and control plane — and the measurement almost nobody publishes.',

    -- Card summary. Two sentences: what it is, and the one number that makes somebody
    -- click. The finding leads, because the gateway is the ordinary half.
    'A production-shaped LLM gateway: virtual keys, per-tenant budgets and rate limits '
    'enforced on atomic DynamoDB conditional writes, exact + semantic response caching, '
    'provider failover with a circuit breaker, and per-request cost attribution. Built to '
    'answer a question the industry argues about by vibes — at the default 0.90 similarity '
    'threshold, a semantic cache answered 98 of 130 never-before-seen questions from a '
    'neighbouring entry, and 92 of those answers were provably wrong.',

    -- Long body, markdown. Kept to the four things worth a reader's time.
    E'## The finding\n\n'
    E'Everyone ships semantic caching; almost nobody measures how often it silently '
    E'returns the wrong answer, because measuring that needs a large question set with '
    E'exact ground truth. [Backline](https://github.com/sergioavilax/backline) is one, so '
    E'Headroom points it at its own cache: 130 answer-keyed questions seeded, 520 probes '
    E'embedded once, and the admission decision replayed offline across every threshold '
    E'from 0.70 to 0.99.\n\n'
    E'At the industry-default **0.90**, one hit in five is a wrong answer served with '
    E'confidence. And no threshold fixes it: the closest **wrong** answer scores '
    E'**0.999539** and the furthest **correct** one scores **0.889850**, so the two bands '
    E'overlap and a single cosine number cannot separate them. The worst pair is two '
    E'questions differing in one date token — `2026-02` against `2026-04` — with entirely '
    E'different answers.\n\n'
    E'The embedding is not broken. It is right. The failure is in using a similarity score '
    E'as an *admission decision*, when the seven characters it correctly calls negligible '
    E'are the entire answer. That is a property of every application that templates its '
    E'prompts.\n\n'
    E'## The gateway that measured it\n\n'
    E'Two dialects, real SSE streaming, and one pipeline: authenticate → scope → route → '
    E'rate limit → cache → budget gate → failover → passthrough → meter. The budget gate '
    E'and the token buckets are single atomic conditional writes, raced by a 64-request '
    E'stampede on every pull request — beside two deliberately broken implementations that '
    E'must fail the same test, because atomicity is necessary and it is not sufficient.\n\n'
    E'## What it costs to stand in front of a model\n\n'
    E'The full 133-question suite re-run *through* the gateway scored **93.7 against 93.3 '
    E'direct** — Δ +0.4 against a bound of 3.0 fixed before the run. Passthrough overhead '
    E'p50 **0.0612 ms**; **0.0249 ms** on ECS Fargate behind an ALB and **0.0175 ms** on '
    E'EKS behind an NLB. Two independent meters over one $7.54 run agreed to twelve decimal '
    E'places.\n\n'
    E'## Deployed, measured, and torn down\n\n'
    E'ECS Fargate + RDS + real DynamoDB + one Lambda on AWS, then a Helm chart on EKS using '
    E'those same managed services rather than rebuilding them as pods. A rolling '
    E'`helm upgrade` under load was measured to **zero dropped requests** — after two runs '
    E'that read one and two, both committed, because the zero only means something if the '
    E'instrument could have said otherwise. The cluster is gone; the evidence is in the '
    E'repo.\n\n'
    E'**Every number in the README is recomputed from a committed artifact by a test on '
    E'every pull request.** A claim that no longer follows from its evidence turns the '
    E'build red.',

    -- Stack. Adjust to text[] / jsonb / a join table as your schema requires.
    ARRAY[
        'Python 3.12', 'FastAPI', 'asyncio', 'PostgreSQL 16', 'pgvector', 'DynamoDB',
        'Next.js', 'TypeScript', 'Docker', 'Terraform', 'AWS ECS Fargate', 'AWS Lambda',
        'Kubernetes', 'EKS', 'Helm', 'vLLM', 'sentence-transformers'
    ],

    'https://github.com/sergioavilax/headroom',
    NULL,          -- nothing is hosted: the cloud phases were applied, measured, destroyed
    TRUE,
    10,            -- ahead of Backline if this is the one to lead with
    NOW()
)
ON CONFLICT (slug) DO UPDATE SET
    title        = EXCLUDED.title,
    tagline      = EXCLUDED.tagline,
    summary      = EXCLUDED.summary,
    body         = EXCLUDED.body,
    tech         = EXCLUDED.tech,
    repo_url     = EXCLUDED.repo_url,
    live_url     = EXCLUDED.live_url,
    featured     = EXCLUDED.featured,
    sort_order   = EXCLUDED.sort_order,
    updated_at   = NOW();

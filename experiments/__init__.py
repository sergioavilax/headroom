"""Phase 8 — the three pre-registered experiments (BUILD_PLAN §P8).

**No gateway features live here.** The machinery finished at Phase 7; this package runs
the experiments through it and produces the project's headline findings. Three rules
govern every module below, and they are why the layout looks the way it does.

**Pre-registration is law.** `experiments/PRE_REGISTRATION.md` was committed before any
of this code could produce a number, and it fixes every metric definition, bound, and
analysis choice that BUILD_PLAN §P8 left open. Code here implements that document; where
the two could differ, the document wins and the code has a bug.

**Money is operator-run.** Exactly one module in this package can spend
(`h1/generate.py`), it carries its own hard dollar stop, and it is never invoked by
Claude Code or by the test suite. Everything else is free: the sweeps are arithmetic over
committed artifacts, the H2 analysis reads a ledger that already exists, and H3's mock
half runs on the MockProvider.

**Artifacts are golden and hashed.** An experiment that cannot be re-run from committed
inputs is an anecdote. The expensive, non-reproducible steps — paraphrase generation
(paid), embedding (needs the `embed` extra), answer-equivalence (needs Backline on disk)
— all happen once, on a machine that has what they need, and commit their output with a
content hash and a provenance block. The measurement steps then replay that artifact
keylessly, with no torch, no Backline, and no network, which is what puts them in CI.
That pattern is not invented here: it is `tests/support/build_semantic_corpus.py` from
Phase 5, scaled up.

Layout::

    artifacts.py   stable JSON, content hashing, provenance stamping
    h1/            the semantic-cache safety curve — the headline
    h2/            Backline's suite through the gateway — overhead and parity
    h3/            failover under load — the chaos suite and the live kill
    artifacts/     golden inputs (hashed, committed)
    results/       what the sweeps and analysers produced (committed)
"""

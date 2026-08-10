"""H1 — the semantic-cache safety curve (BUILD_PLAN §P8.H1).

> everyone ships semantic caching; almost nobody measures how often it silently returns
> the wrong answer, because measuring that requires a large question set with exact
> ground truth. Backline is one.

The pipeline, and which parts cost what::

    suite.py       read Backline's answer-keyed questions          free, no deps
    rubric.py      the preserve-every-entity paraphrase rubric     —
    checks.py      mechanical survival checks on a candidate       free, no deps
    generate.py    paraphrases from claude-haiku-4-5               PAID, ~$1, operator-run
    build.py       equivalence + embeddings -> the golden artifact free, needs Backline + `embed`
    corpus.py      read the golden artifact back                   free, keyless
    sweep.py       replay the admission decision over a grid       free, keyless
    figure.py      the curve, in the console's design language     free, keyless

Everything from ``corpus.py`` rightwards runs in CI against the committed artifact, with
no torch, no Backline, and no network. That division is the whole reason the sweep is
free and the finding is reproducible.
"""

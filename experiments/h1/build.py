"""Assemble the golden artifact: texts, provenance, answer-equivalence, vectors.

Free, and run wherever the two heavy dependencies live — Backline on disk (for its own
scorer) and the ``embed`` extra (for `bge-small-en-v1.5`). Everything downstream of this
file runs in CI with neither::

    BACKLINE_REPO=~/code/backline PYTHONPATH=~/code/backline \\
      uv run --extra embed python -m experiments.h1.build

Two outputs, split on purpose. `h1_corpus.json` carries the texts, the provenance, the
equivalence matrix and the spot-check block — it is meant to be *read*, and it is what the
content hash covers. `h1_vectors.json` carries 384 floats per text and is meant to be
consumed; it names the corpus hash it belongs to, so the pair cannot drift apart.

**The diagonal is checked, not assumed.** Every rendered answer is scored by Backline's own
`score_t1` against its own question, and a single failure aborts the build. If ground truth
does not satisfy the scorer, then "provably wrong for the asked question" means nothing and
no number produced downstream is worth reading (H-061).

**Two embedding spaces, both built here** (H-060): ``prompt`` is the text as Backline sends
it and is the primary; ``body`` is the question with the answer-format tail removed and is
the pre-registered sensitivity check.

**The build refuses an incomplete paraphrase batch.** A question with two paraphrases where
the rest have three quietly reweights the corpus, so `unresolved` must be empty and every
committed candidate is re-checked here rather than trusted from the generation run. It also
refuses a batch drawn under a different `RUBRIC_VERSION` (H-070): the corpus carries the
rubric block as provenance, and a corpus whose probes were drawn under a rubric nobody
approved is provenance that says the wrong thing.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any, Final

from experiments.h1 import rubric
from experiments.h1.checks import check_batch
from experiments.h1.suite import Suite, backline_repo, load_suite, render_answer
from experiments.provenance import (
    ARTIFACTS_DIR,
    content_hash,
    git_sha,
    provenance,
    read_json,
    write_json,
)

__all__ = [
    "CORPUS_PATH",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_MODEL",
    "FLOAT_PLACES",
    "VECTORS_PATH",
    "corpus_material",
    "main",
]

#: BUILD_PLAN L6, and the same model the Phase 5 fixture used — so the two corpora are
#: measured in one space and the P5 numbers remain comparable.
EMBEDDING_MODEL: Final = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS: Final = 384
#: The Phase 5 convention: six places moves a cosine by well under 1e-5, four orders below
#: the 0.005 grid step any threshold decision is made on, and it keeps the file a diff.
FLOAT_PLACES: Final = 6

CORPUS_PATH: Final = ARTIFACTS_DIR / "h1_corpus.json"
VECTORS_PATH: Final = ARTIFACTS_DIR / "h1_vectors.json"
PARAPHRASES_PATH: Final = ARTIFACTS_DIR / "h1_paraphrases.json"

CORPUS_SCHEMA: Final = "h1_corpus/1"
VECTORS_SCHEMA: Final = "h1_vectors/1"

#: What the sweep can do with this artifact. ``family_b_only`` is a complete, publishable
#: state: Family B needs no paraphrases (H-062), so the hard-negative half of the curve
#: exists before a dollar is spent.
STAGE_FAMILY_B: Final = "family_b_only"
STAGE_COMPLETE: Final = "complete"


def corpus_material(payload: dict[str, Any]) -> dict[str, Any]:
    """What :func:`~experiments.artifacts.content_hash` is taken over.

    Texts, provenance-of-meaning, and the equivalence matrix — never the vectors and never
    the timestamps. Hashing vectors would make a rounding change look like a corpus change;
    hashing these pins what a reader has to trust: that every probe still names the question
    it paraphrases, and that "wrong" still means what Backline's scorer said it meant.
    """
    return {
        "suite": payload["suite"],
        "questions": [
            {key: row[key] for key in ("id", "body", "tail", "answer_text", "answer_kind")}
            for row in payload["questions"]
        ],
        "probes": [
            {key: row[key] for key in ("id", "source", "body")} for row in payload["probes"]
        ],
        "equivalence": payload["equivalence"],
        "rubric": payload["rubric"],
    }


def _load_paraphrases(
    path: Path, suite: Suite
) -> tuple[dict[str, list[str]], dict[str, Any] | None]:
    if not path.exists():
        return {}, None
    payload = read_json(path)
    version = (payload.get("rubric") or {}).get("version")
    if version != rubric.RUBRIC_VERSION:
        # The generator drops a superseded batch on load, so reaching here means the file
        # predates a rubric bump that has not been run yet. Belt and braces: the corpus
        # carries the rubric block as provenance, and a corpus whose probes were drawn under
        # a rubric the operator did not approve is provenance that says the wrong thing.
        raise SystemExit(
            f"{path} was generated under rubric version {version}; the rubric is now "
            f"version {rubric.RUBRIC_VERSION} (H-070). A batch is never mixed across "
            f"versions and a corpus is never built from a superseded one. Regenerate:\n"
            f"  uv run python -m experiments.h1.generate --dry-run"
        )
    if payload.get("unresolved"):
        ids = ", ".join(row["id"] for row in payload["unresolved"])
        raise SystemExit(
            f"{path} has {len(payload['unresolved'])} unresolved question(s): {ids}\n"
            f"Re-run the generator for those ids before building — a corpus with uneven "
            f"paraphrase counts reweights itself toward whatever the model found easy:\n"
            f"  uv run python -m experiments.h1.generate --only {ids.replace(', ', ',')}"
        )

    rows: dict[str, list[str]] = {}
    for question in suite.questions:
        entry = payload["questions"].get(question.id)
        if entry is None:
            raise SystemExit(f"{path} has no paraphrases for {question.id}")
        candidates = list(entry["paraphrases"])
        if len(candidates) != rubric.PARAPHRASES_PER_QUESTION:
            raise SystemExit(
                f"{question.id}: {len(candidates)} paraphrases, expected "
                f"{rubric.PARAPHRASES_PER_QUESTION}"
            )
        # Re-checked here rather than trusted: the artifact is built from candidates that
        # pass *now*, against the checks as they are committed now.
        bad = {
            position: result
            for position, result in check_batch(body=question.body, candidates=candidates).items()
            if not result.ok
        }
        if bad:
            detail = "; ".join(
                f"#{position + 1}: {', '.join(result.failures)}"
                for position, result in sorted(bad.items())
            )
            raise SystemExit(f"{question.id}: committed paraphrase fails the checks — {detail}")
        rows[question.id] = candidates
    return rows, payload.get("rubric")


def _equivalence(suite: Suite) -> list[str]:
    """``rows[i][j] == '1'`` iff serving entry *j*'s answer to question *i* passes T1.

    Backline's own scorer, over its own `Question` objects, so the per-kind parsing, the
    money tolerances, the set semantics and the abstention protocol are all its arithmetic
    rather than a re-implementation (H-061).
    """
    sys.path.insert(0, str(backline_repo()))
    try:
        from evals.scoring import AnswerOutcome, score_t1
        from evals.types import load_suite as load_backline_suite
    except ImportError as error:  # pragma: no cover - operator-environment guard
        raise SystemExit(
            f"cannot import Backline's scorer ({error}). The equivalence matrix is its "
            f"arithmetic by decision (H-061), so the build needs the sibling repo on "
            f"PYTHONPATH:\n  PYTHONPATH=$(pwd)/../backline BACKLINE_REPO=$(pwd)/../backline …"
        ) from error

    backline = load_backline_suite("core")
    by_id = {question.id: question for question in backline.questions}
    served = [
        (question, render_answer(question), question.abstains) for question in suite.questions
    ]

    rows: list[str] = []
    for asked in suite.questions:
        question = by_id[asked.id]
        bits = [
            "1" if score_t1(question, AnswerOutcome(text=text, abstained=abstains)).passed else "0"
            for _, text, abstains in served
        ]
        rows.append("".join(bits))

    for index, asked in enumerate(suite.questions):
        if rows[index][index] != "1":
            raise SystemExit(
                f"{asked.id}: its own rendered answer does not score 1.0 through Backline's "
                f"scorer. Ground truth must satisfy the scorer or 'provably wrong' means "
                f"nothing (H-061). Rendered: {render_answer(asked)!r}"
            )
    return rows


def _embed(texts: list[str]) -> list[list[float]]:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:  # pragma: no cover - operator-environment guard
        raise SystemExit(
            f"cannot import sentence-transformers ({error}). Run the build with the extra:\n"
            f"  uv run --extra embed python -m experiments.h1.build"
        ) from error

    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    vectors = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    if vectors.shape[1] != EMBEDDING_DIMENSIONS:
        raise SystemExit(f"expected {EMBEDDING_DIMENSIONS} dimensions, got {vectors.shape[1]}")
    return [[round(float(value), FLOAT_PLACES) for value in row] for row in vectors]


def _probe_rows(suite: Suite, paraphrases: dict[str, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for question in suite.questions:
        for index, body in enumerate(paraphrases.get(question.id, []), start=1):
            rows.append(
                {
                    "id": f"{question.id}#p{index}",
                    # Provenance: the one entry this probe may correctly resolve to. It is
                    # the answer key for cache correctness (BUILD_PLAN §P8.H1).
                    "source": question.id,
                    "category": question.category,
                    "answer_kind": question.answer_kind,
                    "body": body,
                    # H-060: the tail is re-attached, never paraphrased.
                    "prompt": f"{body}\n\n{question.tail}",
                }
            )
    return rows


def build(
    *, suite: Suite, paraphrases: dict[str, list[str]], rubric_block: dict[str, Any] | None
) -> None:
    question_rows = [
        {
            "id": question.id,
            "category": question.category,
            "agent": question.agent,
            "answer_kind": question.answer_kind,
            "body": question.body,
            "tail": question.tail,
            "prompt": question.prompt,
            "answer_text": render_answer(question),
        }
        for question in suite.questions
    ]
    probe_rows = _probe_rows(suite, paraphrases)
    stage = STAGE_COMPLETE if paraphrases else STAGE_FAMILY_B

    print(f"equivalence: {len(suite.questions)}² pairs through Backline's own scorer…", flush=True)
    equivalence = _equivalence(suite)

    sample_pool = [row["id"] for row in probe_rows]
    sample = sorted(
        random.Random(rubric.SPOT_CHECK_SEED).sample(
            sample_pool, min(rubric.SPOT_CHECK_SAMPLE, len(sample_pool))
        )
    )

    payload: dict[str, Any] = {
        "schema": CORPUS_SCHEMA,
        "stage": stage,
        "provenance": provenance(
            produced_by="experiments/h1/build.py",
            inputs={
                "suite_hash": suite.suite_hash,
                "backline_commit": git_sha(backline_repo()),
                "paraphrases": str(PARAPHRASES_PATH.name) if paraphrases else None,
            },
            notes=(
                "Free to rebuild given Backline on disk and the `embed` extra. The sweep "
                "reads this file and needs neither."
            ),
        ),
        "suite": {
            "name": suite.name,
            "suite_hash": suite.suite_hash,
            "world_seed": suite.world_seed,
            "questions": len(suite.questions),
            "excluded": [list(row) for row in suite.excluded],
        },
        "embedding": {
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIMENSIONS,
            "normalized": True,
            "float_places": FLOAT_PLACES,
            "spaces": ["prompt", "body"],
        },
        "rubric": rubric_block,
        "spot_check": {
            "seed": rubric.SPOT_CHECK_SEED,
            "size": rubric.SPOT_CHECK_SAMPLE,
            "sample": sample,
            # Filled by the operator, through the runbook. The sweep does not read it; the
            # report does, and REPORT.md states it as approved or as outstanding.
            "approved_by": None,
            "approved_at": None,
        },
        "questions": question_rows,
        "probes": probe_rows,
        "equivalence": equivalence,
    }
    payload["corpus_hash"] = content_hash(corpus_material(payload))
    write_json(CORPUS_PATH, payload)

    print(f"embedding {2 * (len(question_rows) + len(probe_rows))} texts on CPU…", flush=True)
    spaces: dict[str, dict[str, list[float]]] = {}
    for space, key in (("prompt", "prompt"), ("body", "body")):
        rows = [*question_rows, *probe_rows]
        vectors = _embed([str(row[key]) for row in rows])
        spaces[space] = {str(row["id"]): vector for row, vector in zip(rows, vectors, strict=True)}

    write_json(
        VECTORS_PATH,
        {
            "schema": VECTORS_SCHEMA,
            # Binds the two files: a vectors file whose corpus hash does not match the
            # corpus it sits beside is stale, and the loader refuses it.
            "corpus_hash": payload["corpus_hash"],
            "embedding": payload["embedding"],
            "provenance": provenance(produced_by="experiments/h1/build.py"),
            "spaces": spaces,
        },
    )

    print(
        f"\nstage: {stage}\n"
        f"  {len(question_rows)} questions ({len(suite.excluded)} excluded), "
        f"{len(probe_rows)} probes\n"
        f"  corpus_hash {payload['corpus_hash']}\n"
        f"  wrote {CORPUS_PATH.name} and {VECTORS_PATH.name}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h1.build",
        description="Assemble H1's golden artifact. Needs Backline and the embed extra.",
    )
    parser.add_argument(
        "--no-paraphrases",
        action="store_true",
        help="build the Family-B-only corpus (no paid step needed; H-062)",
    )
    parser.add_argument("--paraphrases", default=str(PARAPHRASES_PATH))
    args = parser.parse_args(argv)

    suite = load_suite()
    if args.no_paraphrases:
        paraphrases: dict[str, list[str]] = {}
        rubric_block: dict[str, Any] | None = None
    else:
        paraphrases, rubric_block = _load_paraphrases(Path(args.paraphrases), suite)
    build(suite=suite, paraphrases=paraphrases, rubric_block=rubric_block)
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())

"""Generate ``tests/fixtures/semantic_corpus.json`` — the keyless semantic fixture.

**Why this file exists.** The semantic cache's whole safety case is a claim about
*similarity*: that a paraphrase of a cached question resolves to it and a near-miss does
not. Asserting that requires real embeddings from the real model (BUILD_PLAN L6:
``BAAI/bge-small-en-v1.5``, CPU), and CI is deliberately keyless *and* torch-free —
H-004 does not install the ``embed`` extra, because a 200 MB download per job buys
nothing on ninety-nine jobs out of a hundred.

So the vectors are computed **once, here, on a machine that has the extra**, and
committed as a content-hashed artifact. CI then replays the similarity arithmetic
against real numbers with no model, no torch, and no network. That is the
``experiments/`` discipline of BUILD_PLAN §P8.H1 in miniature, and deliberately so: the
corpus is a golden artifact, every probe knows its source question, and the provenance
*is* the answer key for cache correctness.

**The corpus shape is H1's, scaled down.** Questions are built from templates crossed
with entities, exactly as Backline's suite is, which is what makes the dangerous
collision class ship with the corpus for free: two questions from the same template with
different artists are each other's near-misses, and no separate "hard negative" has to be
invented (or, worse, hand-tuned until the test passes).

Run it with the extra installed::

    uv sync --extra embed
    uv run --no-sync python -m tests.support.build_semantic_corpus

It is **not** run by the test suite. The suite reads the committed JSON and asserts its
content hash, so a regenerated corpus is a deliberate, reviewable diff rather than a
silent change to what the safety tests mean.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.support.corpus import (
    CORPUS_PATH,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    FLOAT_PLACES,
    corpus_hash,
)

# --- the corpus source of truth -------------------------------------------------------
#
# Four templates crossed with three artists. Every question is answerable from a
# fictional answer key, and the answers differ per artist — which is what makes a
# cross-artist hit a *provably wrong* answer rather than merely a suspicious one.

TEMPLATES: list[dict[str, str]] = [
    {
        "id": "streaming_revenue",
        "question": "What was {artist}'s total streaming revenue in 2019?",
        # Paraphrases preserve every entity, period, and figure; only surface form moves.
        "paraphrase_1": "In 2019, how much streaming revenue did {artist} bring in overall?",
        "paraphrase_2": "Could you tell me {artist}'s overall 2019 revenue from streaming?",
    },
    {
        "id": "monthly_listeners",
        "question": "How many monthly listeners did {artist} average in 2019?",
        "paraphrase_1": "What was {artist}'s average monthly listener count during 2019?",
        "paraphrase_2": "On average, how many people listened to {artist} each month in 2019?",
    },
    {
        "id": "highest_charting",
        "question": "Which {artist} album charted highest in 2019?",
        "paraphrase_1": "What was the highest-charting {artist} album of 2019?",
        "paraphrase_2": "In 2019, which record by {artist} reached the best chart position?",
    },
    {
        "id": "royalty_rate",
        "question": "What royalty rate did {artist} negotiate in 2019?",
        "paraphrase_1": "What was the royalty rate {artist} agreed to back in 2019?",
        "paraphrase_2": "In 2019, what rate of royalties did {artist} secure?",
    },
]

ARTISTS: list[dict[str, Any]] = [
    {
        "id": "radiohead",
        "name": "Radiohead",
        "answers": {
            "streaming_revenue": "$14.2 million",
            "monthly_listeners": "11.4 million",
            "highest_charting": "OK Computer OKNOTOK, at number 12",
            "royalty_rate": "18.5 percent",
        },
    },
    {
        "id": "coldplay",
        "name": "Coldplay",
        "answers": {
            "streaming_revenue": "$31.8 million",
            "monthly_listeners": "42.7 million",
            "highest_charting": "Everyday Life, at number 1",
            "royalty_rate": "22.0 percent",
        },
    },
    {
        "id": "portishead",
        "name": "Portishead",
        "answers": {
            "streaming_revenue": "$2.6 million",
            "monthly_listeners": "3.1 million",
            "highest_charting": "Dummy, at number 74",
            "royalty_rate": "15.75 percent",
        },
    },
]


def build_texts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The canonical questions and the probes, each probe naming its source question."""
    questions: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    for template in TEMPLATES:
        for artist in ARTISTS:
            question_id = f"{template['id']}:{artist['id']}"
            questions.append(
                {
                    "id": question_id,
                    "template": template["id"],
                    "artist": artist["id"],
                    "text": template["question"].format(artist=artist["name"]),
                    # The answer key. A hit that resolves to a different question returns
                    # this string for a question it does not answer — which is exactly
                    # the silent-wrong-answer event §P8.H1 counts.
                    "answer": artist["answers"][template["id"]],
                }
            )
            for index in (1, 2):
                probes.append(
                    {
                        "id": f"{question_id}#p{index}",
                        # Provenance: the question this probe is a paraphrase of, and
                        # therefore the only cache entry it may correctly resolve to.
                        "source": question_id,
                        "template": template["id"],
                        "artist": artist["id"],
                        "text": template[f"paraphrase_{index}"].format(artist=artist["name"]),
                    }
                )
    return questions, probes


def main() -> None:
    # Imported here rather than at module scope so that `mypy --strict` and a plain
    # `python -c "import tests.support.build_semantic_corpus"` both work without the
    # extra installed. Only running the generator needs torch.
    from sentence_transformers import SentenceTransformer

    questions, probes = build_texts()
    rows = questions + probes
    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    vectors = model.encode([row["text"] for row in rows], normalize_embeddings=True, batch_size=16)
    if vectors.shape[1] != EMBEDDING_DIMENSIONS:  # pragma: no cover - generator guard
        raise SystemExit(f"expected {EMBEDDING_DIMENSIONS} dimensions, got {vectors.shape[1]}")

    for row, vector in zip(rows, vectors, strict=True):
        # Rounded to six places: the file is a reviewable diff, and six places moves a
        # cosine by well under 1e-5 — four orders of magnitude below any threshold
        # decision this fixture is used to make.
        row["vector"] = [round(float(value), FLOAT_PLACES) for value in vector]

    payload = {
        "embedding_model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "normalized": True,
        "float_places": FLOAT_PLACES,
        "generated_by": "tests/support/build_semantic_corpus.py",
        "questions": questions,
        "probes": probes,
    }
    payload["corpus_hash"] = corpus_hash(payload)
    Path(CORPUS_PATH).write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {CORPUS_PATH} — {len(rows)} texts, corpus_hash={payload['corpus_hash']}")


if __name__ == "__main__":  # pragma: no cover - a generator, not a test
    main()

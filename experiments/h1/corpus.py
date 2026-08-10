"""Reading the golden artifact back — keyless, torch-free, Backline-free.

Everything expensive happened in `build.py`. This module and everything downstream of it
run in CI on a machine with no model, no sibling repo, and no network, which is what makes
H1's finding reproducible by a stranger with a clone.

Two integrity checks live here rather than in a test, because a corrupt corpus should fail
where it is *read* and not only where somebody remembered to assert:

* the vectors file names the corpus hash it was built beside, and a mismatch raises rather
  than quietly measuring one corpus with another's geometry;
* the equivalence matrix is square, sized to the questions, and has a true diagonal — the
  property that makes "provably wrong" mean anything (H-061).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from experiments.artifacts import content_hash, read_json
from experiments.h1.build import CORPUS_PATH, VECTORS_PATH, corpus_material

__all__ = [
    "SPACES",
    "CorpusProbe",
    "CorpusQuestion",
    "H1Corpus",
    "load_corpus",
    "load_vectors",
    "stored_hash_matches",
]

#: H-060's two embeddings. ``prompt`` is what the gateway would really embed and is the
#: primary; ``body`` strips the answer-format tail and is the sensitivity check.
SPACES: Final = ("prompt", "body")


@dataclass(frozen=True, slots=True)
class CorpusQuestion:
    """A seeded cache entry: the question, and the ground-truth answer it holds."""

    id: str
    category: str
    agent: str
    answer_kind: str
    body: str
    tail: str
    prompt: str
    answer_text: str

    def text(self, space: str) -> str:
        return self.prompt if space == "prompt" else self.body


@dataclass(frozen=True, slots=True)
class CorpusProbe:
    """A paraphrase, and the one entry it may correctly resolve to."""

    id: str
    source: str
    category: str
    answer_kind: str
    body: str
    prompt: str

    def text(self, space: str) -> str:
        return self.prompt if space == "prompt" else self.body


@dataclass(frozen=True, slots=True)
class H1Corpus:
    stage: str
    corpus_hash: str
    suite_hash: str
    world_seed: int
    #: ``(question id, why)`` — amendment A1's three, carried so the exclusion is visible
    #: in the artifact rather than only in the document that decided it.
    excluded: tuple[tuple[str, str], ...]
    embedding_model: str
    dimensions: int
    questions: tuple[CorpusQuestion, ...]
    probes: tuple[CorpusProbe, ...]
    #: Row *i*, column *j*: serving entry *j*'s answer to question *i* passes Backline's T1
    #: scorer. Not symmetric — a tolerance or an abstention protocol makes direction matter.
    equivalence: tuple[str, ...]
    spot_check: dict[str, Any]
    rubric: dict[str, Any] | None

    @property
    def index(self) -> dict[str, int]:
        return {question.id: position for position, question in enumerate(self.questions)}

    def equivalent(self, *, asked: str, served: str) -> bool:
        """Would serving ``served``'s stored answer pass for ``asked``?"""
        index = self.index
        return self.equivalence[index[asked]][index[served]] == "1"

    def question(self, question_id: str) -> CorpusQuestion:
        for question in self.questions:
            if question.id == question_id:
                return question
        raise KeyError(question_id)


def load_corpus(path: Path = CORPUS_PATH) -> H1Corpus:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Build it with `python -m experiments.h1.build` "
            f"(add --no-paraphrases for the free Family-B-only corpus)."
        )
    payload = read_json(path)
    questions = tuple(
        CorpusQuestion(
            id=row["id"],
            category=row["category"],
            agent=row["agent"],
            answer_kind=row["answer_kind"],
            body=row["body"],
            tail=row["tail"],
            prompt=row["prompt"],
            answer_text=row["answer_text"],
        )
        for row in payload["questions"]
    )
    probes = tuple(
        CorpusProbe(
            id=row["id"],
            source=row["source"],
            category=row["category"],
            answer_kind=row["answer_kind"],
            body=row["body"],
            prompt=row["prompt"],
        )
        for row in payload["probes"]
    )
    equivalence = tuple(payload["equivalence"])

    size = len(questions)
    if len(equivalence) != size or any(len(row) != size for row in equivalence):
        raise ValueError(f"equivalence matrix is not {size}x{size}")
    if any(equivalence[position][position] != "1" for position in range(size)):
        raise ValueError("equivalence diagonal is not all true — ground truth fails its own scorer")

    return H1Corpus(
        stage=payload["stage"],
        corpus_hash=payload["corpus_hash"],
        suite_hash=payload["suite"]["suite_hash"],
        world_seed=int(payload["suite"]["world_seed"]),
        excluded=tuple((row[0], row[1]) for row in payload["suite"]["excluded"]),
        embedding_model=payload["embedding"]["model"],
        dimensions=int(payload["embedding"]["dimensions"]),
        questions=questions,
        probes=probes,
        equivalence=equivalence,
        spot_check=dict(payload["spot_check"]),
        rubric=payload["rubric"],
    )


def load_vectors(
    corpus: H1Corpus, path: Path = VECTORS_PATH
) -> dict[str, dict[str, tuple[float, ...]]]:
    """``space -> text id -> vector``, refusing a vectors file from another corpus."""
    payload = read_json(path)
    if payload["corpus_hash"] != corpus.corpus_hash:
        raise ValueError(
            f"{path.name} was built for corpus {payload['corpus_hash'][:12]} but the corpus "
            f"here is {corpus.corpus_hash[:12]} — rebuild both with `experiments.h1.build`"
        )
    spaces: dict[str, dict[str, tuple[float, ...]]] = {}
    for space in SPACES:
        spaces[space] = {
            text_id: tuple(values) for text_id, values in payload["spaces"][space].items()
        }
    expected = {question.id for question in corpus.questions} | {
        probe.id for probe in corpus.probes
    }
    for space, vectors in spaces.items():
        missing = expected - set(vectors)
        if missing:
            raise ValueError(f"space {space!r} is missing {len(missing)} vector(s)")
    return spaces


def stored_hash_matches(path: Path = CORPUS_PATH) -> bool:
    """Does the artifact's own ``corpus_hash`` still describe its contents?

    The check a hand-edit fails. Deliberately *not* a pinned literal in a test: this corpus
    is regenerated by a documented operator step (the paid paraphrase batch), so pinning a
    constant would mean a red suite between the generation and somebody remembering to
    update it. Self-consistency catches the failure that actually matters — a text changed
    without a rebuild — and the provenance block makes a legitimate rebuild visible in the
    diff.
    """
    payload = read_json(path)
    return bool(payload["corpus_hash"] == content_hash(corpus_material(payload)))

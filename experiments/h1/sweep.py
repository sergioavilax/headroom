"""The sweep: one similarity matrix, the shipped admission decision, the whole grid.

BUILD_PLAN §P8.H1's mechanism — *"Embed every probe and every cache entry once, record the
full similarity matrix, then replay the cache-admission decision offline across the entire
threshold range"* — at 130 questions. Zero marginal API cost, deterministic, and keyless:
this module imports no model, no provider, and no sibling repo.

**The decision replayed is the shipped one**, not a re-implementation of it: cosine over one
namespace, a fixed ``context_hash`` and ``embedding_model``, ``similarity >= threshold``,
**top-1** — which is `ResponseCacheStore.search(..., limit=1)`. Phase 5 built
``search(threshold=0.0, limit=k)`` for exactly this, and
`tests/test_experiments_h1.py::test_the_offline_sweep_agrees_with_the_shipped_gateway`
seeds a real gateway and checks the two agree rather than asserting they do.

**Top-1 makes the curve a step function, and that is worth stating.** The nearest entry to a
probe does not depend on the threshold; only whether it clears does. So every probe has one
similarity and one classification, and the whole grid is a series of cut points through the
same 520 numbers. Two consequences: the hit and poison counts are provably monotone in the
threshold (which H-063's "and stays zero above" clause therefore satisfies for free rather
than by luck), and the honest form of the finding is not the grid at all — it is
:data:`Summary.max_swa_similarity` against :data:`Summary.min_correct_similarity`, the two
numbers between which every safe threshold must sit.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from operator import mul
from pathlib import Path
from typing import Any, Final

from experiments.h1.corpus import SPACES, H1Corpus, load_corpus, load_vectors
from experiments.provenance import RESULTS_DIR, provenance, write_json

__all__ = [
    "FAMILY_A",
    "FAMILY_B",
    "GRID",
    "OUTCOMES",
    "USD_PER_QUERY",
    "Neighbour",
    "ProbeOutcome",
    "Summary",
    "classify",
    "main",
    "sweep",
]

#: PRE_REGISTRATION §H1.5: 0.700 → 0.990 in steps of 0.005.
GRID: Final = tuple(round(0.700 + 0.005 * step, 3) for step in range(59))

#: PRE_REGISTRATION §H1.6, fixed before the data: Backline's measured $/query including
#: judge, from its **local-control** run — the lowest of the three published figures, so the
#: modelled saving errs against the cache.
USD_PER_QUERY: Final = Decimal("0.0593")

FAMILY_A: Final = "paraphrase"
FAMILY_B: Final = "novel_question"

OUTCOME_MISS: Final = "miss"
OUTCOME_CORRECT: Final = "correct"
OUTCOME_BENIGN: Final = "benign_collision"
OUTCOME_SWA: Final = "silent_wrong_answer"
OUTCOMES: Final = (OUTCOME_MISS, OUTCOME_CORRECT, OUTCOME_BENIGN, OUTCOME_SWA)

#: How many neighbours are written out per probe. Not used by the decision — top-1 is the
#: decision — but a similarity with no runners-up beside it cannot be argued with.
NEIGHBOURS_KEPT: Final = 3

CURVE_PATH: Final = RESULTS_DIR / "h1_curve.json"
PROBES_PATH: Final = RESULTS_DIR / "h1_probes.json"


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Unit-length vectors, so the dot product *is* the cosine — one direction only.

    The same arithmetic Postgres does as ``1 - (a <=> b)`` and the in-memory store does as a
    dot product, which is what lets an offline replay mean anything about the live path.
    """
    return float(sum(map(mul, left, right)))


@dataclass(frozen=True, slots=True)
class Neighbour:
    entry: str
    similarity: float


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """One probe's whole story: what it asked, what the cache would serve, and how wrong."""

    probe: str
    family: str
    #: The question whose answer would be correct. For Family B that is the probe itself.
    asked: str
    category: str
    neighbours: tuple[Neighbour, ...]

    @property
    def best(self) -> Neighbour:
        return self.neighbours[0]

    def outcome(self, threshold: float) -> str:
        if self.best.similarity < threshold:
            return OUTCOME_MISS
        return self.classification

    #: Fixed at construction: with top-1 admission the served entry does not depend on the
    #: threshold, so neither does what serving it means.
    classification: str = OUTCOME_MISS


def classify(corpus: H1Corpus, *, asked: str, served: str) -> str:
    """What serving ``served``'s answer to ``asked`` is, per PRE_REGISTRATION §H1.6."""
    if served == asked:
        return OUTCOME_CORRECT
    if corpus.equivalent(asked=asked, served=served):
        return OUTCOME_BENIGN
    return OUTCOME_SWA


def _rank(
    probe_vector: tuple[float, ...],
    entries: list[tuple[str, tuple[float, ...]]],
    *,
    exclude: str | None,
    keep: int,
) -> tuple[Neighbour, ...]:
    scored = [
        (entry_id, cosine(probe_vector, vector))
        for entry_id, vector in entries
        if entry_id != exclude
    ]
    # Ties break by ascending entry id (PRE_REGISTRATION §H1.5), so a rebuild is bit-identical.
    scored.sort(key=lambda row: (-row[1], row[0]))
    return tuple(
        Neighbour(entry=entry_id, similarity=round(score, 6)) for entry_id, score in scored[:keep]
    )


def probe_outcomes(
    corpus: H1Corpus, vectors: dict[str, tuple[float, ...]]
) -> tuple[ProbeOutcome, ...]:
    """Both families, ranked against the seeded entries, in one pass over one space."""
    entries = [(question.id, vectors[question.id]) for question in corpus.questions]
    outcomes: list[ProbeOutcome] = []

    for probe in corpus.probes:  # Family A — a right answer exists
        neighbours = _rank(vectors[probe.id], entries, exclude=None, keep=NEIGHBOURS_KEPT)
        outcomes.append(
            ProbeOutcome(
                probe=probe.id,
                family=FAMILY_A,
                asked=probe.source,
                category=probe.category,
                neighbours=neighbours,
                classification=classify(corpus, asked=probe.source, served=neighbours[0].entry),
            )
        )

    for question in corpus.questions:  # Family B — leave-one-out, no right answer exists
        neighbours = _rank(vectors[question.id], entries, exclude=question.id, keep=NEIGHBOURS_KEPT)
        outcomes.append(
            ProbeOutcome(
                probe=f"{question.id}#novel",
                family=FAMILY_B,
                asked=question.id,
                category=question.category,
                neighbours=neighbours,
                classification=classify(corpus, asked=question.id, served=neighbours[0].entry),
            )
        )
    return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class Summary:
    """The numbers the finding is actually made of, per family and space."""

    family: str
    space: str
    probes: int
    #: Highest similarity at which the cache would serve a provably wrong answer. Every safe
    #: threshold is strictly above it; ``None`` when there is no poison anywhere.
    max_swa_similarity: float | None
    #: Lowest similarity at which it would serve a correct one. A threshold above this
    #: starts costing real hits.
    min_correct_similarity: float | None
    counts: dict[str, int]


def _summarise(family: str, space: str, outcomes: list[ProbeOutcome]) -> Summary:
    swa = [row.best.similarity for row in outcomes if row.classification == OUTCOME_SWA]
    correct = [row.best.similarity for row in outcomes if row.classification == OUTCOME_CORRECT]
    return Summary(
        family=family,
        space=space,
        probes=len(outcomes),
        max_swa_similarity=max(swa) if swa else None,
        min_correct_similarity=min(correct) if correct else None,
        counts=dict(Counter(row.classification for row in outcomes)),
    )


def _curve(outcomes: list[ProbeOutcome]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for threshold in GRID:
        tally = Counter(row.outcome(threshold) for row in outcomes)
        hits = tally[OUTCOME_CORRECT] + tally[OUTCOME_BENIGN] + tally[OUTCOME_SWA]
        total = len(outcomes)
        points.append(
            {
                "threshold": threshold,
                "probes": total,
                "hits": hits,
                "correct": tally[OUTCOME_CORRECT],
                "benign_collision": tally[OUTCOME_BENIGN],
                "silent_wrong_answer": tally[OUTCOME_SWA],
                "miss": tally[OUTCOME_MISS],
                "hit_rate": round(hits / total, 6) if total else 0.0,
                "swa_rate_of_probes": round(tally[OUTCOME_SWA] / total, 6) if total else 0.0,
                "swa_rate_of_hits": round(tally[OUTCOME_SWA] / hits, 6) if hits else 0.0,
                "usd_saved": str(USD_PER_QUERY * hits),
            }
        )
    return points


def _tau_zero(by_threshold: dict[float, int]) -> float | None:
    """H-063: the lowest grid threshold whose SWA count is zero and stays zero above it."""
    tau: float | None = None
    for threshold in reversed(GRID):
        if by_threshold[threshold] == 0:
            tau = threshold
        else:
            break
    return tau


def sweep(corpus: H1Corpus, spaces: dict[str, dict[str, tuple[float, ...]]]) -> dict[str, Any]:
    """The whole experiment: every family, every space, the grid, and τ₀."""
    result: dict[str, Any] = {
        "schema": "h1_curve/1",
        "provenance": provenance(
            produced_by="experiments/h1/sweep.py",
            inputs={"corpus_hash": corpus.corpus_hash, "suite_hash": corpus.suite_hash},
            notes="Free and deterministic: arithmetic over committed vectors.",
        ),
        "corpus": {
            "stage": corpus.stage,
            "corpus_hash": corpus.corpus_hash,
            "questions": len(corpus.questions),
            "probes": len(corpus.probes),
            "excluded": [list(row) for row in corpus.excluded],
            "embedding_model": corpus.embedding_model,
        },
        "pre_registration": {
            "grid": {"start": GRID[0], "stop": GRID[-1], "step": 0.005, "points": len(GRID)},
            "usd_per_query": str(USD_PER_QUERY),
            "admission": "top-1, similarity >= threshold, ties by ascending entry id",
            "shipped_default_threshold": 0.9,
        },
        "spaces": {},
        "probe_outcomes": {},
    }

    for space in SPACES:
        outcomes = list(probe_outcomes(corpus, spaces[space]))
        families = {
            FAMILY_A: [row for row in outcomes if row.family == FAMILY_A],
            FAMILY_B: [row for row in outcomes if row.family == FAMILY_B],
        }
        block: dict[str, Any] = {"families": {}, "combined": {}}
        for family, rows in families.items():
            if not rows:
                continue
            summary = _summarise(family, space, rows)
            block["families"][family] = {
                "summary": {
                    "probes": summary.probes,
                    "counts": summary.counts,
                    "max_swa_similarity": summary.max_swa_similarity,
                    "min_correct_similarity": summary.min_correct_similarity,
                },
                "curve": _curve(rows),
            }

        combined_curve = _curve(outcomes)
        swa_by_threshold = {
            float(point["threshold"]): int(point["silent_wrong_answer"]) for point in combined_curve
        }
        combined_summary = _summarise("combined", space, outcomes)
        block["combined"] = {
            "summary": {
                "probes": combined_summary.probes,
                "counts": combined_summary.counts,
                "max_swa_similarity": combined_summary.max_swa_similarity,
                "min_correct_similarity": combined_summary.min_correct_similarity,
            },
            "curve": combined_curve,
            # H-063's rule, applied by code rather than read off the curve.
            "tau_zero": _tau_zero(swa_by_threshold),
            "at_shipped_default": next(
                point for point in combined_curve if point["threshold"] == 0.9
            ),
        }
        block["by_category"] = _by_category(outcomes)
        result["spaces"][space] = block
        result["probe_outcomes"][space] = [
            {
                "probe": row.probe,
                "family": row.family,
                "asked": row.asked,
                "category": row.category,
                "classification": row.classification,
                "neighbours": [
                    {"entry": neighbour.entry, "similarity": neighbour.similarity}
                    for neighbour in row.neighbours
                ],
            }
            for row in outcomes
        ]
    return result


def _by_category(outcomes: list[ProbeOutcome]) -> dict[str, dict[str, Any]]:
    """Descriptive only — pre-registered as secondary, never as a place to find a subset.

    Counted **at the shipped default threshold** rather than over all thresholds at once. A
    bare classification tally would say "15 of 15 catalog_lookup probes are wrong hits" when
    what it means is "…if they hit at all", which at 0.99 most of them do not. The naked
    classification counts are still here, under ``ever``, beside the similarity of the
    closest wrong neighbour in the category — the number that says how high a threshold
    would have to go to escape it.
    """
    tally: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        bucket = tally.setdefault(
            row.category,
            {
                "probes": 0,
                "at_shipped_default": dict.fromkeys(OUTCOMES, 0),
                "ever": dict.fromkeys(OUTCOMES, 0),
                "max_swa_similarity": None,
            },
        )
        bucket["probes"] += 1
        bucket["at_shipped_default"][row.outcome(0.9)] += 1
        bucket["ever"][row.classification] += 1
        if row.classification == OUTCOME_SWA:
            best = bucket["max_swa_similarity"]
            bucket["max_swa_similarity"] = (
                row.best.similarity if best is None else max(float(best), row.best.similarity)
            )
    return dict(sorted(tally.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h1.sweep",
        description="Replay the cache-admission decision across the threshold grid. Free.",
    )
    parser.add_argument("--out", default=str(CURVE_PATH))
    args = parser.parse_args(argv)

    corpus = load_corpus()
    spaces = load_vectors(corpus)
    result = sweep(corpus, spaces)

    outcomes = result.pop("probe_outcomes")
    write_json(Path(args.out), result)
    write_json(
        PROBES_PATH,
        {
            "schema": "h1_probes/1",
            "corpus_hash": corpus.corpus_hash,
            "provenance": provenance(produced_by="experiments/h1/sweep.py"),
            "spaces": outcomes,
        },
    )

    for space in SPACES:
        combined = result["spaces"][space]["combined"]
        summary = combined["summary"]
        print(
            f"[{space}] {summary['probes']} probes · "
            f"counts {summary['counts']} · "
            f"max SWA similarity {summary['max_swa_similarity']} · "
            f"min correct similarity {summary['min_correct_similarity']} · "
            f"τ₀ {combined['tau_zero']}"
        )
    print(f"\nwrote {args.out} and {PROBES_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())

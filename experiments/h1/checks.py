"""Mechanical survival checks — the half of the QA chain that does not need a human.

BUILD_PLAN §P8.H1: *"Mechanically checked (entity/period tokens must survive) + operator
spot-check of a sample."* This module is the first clause, and it runs **before** the
artifact is built, never after a number exists (risk register item 3).

**These checks are deliberately over-broad, and the asymmetry is the design.** A false
positive costs one regeneration — a few thousandths of a cent and a second. A false
negative puts a probe in the corpus that quietly asks a different question, and every
similarity number computed from it is then measuring something nobody named. So the token
extractors keep anything that *might* be an entity, and the stop-list of capitalised words
a paraphrase may legitimately drop is short and explicit rather than clever.

One check looks cosmetic and is not: **no blank line inside a paraphrase**. The corpus
re-attaches the answer-format tail as ``body + "\\n\\n" + tail`` and `split_prompt` splits
at the *first* blank line, so a paraphrase containing one would be silently re-split — half
the question would become the protocol. That is the kind of corruption that produces a
plausible corpus and an unexplainable curve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "MAX_LENGTH_RATIO",
    "MIN_LENGTH_RATIO",
    "STOPWORDS",
    "CheckResult",
    "check_batch",
    "check_paraphrase",
    "salient_tokens",
]

#: Digits with optional thousands separators and decimals. Normalised by dropping commas,
#: so ``1,234.56`` and ``1234.56`` are the same token — a paraphrase may reformat a number
#: but may not change it.
_NUMERIC: Final = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: ``2026-02`` and ``2026-06-30``. Extracted separately from the numerals because a period
#: is the entity most often "improved" into prose ("February 2026"), which loses it.
_PERIOD: Final = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
#: Identifiers and codes: ``FBR-C-00670``, ``ISRC``, ``USD``, ``QZFBR2000146``. Two or more
#: adjacent capitals, optionally hyphenated onward. No trailing ``\b`` so ``ISRCs`` yields
#: ``ISRC``.
_CODE: Final = re.compile(r"[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*")
#: Capitalised words — the proper-noun carrier. Possessives fall away at the boundary, so
#: ``Takeda's`` yields ``Takeda`` and a paraphrase may move the possessive.
_NAME: Final = re.compile(r"\b[A-Z][a-z]+\b")

#: Capitalised words a paraphrase is allowed to drop: sentence openers, imperatives, and
#: articles that only ever start a clause. Everything *not* here must survive, which is the
#: safe direction — adding a word to this list is a deliberate loosening, and forgetting to
#: add one costs a regeneration rather than a corrupt probe.
STOPWORDS: Final = frozenset(
    {
        "A",
        "About",
        "According",
        "After",
        "All",
        "Also",
        "An",
        "And",
        "Any",
        "Are",
        "As",
        "At",
        "Based",
        "Before",
        "Both",
        "But",
        "By",
        "Can",
        "Cite",
        "Compute",
        "Consider",
        "Could",
        "Did",
        "Do",
        "Does",
        "During",
        "Each",
        "End",
        "Every",
        "Explain",
        "Find",
        "For",
        "From",
        "Give",
        "Has",
        "Have",
        "How",
        "If",
        "In",
        "Include",
        "Is",
        "It",
        "Its",
        "List",
        "May",
        "Must",
        "No",
        "Not",
        "Of",
        "On",
        "Only",
        "Or",
        "Our",
        "Per",
        "Provide",
        "Read",
        "Report",
        "Return",
        "Run",
        "Scan",
        "Show",
        "Since",
        "So",
        "Some",
        "State",
        "Submit",
        "Tell",
        "That",
        "The",
        "Their",
        "Then",
        "There",
        "These",
        "This",
        "Those",
        "To",
        "Under",
        "Use",
        "Using",
        "Was",
        "We",
        "Were",
        "What",
        "When",
        "Where",
        "Which",
        "While",
        "Who",
        "Why",
        "Will",
        "With",
        "Within",
        "Would",
        "You",
        "Your",
    }
)

#: A paraphrase may be terser or wordier; it may not be a fragment or an essay.
MIN_LENGTH_RATIO: Final = 0.5
MAX_LENGTH_RATIO: Final = 2.0


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def salient_tokens(text: str) -> dict[str, frozenset[str]]:
    """Everything a paraphrase of ``text`` must still contain, by class.

    Returned per class rather than as one set so a failure says *what* was lost — "period
    2026-02" and "name Okonkwo" send an operator to different places.
    """
    return {
        "period": frozenset(_PERIOD.findall(text)),
        "number": frozenset(match.replace(",", "") for match in _NUMERIC.findall(text)),
        "code": frozenset(_CODE.findall(text)),
        "name": frozenset(name for name in _NAME.findall(text) if name not in STOPWORDS),
    }


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Whether a candidate may enter the corpus, and if not, precisely why."""

    ok: bool
    failures: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def check_paraphrase(*, body: str, candidate: str) -> CheckResult:
    """Apply every mechanical rule to one candidate against its source question body."""
    failures: list[str] = []
    stripped = candidate.strip()

    if not stripped:
        return CheckResult(False, ("empty",))
    if "\n\n" in stripped:
        # See the module docstring: this one would corrupt the body/tail split silently.
        failures.append("contains a blank line, which would re-split the prompt")
    for protocol in ("ANSWER:", "FLAG:", "BATCH:"):
        if protocol in stripped:
            failures.append(f"contains the output protocol token {protocol!r}")
    if _normalise(stripped) == _normalise(body):
        failures.append("identical to the original")

    ratio = len(stripped) / len(body) if body else 0.0
    if not MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO:
        failures.append(
            f"length ratio {ratio:.2f} outside [{MIN_LENGTH_RATIO}, {MAX_LENGTH_RATIO}]"
        )

    expected = salient_tokens(body)
    present = salient_tokens(stripped)
    for kind in ("period", "number", "code", "name"):
        lost = sorted(expected[kind] - present[kind])
        if lost:
            failures.append(f"lost {kind}: {', '.join(lost)}")

    return CheckResult(not failures, tuple(failures))


def check_batch(*, body: str, candidates: list[str]) -> dict[int, CheckResult]:
    """Every candidate for one question, plus the one rule that is about the *set*.

    Three paraphrases that are the same sentence twice are two probes measuring one thing,
    which quietly reweights the corpus toward whichever questions the model found easy.
    """
    results = {
        index: check_paraphrase(body=body, candidate=text) for index, text in enumerate(candidates)
    }
    seen: dict[str, int] = {}
    for index, text in enumerate(candidates):
        key = _normalise(text)
        first = seen.setdefault(key, index)
        if first != index:
            previous = results[index]
            results[index] = CheckResult(
                False, (*previous.failures, f"duplicate of candidate {first + 1}")
            )
    return results

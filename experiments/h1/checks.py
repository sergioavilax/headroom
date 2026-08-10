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

**Amended before any measurement existed (H-068).** The over-broad direction has a floor,
and the first generation run found it: 16 of 130 questions were unresolvable because
``Suppose``, ``Please``, ``Across``, ``Counting``, ``Summing``, ``Exactly`` and ``Audit``
were being extracted as names that must survive. Those are not entities; they are ordinary
words that happen to open a sentence, and no faithful paraphrase can keep them — the
failures were identical across all 3 candidates over 3 attempts, so re-drawing could not clear
them. A rule a correct answer cannot satisfy is not strictness, it is a stuck generator.

Three amendments, each narrow, and the reasoning for each is in H-068:

* **Position is not entityhood.** A capitalised word is exempt only when it opens a
  sentence *and* is an ordinary English word (:data:`COMMON_WORDS`). Both clauses are
  required, so ``"Bones"`` mid-sentence and ``Germany`` at a sentence start both stay
  required, and a name is exempt only if *every* one of its occurrences is positional.
* **ALL-CAPS emphasis is not a code.** ``report ONLY findings`` is emphasis, not an
  identifier. ``EP``, ``US``, ``GB`` and ``ISRC`` are unaffected — see H-068 on why ``EP``
  stayed strict where ``ONLY`` did not.
* **A gloss the body writes itself declares an equivalence.** ``United States (US)`` is one
  entity with two spellings, so the pair is satisfied by *either*, and ``U.S.`` normalises
  to ``US``. Dropping both still fails, which is the case that matters.

**The exemptions apply to what a paraphrase must *keep*, never to what counts as *kept*.**
:func:`required_tokens` is lenient; :func:`salient_tokens`, which reads the candidate, stays
literal. The asymmetry is load-bearing: were the sentence-initial rule applied to the
candidate too, a paraphrase that opened with ``Voltage has …`` would have its own real name
exempted out of the present set and be failed for losing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "COMMON_WORDS",
    "MAX_LENGTH_RATIO",
    "MIN_LENGTH_RATIO",
    "STOPWORDS",
    "AliasGroup",
    "CheckResult",
    "Requirements",
    "check_batch",
    "check_paraphrase",
    "required_tokens",
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
#: Dotted acronyms — ``U.S.``, ``U.K.``. Normalised to the undotted spelling on *both* sides
#: of every comparison, so ``U.S.`` reads as ``US`` rather than as no token at all. Two
#: letter-dot pairs minimum, which is what keeps ``Pt. 3`` out of it.
_DOTTED: Final = re.compile(r"\b(?:[A-Z]\.){2,}")
#: A gloss the question writes itself: ``United States (US)``, ``Germany (DE)``. The
#: parenthesis must hold the code *alone* — ``"Bones" (ISRC QZFBR2100139)`` is two tokens and
#: does not match, so an ISRC never becomes an optional spelling of a track name.
_GLOSS: Final = re.compile(r"((?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)*)\s*\(([A-Z][A-Z0-9]+)\)")

#: Characters after which a capital letter is explained by position alone.
_SENTENCE_END: Final = frozenset(".!?\n")

#: Below this length an ALL-CAPS token is read as a code even if it spells a word: ``US``,
#: ``EP``, ``GB``, ``IT`` are codes in this corpus and ``ONLY`` is emphasis.
_MIN_EMPHASIS_LENGTH: Final = 3

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

#: Ordinary English words, lower-cased. Consulted **only** where something other than
#: entityhood explains the capital: a word that opens a sentence, and an ALL-CAPS word long
#: enough not to be a code. Never consulted mid-clause, which is why a band called *Counting
#: Crows* would still be protected here — ``Crows`` is required whatever ``Counting`` does.
#:
#: This list is allowed to be generous *because* of that conditioning, which is the opposite
#: trade from :data:`STOPWORDS` above: a word added there is dropped everywhere, a word added
#: here is dropped only where its capital was already explained.
COMMON_WORDS: Final = frozenset(
    {
        "above",
        "accordingly",
        "across",
        "actually",
        "additionally",
        "afterwards",
        "again",
        "aggregating",
        "along",
        "already",
        "also",
        "although",
        "altogether",
        "among",
        "amongst",
        "another",
        "anything",
        "apart",
        "applying",
        "approximately",
        "around",
        "aside",
        "assess",
        "assume",
        "assuming",
        "audit",
        "auditing",
        "because",
        "beginning",
        "below",
        "beneath",
        "besides",
        "between",
        "beyond",
        "breaking",
        "calculate",
        "calculating",
        "checking",
        "collectively",
        "combining",
        "comparing",
        "computing",
        "concerning",
        "confirm",
        "considering",
        "counting",
        "currently",
        "described",
        "describe",
        "despite",
        "detail",
        "determine",
        "determining",
        "effectively",
        "either",
        "estimate",
        "evaluate",
        "everything",
        "exactly",
        "excluding",
        "extract",
        "finally",
        "first",
        "focusing",
        "following",
        "generally",
        "given",
        "however",
        "identify",
        "identifying",
        "ideally",
        "ignoring",
        "including",
        "individually",
        "instead",
        "just",
        "kindly",
        "less",
        "limiting",
        "listing",
        "looking",
        "many",
        "measuring",
        "meanwhile",
        "more",
        "most",
        "much",
        "near",
        "neither",
        "netting",
        "next",
        "none",
        "normally",
        "note",
        "nothing",
        "once",
        "only",
        "otherwise",
        "ought",
        "overall",
        "please",
        "precisely",
        "previously",
        "purely",
        "quantify",
        "reconcile",
        "reconciling",
        "regarding",
        "restricting",
        "reviewing",
        "rolling",
        "roughly",
        "second",
        "separately",
        "several",
        "shall",
        "should",
        "simply",
        "solely",
        "something",
        "specifically",
        "starting",
        "strictly",
        "such",
        "summarise",
        "summarize",
        "summing",
        "suppose",
        "supposing",
        "taking",
        "tally",
        "therefore",
        "though",
        "throughout",
        "thus",
        "together",
        "total",
        "totalling",
        "toward",
        "towards",
        "treating",
        "typically",
        "unless",
        "until",
        "upon",
        "various",
        "verify",
        "versus",
        "via",
        "walk",
        "whatever",
        "whenever",
        "whether",
        "whichever",
        "without",
        "working",
    }
)

#: A paraphrase may be terser or wordier; it may not be a fragment or an essay.
MIN_LENGTH_RATIO: Final = 0.5
MAX_LENGTH_RATIO: Final = 2.0


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def salient_tokens(text: str) -> dict[str, frozenset[str]]:
    """Every salient token *present* in ``text``, by class — the literal reading.

    Returned per class rather than as one set so a failure says *what* was lost — "period
    2026-02" and "name Okonkwo" send an operator to different places.

    This is the **candidate** side of every comparison and it applies no exemptions beyond
    :data:`STOPWORDS`: what a paraphrase is allowed to drop is decided by
    :func:`required_tokens`, over the *body*, and nowhere else (H-068).
    """
    codes = set(_CODE.findall(text))
    codes.update(match.group().replace(".", "") for match in _DOTTED.finditer(text))
    return {
        "period": frozenset(_PERIOD.findall(text)),
        "number": frozenset(match.replace(",", "") for match in _NUMERIC.findall(text)),
        "code": frozenset(codes),
        "name": frozenset(name for name in _NAME.findall(text) if name not in STOPWORDS),
    }


def _opens_a_sentence(text: str, start: int) -> bool:
    """Is the token at ``start`` capitalised by position alone?

    Only spaces and tabs are stepped over. A quote, bracket or dash before the word is
    itself evidence of nameness — ``"Bones"`` and ``(Kinetic Digital`` are not sentence
    openings — so those keep their capital's meaning and stay required.
    """
    index = start - 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    return index < 0 or text[index] in _SENTENCE_END


@dataclass(frozen=True, slots=True)
class AliasGroup:
    """One entity the body glosses itself: ``United States (US)``.

    Either spelling satisfies it. Losing **both** is still a failure, which is the case the
    check exists for — this widens what counts as survival, never what counts as absence.
    """

    name: str
    code: str
    parts: frozenset[str]

    def satisfied_by(self, present: dict[str, frozenset[str]]) -> bool:
        return self.code in present["code"] or self.parts <= present["name"]


@dataclass(frozen=True, slots=True)
class Requirements:
    """What a paraphrase of one body must carry: exact tokens, plus either-spelling groups."""

    tokens: dict[str, frozenset[str]]
    groups: tuple[AliasGroup, ...]


def required_tokens(text: str) -> Requirements:
    """Everything a paraphrase of ``text`` must still contain — the **lenient** side.

    Three subtractions from the literal reading, each argued in H-068: a capitalised common
    word that only ever opens a sentence, an ALL-CAPS common word used as emphasis, and the
    two spellings of a self-glossed entity, which fold into one :class:`AliasGroup` instead
    of two independent demands.
    """
    tokens = dict(salient_tokens(text))

    kept: set[str] = set()
    for match in _NAME.finditer(text):
        name = match.group()
        if name in STOPWORDS:
            continue
        # Any single non-positional occurrence makes the name an entity everywhere.
        if _opens_a_sentence(text, match.start()) and name.casefold() in COMMON_WORDS:
            continue
        kept.add(name)
    tokens["name"] = frozenset(kept)

    tokens["code"] = frozenset(
        code
        for code in tokens["code"]
        if not (len(code) >= _MIN_EMPHASIS_LENGTH and code.casefold() in COMMON_WORDS)
    )

    groups: list[AliasGroup] = []
    for match in _GLOSS.finditer(text):
        phrase, code = match.group(1), match.group(2)
        parts = frozenset(part for part in _NAME.findall(phrase) if part in tokens["name"])
        if not parts or code not in tokens["code"]:
            # Nothing to trade: the gloss adds no leniency, so the tokens stay as they are.
            continue
        groups.append(AliasGroup(name=phrase, code=code, parts=parts))

    if groups:
        tokens["name"] = tokens["name"].difference(*(group.parts for group in groups))
        tokens["code"] = tokens["code"] - {group.code for group in groups}
    return Requirements(tokens=tokens, groups=tuple(groups))


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

    required = required_tokens(body)
    present = salient_tokens(stripped)
    for kind in ("period", "number", "code", "name"):
        lost = sorted(required.tokens[kind] - present[kind])
        if lost:
            failures.append(f"lost {kind}: {', '.join(lost)}")
    for group in required.groups:
        if not group.satisfied_by(present):
            failures.append(
                f"lost entity: neither {group.name!r} nor its code {group.code} survives"
            )

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

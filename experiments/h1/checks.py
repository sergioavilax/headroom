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

**Amended a second time, still before any measurement (H-069): a two-part ask must stay two
parts.** The operator's spot-check rejected a paraphrase every token rule passed. The body
asked two things — *"what royalty rate applies …? Cite the governing clause."* — and the
candidate asked one, the citation, with the rate demoted to a modifier inside it. A forced
redraw reproduced the same collapse in 2 of 3 fresh candidates, so this is not a bad draw:
`claude-haiku-4-5` systematically compresses *answer-plus-citation* into *citation*. Every
entity, period and figure survives the compression, which is exactly why the token rules
cannot see it.

:func:`compound_ask` finds the shape and :meth:`CompoundAsk.satisfied_by` requires it to
survive, on the same footing as an entity. Two things make it a rule about structure rather
than about one question's wording:

* **The shape is read off the body, never hardcoded.** A compound ask is an interrogative
  sentence *plus* a separate sentence opening with a request verb (:data:`REQUEST_VERBS`).
  The two demands are the head nouns of what each part asks for — extracted, not listed. In
  this suite that finds 25 questions across three categories; ``clause`` and ``rate`` appear
  nowhere in this module.
* **Two demands must sit in two asks.** English joins two demands with a coordinator, a
  sentence break, or a participial adjunct (``…, citing the controlling clause``), and
  :func:`ask_segments` splits on exactly those. Both demands inside one segment is the
  collapse: *"Cite the clause that sets X's royalty rate"* mentions the rate but does not
  ask for it.

**What this check does not do**, stated plainly because the spot-check is the other half of
the QA chain and needs to know what is left to it: it catches a compound ask *collapsing to
one*, not every way a paraphrase can drift. A candidate that keeps two separate demands but
swaps one for something the body never asked would pass here. And the rule fires only on
*question + instruction*; a body whose parts are all instructions (the reconciliation runs)
is out of its scope, deliberately — no collapse has been observed there, and widening a rule
to a shape without evidence is how H-068's stuck generator happened.

**Amended a third time, still before any measurement (H-071): a prohibition must survive as a
prohibition.** The operator's spot-check rejected ``reconciliation-006#p1`` for reading *"…
submitting them individually rather than as a batch"* where the body says *"Do not submit a
batch."* The forced redraw's sampled probe came back clean and its **p3 reproduced the
identical inversion** on an independent draw; auditing the whole batch found the same shape
on five further bodies. `claude-haiku-4-5` systematically softens a bare prohibition into a
positive order about what to do *instead*. Every entity, period and figure survives that —
the prohibited object is usually still sitting in the sentence — which is why the token rules
cannot see it, and the compound-ask rule cannot either: an all-instruction body is out of its
scope by design, so nothing in this module was looking at the reconciliation family's last
sentence at all.

:func:`prohibitions` finds the shape, and :class:`Prohibition` asks two things of a candidate,
on the same footing as an entity:

* **The prohibition survives.** Some negation must still scope the prohibited verb or its
  object — verbatim (``do not submit a batch``), through another negator (``without
  submitting a batch``, ``avoiding batch submission``), or over the object alone (``not a
  batch submission``, ``not batched``). A rewrite that drops it fails, and so does one that
  keeps a prohibition about something else (``Do not group submissions.``).
* **It has not become an order.** No un-negated use of the prohibited verb may appear.
  *"submitting them individually rather than as a batch"* keeps the exclusion and adds an
  instruction the body never gave: a bare prohibition names no alternative, and one that
  acquires one is telling the agent to do something nobody asked for.

**Scope, and it is narrow on purpose.** The rule fires only where a negator *opens* an ask —
the bare prohibition standing as its own instruction, which is what every observed failure is
about. A negation modifying an otherwise affirmative instruction (``say so in prose without
flagging it``, once in this suite) is left alone: there the sentence's own verb is positive, a
faithful rewrite may legitimately restructure it into a restriction (``flag only what is out
of tolerance``), and no inversion has been observed on that shape. Widening a rule to a shape
without evidence is how H-068's stuck generator happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

__all__ = [
    "COMMON_WORDS",
    "MAX_LENGTH_RATIO",
    "MIN_LENGTH_RATIO",
    "NEGATION_CUES",
    "NEGATIVE_OBJECTS",
    "PROHIBITION_MARKERS",
    "REQUEST_PARTICIPLES",
    "REQUEST_VERBS",
    "STOPWORDS",
    "WH_WORDS",
    "AliasGroup",
    "CheckResult",
    "CompoundAsk",
    "Prohibition",
    "Requirements",
    "ask_segments",
    "check_batch",
    "check_paraphrase",
    "compound_ask",
    "prohibitions",
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


# --- the compound ask (H-069) --------------------------------------------------------------

#: Verbs that open an explicit instruction to produce something *besides* the answer. A
#: sentence is an instruction only when one of these opens it, which is also what keeps
#: ``Do not submit a batch.`` out: a prohibition adds no second demand, and ``do`` is not
#: here. Kept short and literal — a verb added here makes a body compound, and a body wrongly
#: called compound costs regenerations on every one of its candidates.
REQUEST_VERBS: Final = frozenset(
    {
        "cite",
        "explain",
        "give",
        "identify",
        "indicate",
        "list",
        "name",
        "provide",
        "quote",
        "report",
        "show",
        "specify",
        "state",
        "tell",
    }
)

#: The same requests written as adjuncts: ``…, citing the controlling clause``. Spelled out
#: rather than derived, because deriving them means guessing at English morphology
#: (``specify`` → ``specifying``, ``give`` → ``giving``) in a module whose whole argument is
#: that explicit beats clever. These are segment *boundaries*, not sentence openers: a
#: paraphrase that appends its second demand this way has kept it, and must not be failed.
REQUEST_PARTICIPLES: Final = frozenset(
    {
        "citing",
        "explaining",
        "giving",
        "identifying",
        "indicating",
        "listing",
        "naming",
        "providing",
        "quoting",
        "reporting",
        "showing",
        "specifying",
        "stating",
        "telling",
    }
)

#: What makes a segment a question when the body's own question has no head noun to demand —
#: a polar question (``is Japan (JP) inside the licensed territory?``) asks for a yes or a no,
#: not for a thing.
WH_WORDS: Final = frozenset(
    {"how", "what", "when", "where", "which", "who", "whom", "whose", "why"}
)

#: Auxiliaries that open a polar question by inversion.
_POLAR_OPENERS: Final = frozenset(
    {"are", "can", "could", "did", "do", "does", "has", "have", "is", "should", "was", "were"}
)

#: Words that introduce or close a demand phrase without naming what is demanded. Skipped
#: while nothing has been collected (``Cite **the governing** clause``) and terminal once
#: something has (``the rate clause **you** applied``).
_PHRASE_STOP: Final = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "each",
        "every",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "i",
        "in",
        "is",
        "it",
        "its",
        "may",
        "must",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "should",
        "that",
        "the",
        "their",
        "these",
        "they",
        "this",
        "those",
        "to",
        "under",
        "was",
        "we",
        "were",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

#: Adverbs a directive may open with before its verb: ``Please cite …``, ``Then list …``.
_DIRECTIVE_LEAD: Final = frozenset(
    {"additionally", "also", "finally", "lastly", "next", "please", "then"}
)

#: A demand phrase is a noun phrase, not a clause. Four words is longer than any in this
#: suite (``the rate clause``, ``the governing clause``, ``royalty rate``) and short enough
#: that a runaway match cannot swallow the sentence.
_MAX_PHRASE_WORDS: Final = 4

#: How far past ``start`` to look while collecting those four: the leading function words a
#: phrase is allowed to open with (``what is the new download rate`` skips three) are stepped
#: over, not collected, so the scan window has to be wider than the phrase itself.
_MAX_SCAN_WORDS: Final = 8

#: Once a word has been collected, an inflected form is the verb the phrase attaches to:
#: ``the royalty rate **applies** to``, ``the clause **governing** X``.
_INFLECTED: Final = ("ed", "ing", "s")

_WORD: Final = re.compile(r"[A-Za-z][A-Za-z'-]*")

#: A sentence ends at ``.``/``?``/``!`` — unless that full stop belongs to a single-letter
#: initial, which is what keeps ``U.S. royalty`` one span rather than two.
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.?!])(?<![A-Z]\.)\s+")

#: Where a second demand may legitimately begin: a sentence break, a coordinator, or a comma
#: introducing a participial request. Splitting here is the whole test — two demands on
#: opposite sides of one of these are two asks; two demands with none between them are one.
#:
#: **The case-insensitivity is scoped to the word alternatives, and that is load-bearing**
#: (H-071). It used to be a flag on the whole pattern, which quietly turned the initials guard
#: ``(?<![A-Z]\.)`` into "any letter followed by a full stop" — so this never split a sentence
#: ending in ``.`` at all, only one ending in ``?``. `_SENTENCE_BREAK` above is unaffected and
#: always was, so every *body* parsed correctly; what suffered were candidates that put their
#: second demand in a second declarative sentence.
_ASK_BREAK: Final = re.compile(
    r"(?<=[.?!;:])(?<![A-Z]\.)\s+"
    r"|(?i:\s*,?\s+(?:and|or|plus|as\s+well\s+as)\s+)"
    r"|(?i:\s*,\s+(?=(?:" + "|".join(sorted(REQUEST_PARTICIPLES)) + r")\b))"
)


def _words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _demand_phrase(words: list[str], start: int) -> str | None:
    """The head noun of the noun phrase beginning at ``start``, or ``None``.

    Leading function words are stepped over; the phrase ends at the first function word or
    inflected form after it has begun. The *last* word collected is the head, which is where
    English puts it: ``the governing clause`` → ``clause``, ``royalty rate`` → ``rate``.
    """
    collected: list[str] = []
    for word in words[start : start + _MAX_SCAN_WORDS]:
        if word in _PHRASE_STOP:
            if collected:
                break
            continue
        if collected and word.endswith(_INFLECTED):
            break
        collected.append(word)
        if len(collected) >= _MAX_PHRASE_WORDS:
            break
    return collected[-1] if collected else None


def _instruction_demand(sentence: str) -> str | None:
    """What an instruction sentence asks for, or ``None`` if it is not an instruction."""
    words = _words(sentence)
    index = 0
    while index < len(words) and words[index] in _DIRECTIVE_LEAD:
        index += 1
    if index >= len(words) or words[index] not in REQUEST_VERBS:
        return None
    return _demand_phrase(words, index + 1)


def _question_demand(sentence: str) -> str | None:
    """What an interrogative asks for — the head of its wh-phrase, or ``None`` if polar."""
    words = _words(sentence)
    for index, word in enumerate(words):
        if word in WH_WORDS:
            return _demand_phrase(words, index + 1)
    return None


def _carries(segment: str, noun: str) -> bool:
    """Is ``noun`` named in ``segment``? Singular and plural count as the same demand."""
    return re.search(rf"\b{re.escape(noun)}s?\b", segment, re.IGNORECASE) is not None


def _is_interrogative(segment: str) -> bool:
    words = _words(segment)
    return (
        segment.rstrip().endswith("?")
        or bool(WH_WORDS.intersection(words[:6]))
        or (bool(words) and words[0] in _POLAR_OPENERS)
    )


def ask_segments(text: str) -> tuple[str, ...]:
    """``text`` split wherever a fresh demand may begin (see :data:`_ASK_BREAK`)."""
    return tuple(part.strip() for part in _ASK_BREAK.split(text) if part and part.strip())


@dataclass(frozen=True, slots=True)
class CompoundAsk:
    """A body that asks for two things: a question, and an instruction standing beside it.

    ``instruction`` is what the instruction demands (``clause``). ``question`` is what the
    interrogative demands (``rate``), or ``None`` when it demands no thing — a polar question
    wants a yes or a no, and then any second interrogative segment satisfies it.
    """

    instruction: str
    question: str | None

    def satisfied_by(self, text: str) -> bool:
        """Do the two demands land in two different asks?

        Any pair of segments will do, in either order. A faithful paraphrase may lead with
        the citation (*"which clause sets the rate, and what is that rate?"*) and it may name
        a demand more than once; what it may not do is name both only inside one ask.
        """
        segments = ask_segments(text)
        for index, segment in enumerate(segments):
            if not _carries(segment, self.instruction):
                continue
            for other, candidate in enumerate(segments):
                if other == index:
                    continue
                if (
                    _carries(candidate, self.question)
                    if self.question
                    else _is_interrogative(candidate)
                ):
                    return True
        return False

    def describe(self) -> str:
        asked = f"'{self.question}'" if self.question else "the question"
        return f"{asked} and '{self.instruction}'"


def compound_ask(body: str) -> CompoundAsk | None:
    """The two-part ask in ``body``, or ``None`` if it asks for one thing.

    Compound means an interrogative sentence *and* a separate instruction sentence. Where a
    body has several of each, the last of each is taken: the trailing instruction is the one
    a rewrite folds into the question, and taking the last is deterministic. A body whose
    instruction demands the same noun as its question (nothing in this suite) reduces to the
    polar case rather than to a demand that compares against itself.
    """
    questions: list[str] = []
    instructions: list[str] = []
    for sentence in _SENTENCE_BREAK.split(body):
        stripped = sentence.strip()
        if not stripped:
            continue
        if stripped.endswith("?"):
            questions.append(stripped)
        elif (demand := _instruction_demand(stripped)) is not None:
            instructions.append(demand)
    if not questions or not instructions:
        return None
    instruction = instructions[-1]
    focus = _question_demand(questions[-1])
    return CompoundAsk(instruction=instruction, question=None if focus == instruction else focus)


# --- the prohibition (H-071) ---------------------------------------------------------------

#: Negators that take a *verb phrase* after them, and so can open a prohibition: ``Do not
#: submit …``, ``Never flag …``, ``Without submitting …``. ``no`` is deliberately absent — it
#: governs a noun (``no batch``), which is a negation but not an instruction, and reading it as
#: one would make ``No findings were staged`` a rule about a verb called *findings*.
PROHIBITION_MARKERS: Final = frozenset(
    {
        "cannot",
        "can't",
        "doesn't",
        "don't",
        "mustn't",
        "never",
        "not",
        "shouldn't",
        "without",
        "won't",
    }
)

#: Auxiliaries a prohibition may open with before its negator: ``**Do** not submit …``.
_PROHIBITION_AUXILIARIES: Final = frozenset(
    {"can", "could", "do", "does", "may", "might", "must", "shall", "should", "will", "would"}
)

#: What counts as the prohibition still being there, on the candidate side. Wider than
#: :data:`PROHIBITION_MARKERS` because this decides *survival*, and the direction of error on
#: survival is leniency: a rewrite that says ``avoiding batch submission`` or ``excluding batch
#: entries`` has kept the prohibition in words the marker list would never contain. The
#: contrastives are here for the same reason — ``rather than as a batch`` really does forbid the
#: batch. What that phrasing usually fails is the *other* clause, and failing it there produces
#: the accurate diagnosis rather than two vague ones.
NEGATION_CUES: Final = PROHIBITION_MARKERS | frozenset(
    {
        "avoid",
        "avoiding",
        "avoids",
        "exclude",
        "excludes",
        "excluding",
        "forgo",
        "forgoing",
        "instead",
        "neither",
        "no",
        "none",
        "nor",
        "nothing",
        "omit",
        "omits",
        "omitting",
        "rather",
        "refrain",
        "refraining",
        "skip",
        "skipping",
    }
)

#: A prohibition may also survive as a negative *object*: ``submit nothing`` is ``do not submit
#: anything``. Scanned a short way *forward* from the verb, and restricted to quantifiers, so
#: that the ``not`` in ``Submit findings individually, not as a batch`` cannot excuse the
#: ``Submit`` in front of it — which is precisely the inversion this check exists for.
NEGATIVE_OBJECTS: Final = frozenset({"neither", "nobody", "none", "no", "nothing"})

#: How far back a negation may sit and still govern the word it negates. The widest faithful
#: form observed in eight draws across these bodies is ``not as a batch`` — three — so four
#: leaves exactly one word of margin. Wider makes the second clause blind: every inversion in
#: the batch puts its contrastive *after* the verb it wrongly instructs.
_NEGATION_WINDOW: Final = 4

#: How far forward to look for a negative object. ``submit nothing``, ``submit no batch``.
_NEGATIVE_OBJECT_WINDOW: Final = 2


def _inflections(word: str) -> tuple[str, ...]:
    """The forms one word may legitimately be reworded into.

    Derived rather than listed, unlike :data:`REQUEST_PARTICIPLES`, because here the word is
    read off the body at runtime and there is no list to write. The doubling rule is the one
    piece of English morphology that has to be right for the observed case — ``submit`` →
    ``submitted``/``submitting`` — and everything else is a spare suffix that costs nothing.
    """
    forms = {word, word + "s", word + "es", word + "d", word + "ed", word + "ing"}
    vowels = "aeiou"
    if len(word) > 2 and word[-1] not in vowels and word[-2] in vowels and word[-3] not in vowels:
        forms |= {word + word[-1] + "ed", word + word[-1] + "ing"}
    return tuple(sorted(forms))


@lru_cache(maxsize=256)
def _word_pattern(word: str) -> re.Pattern[str]:
    """``word`` in any of its forms, matched inside a token as well as around it.

    Inside, because `_WORD` keeps a hyphenated compound whole and ``do not batch-submit`` is a
    faithful rewrite whose verb lives in the middle of one.
    """
    return re.compile(
        r"\b(?:" + "|".join(re.escape(form) for form in _inflections(word)) + r")\b",
        re.IGNORECASE,
    )


def _negated_at(words: list[str], position: int) -> bool:
    """Is the word at ``position`` inside a negation's scope, within this segment?"""
    start = max(0, position - _NEGATION_WINDOW)
    if any(word in NEGATION_CUES for word in words[start:position]):
        return True
    ahead = words[position + 1 : position + 1 + _NEGATIVE_OBJECT_WINDOW]
    return any(word in NEGATIVE_OBJECTS for word in ahead)


@dataclass(frozen=True, slots=True)
class Prohibition:
    """One thing the body forbids: ``Do not submit a batch.``

    ``verb`` is what is forbidden (``submit``) and ``obj`` the head of what it is forbidden on
    (``batch``), or ``None`` where the prohibition names no object. ``text`` is the ask it was
    read out of, so a failure can quote the body rather than describe it.
    """

    verb: str
    obj: str | None
    text: str

    def _sites(self, candidate: str) -> list[tuple[list[str], int, bool]]:
        """Every mention of the verb or the object: its segment, position, and negation."""
        verb = _word_pattern(self.verb)
        obj = _word_pattern(self.obj) if self.obj else None
        sites: list[tuple[list[str], int, bool]] = []
        for segment in ask_segments(candidate):
            words = _words(segment)
            for position, word in enumerate(words):
                if verb.search(word) or (obj is not None and obj.search(word)):
                    sites.append((words, position, _negated_at(words, position)))
        return sites

    def survives_in(self, candidate: str) -> bool:
        """Does *some* negation still scope the forbidden verb or its object?

        Either one will do: ``not a batch submission`` never uses the verb at all — the
        nominalisation is a different word — and has kept the prohibition on the object;
        ``do not submit`` carries it on the verb with the object left implicit. Losing both is
        a rewrite that no longer forbids anything.
        """
        return any(negated for _, _, negated in self._sites(candidate))

    def instructed_in(self, candidate: str) -> bool:
        """Is the forbidden verb used as an instruction, with nothing negating it?

        Every mention has to be clean, not just one: *"report the findings, submitting them
        individually rather than as a batch"* forbids the batch at the end and orders the
        submission in the middle, and the order is the drift.
        """
        verb = _word_pattern(self.verb)
        return any(
            verb.search(words[position]) and not negated
            for words, position, negated in self._sites(candidate)
        )

    def describe(self) -> str:
        return f"{self.verb} {self.obj}" if self.obj else self.verb


def prohibitions(body: str) -> tuple[Prohibition, ...]:
    """Everything ``body`` forbids outright, in the order it forbids it.

    An ask whose *own* verb is negated — ``Do not submit a batch.``, ``Never flag a
    measurement …`` — and nothing else. A negation inside an otherwise affirmative instruction
    is left to the spot-check, for the reason the module docstring gives.
    """
    found: list[Prohibition] = []
    for segment in ask_segments(body):
        words = _words(segment)
        index = 0
        while index < len(words) and words[index] in _DIRECTIVE_LEAD:
            index += 1
        if index < len(words) and words[index] in _PROHIBITION_AUXILIARIES:
            index += 1
        if index >= len(words) or words[index] not in PROHIBITION_MARKERS:
            continue
        verb_index = index + 1
        if verb_index >= len(words):
            continue
        verb = words[verb_index]
        # `not what applies today` is a clarification, not an order: the word after the negator
        # has to be able to be a verb before any of this means anything.
        if verb in _PHRASE_STOP or verb in WH_WORDS or verb in PROHIBITION_MARKERS:
            continue
        found.append(
            Prohibition(verb=verb, obj=_demand_phrase(words, verb_index + 1), text=segment)
        )
    return tuple(found)


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

    ask = compound_ask(body)
    if ask is not None and not ask.satisfied_by(stripped):
        failures.append(
            f"collapsed the compound ask: {ask.describe()} are asked for separately in the "
            f"question and are folded into one here"
        )

    for forbidden in prohibitions(body):
        # The inversion first, where both would fire: it is the more specific finding, and a
        # rewrite that orders the forbidden action has already lost the argument about whether
        # it also still forbids it somewhere.
        if forbidden.instructed_in(stripped):
            failures.append(
                f"inverted the prohibition {forbidden.text!r}: this instructs "
                f"{forbidden.verb!r} with nothing negating it, and a bare prohibition names "
                f"no alternative to do instead"
            )
        elif not forbidden.survives_in(stripped):
            failures.append(
                f"dropped the prohibition {forbidden.text!r}: nothing here forbids "
                f"{forbidden.describe()!r}"
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

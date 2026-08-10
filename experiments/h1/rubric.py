"""The paraphrase rubric, and the exact request `generate.py` sends.

BUILD_PLAN §P8.H1 states the rubric in one line — *"preserve every entity, period, figure,
and intent exactly; vary only surface form"* — and this module is that line made
operational, because a rubric that lives in a docstring is a rubric nobody ran.

Two properties of what is written here matter more than the wording.

**The model never sees the answer-format tail.** It is asked to rewrite the question body
only, and `generate.py` re-attaches the tail byte-for-byte (H-060). So the commonest way a
helpful model would corrupt this corpus — improving `ANSWER: $<amount>` — is not a failure
mode that has to be caught, because the model is never handed the string.

**The model is told what the paraphrases are *for*.** A rubric that only says "reword this"
gets rewordings that drop the artist's name, because dropping a name reads as good writing.
Saying that a downstream system will match these by meaning, and that a lost entity makes
the item useless, is what moves the failure rate — and it costs nothing to say.

This file is the artifact's provenance: its :data:`RUBRIC_VERSION` and the hash of
:data:`SYSTEM_PROMPT` are stamped into `h1_corpus.json`, so the operator's spot-check
approval is an approval *of a specific text* rather than of a remembered intention.

**Version 2 asks for the compound ask, because the checker alone did not get it drawn**
(H-070). H-069 added a mechanical rule — a body that asks for a value *and* separately
instructs a citation must stay two asks — and deliberately left this prompt alone, on the
argument that a 1-in-3 failure is a retry rather than a regeneration. Three redraw rounds
said otherwise: 17 → 8 → 5 → 5, with five ids failing three to four independent rounds on
the same shape. Rule 4 below is that failure stated as a requirement, positively, with one
faithful form shown. The checker is unchanged and still verifies it: the rubric asks, the
checker verifies, and `test_the_rubric_teaches_a_form_the_checker_accepts` pins that the
form taught here is a form the checker passes.
"""

from __future__ import annotations

from typing import Final

from experiments.provenance import content_hash

__all__ = [
    "COMPOUND_EXAMPLE_FAITHFUL",
    "COMPOUND_EXAMPLE_SOURCE",
    "PARAPHRASES_PER_QUESTION",
    "RUBRIC_VERSION",
    "SPOT_CHECK_SAMPLE",
    "SPOT_CHECK_SEED",
    "SYSTEM_PROMPT",
    "rubric_hash",
    "user_message",
]

#: Bumped whenever :data:`SYSTEM_PROMPT` or :func:`user_message` changes meaning. A batch
#: generated under one version is never mixed with a batch generated under another — which
#: `generate.py` and `build.py` now enforce rather than remember (H-070).
#:
#: 1 → 2 (H-070): rule 4, the two-part ask, after three redraw rounds thrashed on it.
RUBRIC_VERSION: Final = 2

#: BUILD_PLAN §P8.H1: "~3 paraphrases per question (~400 probes)".
PARAPHRASES_PER_QUESTION: Final = 3

#: The operator's spot-check (§P8.H1's QA chain). Drawn by a seeded RNG so the sample is
#: reproducible and cannot be re-drawn until it looks good.
SPOT_CHECK_SAMPLE: Final = 20
SPOT_CHECK_SEED: Final = 20260810

#: Rule 4's worked example, held here as well as in :data:`SYSTEM_PROMPT` so that a test can
#: pin the two together *and* run the taught form through the checker. Invented rather than
#: lifted from the suite: the model is being taught a shape, not a question it will be asked
#: to rewrite. A rubric that teaches a form the checker would reject is the H-068 mistake in
#: its politest form, so that agreement is a test rather than a belief (H-070).
COMPOUND_EXAMPLE_SOURCE: Final = (
    "What was the closing balance for 2026-05? Cite the statement it comes from."
)
COMPOUND_EXAMPLE_FAITHFUL: Final = (
    "What closing balance was recorded for 2026-05, and which statement reports it?"
)

SYSTEM_PROMPT: Final = """\
You rewrite questions for a research corpus that measures semantic caching.

Each item you produce will be embedded and matched, by meaning, against the original
question. If a rewrite loses a name, a date, a figure, or the exact thing being asked,
it stops measuring anything and the item is discarded. Fidelity is the whole product;
elegance is worth nothing here.

RULES — every one of them is checked mechanically after you answer:

1. PRESERVE EVERY ENTITY EXACTLY, spelled exactly as written: artist and band names,
   company and distributor names, contract and document identifiers (FBR-C-00670),
   section markers, ISRCs, statement ids, territory and currency codes.
2. PRESERVE EVERY PERIOD AND FIGURE EXACTLY: 2026-02 stays 2026-02, never "February
   2026" and never "2026-2". Numbers, money amounts, percentages and thresholds keep
   their digits and their punctuation.
3. PRESERVE THE INTENT EXACTLY. Ask for precisely what was asked for: the same quantity,
   the same scope, the same point in time, the same conditions. Do not narrow it, widen
   it, split it, or make it more specific. Do not add or remove a request to cite
   sources, to show work, or to do anything else.
4. KEEP A TWO-PART ASK IN TWO PARTS. When the question asks for a value AND separately
   instructs you to cite, name, report, quote or show something, the rewrite must ask for
   both, as two distinct asks — joined by "and", left as a second sentence, or attached
   as a "…, citing …" clause. Mentioning the second thing inside the first ("cite the
   clause that sets the rate") names it; it does not ask for it.
   Original: What was the closing balance for 2026-05? Cite the statement it comes from.
   Faithful: What closing balance was recorded for 2026-05, and which statement reports it?
5. VARY ONLY THE SURFACE. Word order, voice, register, phrasing, question form. A
   competent reader must agree the two questions have exactly one correct answer between
   them.
6. DO NOT ANSWER, explain, hedge, or comment. Produce the rewritten question and nothing
   else.
7. DO NOT add any output-format instruction. One is appended afterwards, automatically,
   and anything you add would conflict with it.

The rewrites must differ from the original and from each other."""


def user_message(body: str, *, count: int = PARAPHRASES_PER_QUESTION) -> str:
    """The per-question request: the body, the count, and a fixed output shape.

    Numbered lines rather than JSON: the model is `claude-haiku-4-5`, the payload is a
    handful of short strings, and a JSON wrapper is one more thing that can be returned
    slightly wrong for reasons that have nothing to do with paraphrase quality. Parsing is
    in `generate.py` and it is strict — a reply that does not match is a regeneration, not
    a repair.
    """
    return (
        f"Rewrite this question {count} different ways, following every rule.\n\n"
        f"--- QUESTION ---\n{body}\n--- END QUESTION ---\n\n"
        f"Reply with exactly {count} lines, each starting with its number and a period "
        f"(`1. `, `2. `, …) and containing only the rewritten question. "
        f"No preamble, no blank lines, no commentary."
    )


def rubric_hash() -> str:
    """What the operator approved, as a value the artifact can carry."""
    return content_hash(
        {
            "version": RUBRIC_VERSION,
            "system": SYSTEM_PROMPT,
            "user_template": user_message("<body>"),
            "per_question": PARAPHRASES_PER_QUESTION,
        }
    )

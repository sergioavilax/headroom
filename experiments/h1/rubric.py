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
"""

from __future__ import annotations

from typing import Final

from experiments.artifacts import content_hash

__all__ = [
    "PARAPHRASES_PER_QUESTION",
    "RUBRIC_VERSION",
    "SPOT_CHECK_SAMPLE",
    "SPOT_CHECK_SEED",
    "SYSTEM_PROMPT",
    "rubric_hash",
    "user_message",
]

#: Bumped whenever :data:`SYSTEM_PROMPT` or :func:`user_message` changes meaning. A batch
#: generated under one version is never mixed with a batch generated under another.
RUBRIC_VERSION: Final = 1

#: BUILD_PLAN §P8.H1: "~3 paraphrases per question (~400 probes)".
PARAPHRASES_PER_QUESTION: Final = 3

#: The operator's spot-check (§P8.H1's QA chain). Drawn by a seeded RNG so the sample is
#: reproducible and cannot be re-drawn until it looks good.
SPOT_CHECK_SAMPLE: Final = 20
SPOT_CHECK_SEED: Final = 20260810

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
4. VARY ONLY THE SURFACE. Word order, voice, register, phrasing, question form. A
   competent reader must agree the two questions have exactly one correct answer between
   them.
5. DO NOT ANSWER, explain, hedge, or comment. Produce the rewritten question and nothing
   else.
6. DO NOT add any output-format instruction. One is appended afterwards, automatically,
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

"""Backline's answer-keyed questions, and what a seeded cache entry says.

Reads `evals/suites/core.json` from the sibling repo — a plain JSON file, so this module
needs neither Backline installed nor a database. What it produces is the *instrument*:
130 questions, each split into the part a paraphrase may rewrite and the part it may not,
each with the exact answer the answer key holds.

Three things here are load-bearing and each is asserted by a keyless test.

**The prompt splits at its first blank line** (H-060). Backline states an output contract
in every prompt — ``End your reply with a line exactly `ANSWER: $<amount>` (USD).`` — and
that contract is a wire format, not prose. All 133 prompts split cleanly, into 133 bodies
and 8 distinct tails.

**Three questions are excluded, mechanically** (PRE_REGISTRATION amendment A1). The
prompt-injection canaries carry ``expected: null`` and ``tiers: ["t2"]``: they are scored
by trace assertions rather than by an answer, so they have no ground truth to seed from
and no defined result in the equivalence matrix. The rule is stated twice — no expected,
or no ``t1`` tier — and :func:`load_suite` asserts the two select the same set.

**A seeded entry's answer is rendered from the key by code with no model in it** (H-059),
in Backline's own ``ANSWER:`` / ``FLAG:`` protocol, so its own scorer accepts it for its
own question. That diagonal property is what makes the equivalence matrix meaningful, and
``build.py`` checks it rather than assuming it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from experiments.artifacts import REPO_ROOT, read_json

__all__ = [
    "EXCLUDED_REASON",
    "SUITE_RELPATH",
    "Suite",
    "SuiteQuestion",
    "anthropic_response_body",
    "backline_repo",
    "load_suite",
    "render_answer",
    "split_prompt",
]

#: Where the suite lives inside the Backline checkout.
SUITE_RELPATH: Final = "evals/suites/core.json"

#: Recorded against every excluded id, so the artifact explains itself.
EXCLUDED_REASON: Final = "no T1 answer key (expected is null; scored by trace assertions at T2)"


def backline_repo() -> Path:
    """The sibling checkout. ``BACKLINE_REPO`` overrides; the default is `../backline`.

    A per-machine fact, so it comes from the environment with a sensible default — the
    same rule ``config/routing.yaml`` applies to a vLLM endpoint (H-011's neighbourhood).
    """
    override = os.environ.get("BACKLINE_REPO")
    return Path(override) if override else REPO_ROOT.parent / "backline"


def split_prompt(prompt: str) -> tuple[str, str]:
    """``(question body, answer-format tail)``, split at the **first** blank line.

    First rather than last: a multi-paragraph question (the reconciliation ones are)
    would otherwise have most of itself classified as protocol.
    """
    body, separator, tail = prompt.partition("\n\n")
    if not separator:
        raise ValueError(f"prompt has no blank-line split: {prompt[:80]!r}")
    return body.strip(), tail.strip()


@dataclass(frozen=True, slots=True)
class SuiteQuestion:
    """One answer-keyed question, split the way a paraphrase has to respect it."""

    id: str
    category: str
    agent: str
    answer_kind: str
    tiers: tuple[str, ...]
    #: The part a paraphrase rewrites.
    body: str
    #: The output contract. Re-attached byte-for-byte; never paraphrased (H-060).
    tail: str
    #: The answer key. Never ``None`` here — that is what the exclusion is about.
    expected: Any
    tolerance: str | None

    @property
    def prompt(self) -> str:
        """The text Backline actually sends, and therefore the text the gateway embeds."""
        return f"{self.body}\n\n{self.tail}"

    @property
    def abstains(self) -> bool:
        return self.answer_kind == "abstain"


@dataclass(frozen=True, slots=True)
class Suite:
    name: str
    suite_hash: str
    world_seed: int
    questions: tuple[SuiteQuestion, ...]
    #: ``(id, reason)`` for everything the mechanical rule removed. Never empty in
    #: practice, and reported rather than dropped.
    excluded: tuple[tuple[str, str], ...]

    def by_id(self, question_id: str) -> SuiteQuestion:
        for question in self.questions:
            if question.id == question_id:
                return question
        raise KeyError(question_id)


def load_suite(path: Path | None = None) -> Suite:
    """Read the committed suite and apply amendment A1's exclusion rule."""
    source = path or backline_repo() / SUITE_RELPATH
    if not source.exists():
        raise FileNotFoundError(
            f"Backline's suite is not at {source}. Clone the sibling repo beside this one, "
            f"or point BACKLINE_REPO at it. This is only needed to *build* the H1 artifact; "
            f"the sweep reads the committed one and needs neither."
        )
    payload = read_json(source)

    kept: list[SuiteQuestion] = []
    excluded: list[tuple[str, str]] = []
    for row in payload["questions"]:
        tiers = tuple(row["tiers"])
        has_key = row["expected"] is not None
        has_t1 = "t1" in tiers
        # Amendment A1 states the rule two ways precisely so a disagreement is loud: a
        # question with an answer key but no T1 tier (or the reverse) would mean the
        # suite's own conventions had moved under this experiment.
        if has_key != has_t1:
            raise ValueError(
                f"{row['id']}: expected-is-set ({has_key}) disagrees with t1-in-tiers "
                f"({has_t1}); amendment A1's exclusion rule no longer describes this suite"
            )
        if not has_key:
            excluded.append((row["id"], EXCLUDED_REASON))
            continue
        body, tail = split_prompt(row["prompt"])
        kept.append(
            SuiteQuestion(
                id=row["id"],
                category=row["category"],
                agent=row["agent"],
                answer_kind=row["answer_kind"],
                tiers=tiers,
                body=body,
                tail=tail,
                expected=row["expected"],
                tolerance=row.get("tolerance"),
            )
        )

    return Suite(
        name=payload["name"],
        suite_hash=payload["suite_hash"],
        world_seed=int(payload["world_seed"]),
        questions=tuple(kept),
        excluded=tuple(excluded),
    )


def render_answer(question: SuiteQuestion) -> str:
    """The reply text a cache entry for this question holds — ground truth, no model.

    Written to satisfy Backline's own extractors (`evals/answers.py`) exactly, per answer
    kind. ``build.py`` asserts the diagonal — every rendered answer scores 1.0 for its own
    question through `evals.scoring.score_t1` — so this function is checked against the
    scorer rather than against a reading of it.
    """
    kind = question.answer_kind
    expected = question.expected

    if kind == "flags":
        # Reconciliation reports findings as their own lines and carries no ANSWER line.
        # Sorted so a rebuild is byte-identical; the scorer reads a set either way.
        flags = sorted(
            (str(f["kind"]), str(f["source"]), int(f["line_id"])) for f in expected["flags"]
        )
        lines = [f"FLAG: {kind_} {source}:{line_id}" for kind_, source, line_id in flags]
        header = f"Scan complete: {len(lines)} out-of-tolerance finding(s)."
        return "\n".join([header, *lines])

    if kind == "abstain":
        # The typed flag is what scores (`_abstain` reads `outcome.abstained`), but the
        # text still states it, because the text is what a caller is served.
        return "The data does not support an answer to this question.\nANSWER: ABSTAIN"

    if kind == "money":
        value = f"${_plain(expected)}"
    elif kind == "percent":
        value = f"{_plain(expected)}%"
    elif kind == "set":
        value = "; ".join(str(item) for item in expected)
    else:  # count, bool, period, value
        value = _plain(expected) if kind == "count" else str(expected)
    return f"ANSWER: {value}"


def _plain(expected: Any) -> str:
    """A number as digits, never in exponent form. Phase 3's deviation 6, one repo over.

    Three of the suite's percent answers are stored as ``2E+1`` / ``3E+1``. ``str()`` on a
    ``Decimal`` keeps that spelling, and Backline's own ``_MONEY`` regex reads ``2E+1`` as
    **2** — so a naive render of ground truth scores 2% against an expected 20% and fails
    for its own question. ``format(value, "f")`` is the same fix Phase 3 applied when
    ``str(Decimal("0.000000000000"))`` turned out to be ``"0E-12"`` in a JSON field a
    dashboard was about to render.

    Found by the diagonal check in `build.py`, which is what that check is for.
    """
    try:
        return format(Decimal(str(expected)), "f")
    except (InvalidOperation, ValueError):
        return str(expected)


def anthropic_response_body(question: SuiteQuestion, *, model: str = "claude-sonnet-5") -> bytes:
    """The rendered answer as a real Anthropic Messages body.

    Only needed where an entry is *actually stored* — the end-to-end test that seeds a
    live gateway and checks the offline sweep agrees with the shipped decision. The sweep
    itself never touches a body: it is arithmetic over vectors and the equivalence matrix.
    """
    import json

    text = render_answer(question)
    payload = {
        "id": f"msg_h1_{question.id}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        # Plausible and fixed: nothing in H1 prices these, and a varying count would make
        # the artifact's diff noise rather than information.
        "usage": {"input_tokens": 64, "output_tokens": 16},
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")

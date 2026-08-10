"""Paraphrase generation — the only module in Phase 8 that can spend money.

**Operator-run.** Never invoked by the test suite, never by Claude Code. Its command,
with the expected cost stated before it, is in `experiments/RUNBOOK.md`.

    uv run python -m experiments.h1.generate --dry-run     # projection, $0.00
    uv run python -m experiments.h1.generate               # ~$0.30, hard stop at $1.00

**The stop is inside the harness, not in the operator's attention** (H-066, and the
Backline D-020 scar behind it). Before every call the generator prices that call's worst
case — the whole prompt at three bytes per token, plus ``max_tokens`` of output, at the
model's *dated* rate out of `config/models.yaml` — and refuses to issue it if landed spend
plus that worst case would cross :data:`CAP_USD`. It reads **committed** spend rather than
landed, which is §0.2 rule 5 applied to the experiment's own money by the repo whose
product is that rule.

**It is resumable, because a paid step that cannot resume is a paid step you pay twice.**
Questions already complete in the output file are skipped; ``--only`` re-runs a named few.
A crash, a laptop lid, or a stop at the cap costs nothing already bought.

**A candidate that fails the mechanical checks is regenerated here, before the artifact
exists** (risk register item 3). Up to :data:`MAX_ROUNDS` attempts per question; what is
still failing after that is written to ``unresolved`` and `build.py` refuses to assemble a
corpus while that list is non-empty. Silently shipping two paraphrases for one question and
three for the rest would reweight the corpus toward whatever the model found easy.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Final

import httpx

from experiments.h1 import rubric
from experiments.h1.checks import check_batch
from experiments.h1.suite import Suite, SuiteQuestion, backline_repo, load_suite
from experiments.provenance import ARTIFACTS_DIR, git_sha, provenance, read_json, write_json
from headroom.metering.cost import quantize_usd, usd_for_tokens
from headroom.metering.prices import load_price_book

__all__ = ["CAP_USD", "MODEL", "OUTPUT_PATH", "BudgetStop", "Spend", "main"]

#: BUILD_PLAN §0.6's H1 line, enforced here rather than remembered (H-066).
CAP_USD: Final = Decimal("1.00")

#: BUILD_PLAN §P8.H1 names the model. It is also priced in `config/models.yaml`, which is
#: what lets the stop below use the gateway's own dated price book rather than a constant.
MODEL: Final = "claude-haiku-4-5"

#: Three short questions plus nothing else. Generous by ~4x, and it is the *output* half of
#: every worst-case estimate, so generosity costs reservation headroom rather than money.
MAX_OUTPUT_TOKENS: Final = 400

#: Paraphrase diversity is the point; the default sampling temperature is what produces it.
#: Stated rather than defaulted so the artifact can record it.
TEMPERATURE: Final = 1.0

#: Attempts per question before it is written to ``unresolved``.
MAX_ROUNDS: Final = 3

#: H-034's constant, reused deliberately: the estimate that guards this spend is the same
#: shape as the one that guards a tenant's, and erring upward is the same trade.
EST_BYTES_PER_TOKEN: Final = 3

ANTHROPIC_VERSION: Final = "2023-06-01"
DEFAULT_BASE_URL: Final = "https://api.anthropic.com"

OUTPUT_PATH: Final = ARTIFACTS_DIR / "h1_paraphrases.json"
SCHEMA: Final = "h1_paraphrases/1"

_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*\S)\s*$")


class BudgetStop(RuntimeError):
    """The next call would cross :data:`CAP_USD`. Nothing was sent."""


@dataclass
class Spend:
    """Landed spend, and the committed-spend gate in front of the next call."""

    cap: Decimal = CAP_USD
    landed: Decimal = Decimal("0")
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def guard(self, worst_case: Decimal) -> None:
        if self.landed + worst_case > self.cap:
            raise BudgetStop(
                f"refusing to send: ${self.landed} landed + ${worst_case} worst case "
                f"would exceed the ${self.cap} cap for H1 paraphrase generation "
                f"(BUILD_PLAN §0.6, docs/DECISIONS.md H-066). "
                f"Re-run to resume: completed questions are kept."
            )

    def land(self, *, cost: Decimal, input_tokens: int, output_tokens: int) -> None:
        self.landed = quantize_usd(self.landed + cost)
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


@dataclass
class Rates:
    """The model's dated rates, resolved once out of the gateway's own price book."""

    usd_per_mtok_in: Decimal
    usd_per_mtok_out: Decimal
    effective_from: str

    @classmethod
    def resolve(cls, model: str, when: date) -> Rates:
        row = load_price_book().price_for(model, when)
        if row is None:
            raise SystemExit(
                f"{model} has no price in config/models.yaml effective {when}. "
                f"The budget stop cannot bound an unpriced call, so nothing is sent."
            )
        return cls(
            usd_per_mtok_in=row.usd_per_mtok_in,
            usd_per_mtok_out=row.usd_per_mtok_out,
            effective_from=str(row.effective_from),
        )

    def cost(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        return quantize_usd(
            usd_for_tokens(input_tokens, self.usd_per_mtok_in)
            + usd_for_tokens(output_tokens, self.usd_per_mtok_out)
        )

    def worst_case(self, *, prompt_bytes: int) -> Decimal:
        estimated_in = -(-prompt_bytes // EST_BYTES_PER_TOKEN)  # ceil, H-034's direction
        return self.cost(input_tokens=estimated_in, output_tokens=MAX_OUTPUT_TOKENS).quantize(
            Decimal("0.000001"), rounding=ROUND_CEILING
        )


@dataclass
class Batch:
    """What has been generated so far, and what still needs work."""

    questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    unresolved: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, question_id: str) -> bool:
        row = self.questions.get(question_id)
        if row is None:
            return False
        return len(row["paraphrases"]) == rubric.PARAPHRASES_PER_QUESTION


def parse_reply(text: str, *, count: int) -> list[str]:
    """Exactly ``count`` numbered lines, in order. Anything else raises.

    Strict on purpose: a reply the parser has to be clever about is a reply whose content
    nobody has actually read, and this is the one step that costs money to repeat.
    """
    found: dict[int, str] = {}
    for line in text.splitlines():
        match = _NUMBERED.match(line)
        if match:
            found[int(match.group(1))] = match.group(2).strip()
    expected = set(range(1, count + 1))
    if set(found) != expected:
        raise ValueError(f"expected numbered lines {sorted(expected)}, parsed {sorted(found)}")
    return [found[index] for index in range(1, count + 1)]


def call_model(
    client: httpx.Client, *, base_url: str, api_key: str, question: SuiteQuestion
) -> tuple[str, int, int]:
    """One Messages call. Returns ``(text, input_tokens, output_tokens)``."""
    response = client.post(
        f"{base_url.rstrip('/')}/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": TEMPERATURE,
            "system": rubric.SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": rubric.user_message(question.body)}],
        },
        timeout=120.0,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    text = "".join(
        block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"
    )
    usage = payload.get("usage") or {}
    return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def generate(
    suite: Suite,
    *,
    api_key: str,
    base_url: str,
    batch: Batch,
    spend: Spend,
    rates: Rates,
    only: set[str] | None,
) -> Batch:
    todo = [
        question
        for question in suite.questions
        if not batch.complete(question.id) and (only is None or question.id in only)
    ]
    if not todo:
        print("nothing to do: every requested question already has its paraphrases")
        return batch

    prompt_overhead = len(rubric.SYSTEM_PROMPT) + len(rubric.user_message(""))
    with httpx.Client() as client:
        for index, question in enumerate(todo, start=1):
            worst = rates.worst_case(prompt_bytes=prompt_overhead + len(question.body))
            failures: list[str] = []
            accepted: list[str] | None = None

            for attempt in range(1, MAX_ROUNDS + 1):
                spend.guard(worst)
                text, tokens_in, tokens_out = call_model(
                    client, base_url=base_url, api_key=api_key, question=question
                )
                spend.land(
                    cost=rates.cost(input_tokens=tokens_in, output_tokens=tokens_out),
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                )
                try:
                    candidates = parse_reply(text, count=rubric.PARAPHRASES_PER_QUESTION)
                except ValueError as error:
                    failures = [f"attempt {attempt}: unparseable reply — {error}"]
                    continue
                results = check_batch(body=question.body, candidates=candidates)
                bad = {position: result for position, result in results.items() if not result.ok}
                if not bad:
                    accepted = candidates
                    batch.questions[question.id] = {
                        "body": question.body,
                        "paraphrases": candidates,
                        "attempts": attempt,
                    }
                    break
                failures = [
                    f"attempt {attempt}, candidate {position + 1}: {'; '.join(result.failures)}"
                    for position, result in sorted(bad.items())
                ]

            status = "ok" if accepted else "UNRESOLVED"
            print(
                f"[{index}/{len(todo)}] {question.id:<26} {status:<11} "
                f"${spend.landed} landed / ${spend.cap} cap",
                flush=True,
            )
            if accepted is None:
                batch.unresolved.append({"id": question.id, "failures": failures})
                for line in failures:
                    print(f"    {line}", file=sys.stderr)

    return batch


def load_batch(path: Path) -> Batch:
    if not path.exists():
        return Batch()
    payload = read_json(path)
    return Batch(questions=dict(payload.get("questions", {})), unresolved=[])


def save_batch(path: Path, *, suite: Suite, batch: Batch, spend: Spend, rates: Rates) -> None:
    write_json(
        path,
        {
            "schema": SCHEMA,
            "provenance": provenance(
                produced_by="experiments/h1/generate.py",
                inputs={
                    "suite_hash": suite.suite_hash,
                    "world_seed": suite.world_seed,
                    "backline_commit": git_sha(backline_repo()),
                },
                notes=(
                    "Paid, operator-run. Regenerating this file costs money; the sweep does "
                    "not read it directly — build.py folds it into h1_corpus.json."
                ),
            ),
            "rubric": {
                "version": rubric.RUBRIC_VERSION,
                "hash": rubric.rubric_hash(),
                "model": MODEL,
                "temperature": TEMPERATURE,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "per_question": rubric.PARAPHRASES_PER_QUESTION,
            },
            "spend": {
                "usd": str(spend.landed),
                "cap_usd": str(spend.cap),
                "calls": spend.calls,
                "input_tokens": spend.input_tokens,
                "output_tokens": spend.output_tokens,
                "usd_per_mtok_in": str(rates.usd_per_mtok_in),
                "usd_per_mtok_out": str(rates.usd_per_mtok_out),
                "price_effective_from": rates.effective_from,
            },
            "questions": dict(sorted(batch.questions.items())),
            "unresolved": batch.unresolved,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h1.generate",
        description="Generate H1's paraphrase probes. PAID — operator-run, hard stop at $1.00.",
    )
    parser.add_argument("--dry-run", action="store_true", help="project the cost and send nothing")
    parser.add_argument("--only", default=None, help="comma-separated question ids to (re)do")
    parser.add_argument("--cap", default=str(CAP_USD), help=f"hard USD stop (default {CAP_USD})")
    parser.add_argument("--base-url", default=os.environ.get("H1_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--out", default=str(OUTPUT_PATH))
    args = parser.parse_args(argv)

    suite = load_suite()
    rates = Rates.resolve(MODEL, datetime.now(UTC).date())
    out = Path(args.out)
    batch = load_batch(out)
    only = set(args.only.split(",")) if args.only else None

    todo = [
        question
        for question in suite.questions
        if not batch.complete(question.id) and (only is None or question.id in only)
    ]
    overhead = len(rubric.SYSTEM_PROMPT) + len(rubric.user_message(""))
    projection = sum(
        (rates.worst_case(prompt_bytes=overhead + len(question.body)) for question in todo),
        Decimal("0"),
    )
    print(
        f"suite {suite.suite_hash} · {len(suite.questions)} keyed questions "
        f"({len(suite.excluded)} excluded) · {len(todo)} to generate\n"
        f"model {MODEL} at ${rates.usd_per_mtok_in}/${rates.usd_per_mtok_out} per MTok "
        f"(effective {rates.effective_from})\n"
        f"worst case ${projection} · cap ${args.cap} · "
        f"expected actual ~1/3 of the worst case (the estimate is deliberately high)"
    )
    if args.dry_run:
        print("\n--dry-run: nothing sent, $0.00 spent")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set — this step spends money and needs a key")

    spend = Spend(cap=Decimal(args.cap))
    try:
        generate(
            suite,
            api_key=api_key,
            base_url=args.base_url,
            batch=batch,
            spend=spend,
            rates=rates,
            only=only,
        )
    except BudgetStop as stop:
        print(f"\nSTOPPED: {stop}", file=sys.stderr)
        save_batch(out, suite=suite, batch=batch, spend=spend, rates=rates)
        return 2
    finally:
        if spend.calls:
            save_batch(out, suite=suite, batch=batch, spend=spend, rates=rates)

    done = sum(1 for question in suite.questions if batch.complete(question.id))
    print(
        f"\n{done}/{len(suite.questions)} questions complete · "
        f"{len(batch.unresolved)} unresolved · ${spend.landed} spent in {spend.calls} calls\n"
        f"wrote {out}"
    )
    return 1 if batch.unresolved else 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())

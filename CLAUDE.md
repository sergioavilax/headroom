# CLAUDE.md — how to work in this repo

Headroom is built phase by phase from [BUILD_PLAN.md](BUILD_PLAN.md). **Read the
plan's §0 before doing anything**; this file is the operational summary that governs
every session. If something in the plan looks wrong or impossible, **stop and report**
— the plan is amended in the PR that discovers the problem, never silently worked
around.

---

## ⛔ Venue rule — read this first

> **1. Claude Code runs in the LOCAL CLI, in the repo, every phase. Never the web
> UI.** The cloud sandbox cannot reach Docker, the vLLM boxes, AWS credentials,
> terraform, or kubectl — this was learned the hard way on Backline's Phase A1 and is
> not up for debate. `cd ~/code/headroom && claude`.

That is invariant 1 of BUILD_PLAN §0.2, quoted verbatim. If a session finds itself
without Docker, without the local network, or unable to run `make up`, it is in the
wrong venue: stop and say so rather than inventing a workaround.

---

## Non-negotiable invariants (BUILD_PLAN §0.2, verbatim)

1. **Claude Code runs in the LOCAL CLI, in the repo, every phase. Never the web UI.**
   The cloud sandbox cannot reach Docker, the vLLM boxes, AWS credentials, terraform,
   or kubectl — this was learned the hard way on Backline's Phase A1 and is not up for
   debate. `cd ~/code/headroom && claude`.
2. **The human runs every `terraform apply`, `terraform destroy`, `helm install`,
   `helm uninstall`, and `eksctl`/cluster mutation.** Claude Code writes files and may
   run `fmt`/`validate`/`plan`/`helm template`/`helm lint`. Two terminals: CC in one,
   the human's hands in the other.
3. **No API key ever enters the repo, a compose file's committed env, Terraform state,
   or a task definition's plain environment.** `.env` locally (gitignored), Secrets
   Manager on AWS, Kubernetes Secrets on EKS — set by the human, out of band,
   leading-space CLI calls.
4. **Keyless by default.** Every test runs on the MockProvider without a key; CI is
   fully keyless; live spend sits behind explicit `--budget` flags and `pytest -m
   live`. This is the Backline discipline, ported.
5. **Money rules.** Every experiment has a pre-committed budget (§0.6). Per-run caps
   are sized empirically, never guessed (Backline D-020's scar). The budget gate reads
   **committed** spend — reserved + landed — never landed alone (D-019's scar; in
   Headroom this isn't just a harness rule, it's the *product*: §P4).
6. **Truncated or partial upstream replies are never cached and never billed as
   complete** (D-021's scar, one layer down: a semantic cache that stores an amputated
   answer poisons every future hit).
7. **Additive phases.** Nothing existing is stripped or rewritten to serve a later
   phase; interfaces are designed so later phases extend (storage interfaces, provider
   registry, policy hooks). Modular and reusable from Phase 0 — this is also the
   operator's standing preference.
8. **Pre-registration.** Every experiment in Phase 8 has its hypothesis, metrics, and
   falsification conditions written in this plan *before* data exists. Both outcomes
   are publishable. Readings are never re-taken until they flatter.
9. **Evidence lives in the repo, outside every blast radius.** No "evidence bucket"
   inside a Terraform module (the Backline teardown ate one). Screenshots, curves, and
   reports commit to `docs/evidence/` and `experiments/results/`.

---

## Session protocol (BUILD_PLAN §0.3)

- One fresh **local CLI** Claude Code session per phase. Session starts by reading
  `BUILD_PLAN.md` + this file; ends with a `docs/PHASE_LOG.md` entry (shipped /
  deferred / deviations / gate output verbatim).
- One PR per phase, branch `claude/p<N>-<slug>`, merged by the human after the gate.
  Tests + ruff + mypy gate every PR from Phase 0 onward.
- `docs/DECISIONS.md` (H-000…) records every judgment call with alternatives and
  consequences — same format as Backline's D-log.
- `/effort max` for phases 1, 4, 5, 6, 8, 9, 10 (design-heavy); high is fine for the
  rest.
- The human is the feedback loop on anything that touches AWS/EKS: errors get pasted
  back, never guessed around.

Additional working rules that follow from the above:

- **Never leave the suite red.** If a phase is too large for one session, finish a
  coherent sub-slice, mark the remainder explicitly in `PHASE_LOG.md`, and stop
  cleanly.
- **The plan is not amended silently.** A deviation is a PHASE_LOG entry and, when it
  is a judgment call, a DECISIONS entry too.

## Operator's standing preferences

- **Modular and reusable from day one.** Interfaces before implementations where a
  later phase will plug in (storage, providers, policy hooks). Small files with one
  responsibility beat clever ones.
- **Additive-only changes.** Later phases extend; they do not rewrite what shipped.
  A refactor is allowed only with tests green and behaviour preserved, and it is
  called out in the PR.
- **UI, when the UI phases arrive (Phase 7):** true black `#000000`, cool zinc
  surfaces, mono for numbers — the operator's design language. Read the
  frontend-design skill before writing a line of it.
- **Commit messages and history are part of the artifact.** This repo is public from
  commit one. Conventional commits (`feat(scope): …`, `fix:`, `test:`, `docs:`,
  `chore:`, `ci:`), one phase per PR.
- **LF line endings everywhere** (`.gitattributes` enforces it; the repo is developed
  under WSL2 with Windows tooling within reach).

## Engineering conventions

- **Python**: 3.12, `uv` for env and locking, `ruff` (lint + format), `mypy --strict`,
  `pytest` + `pytest-asyncio`. Run `make lint typecheck test` before committing.
- **Migrations**: raw SQL in `migrations/`, applied in filename order by
  `python -m headroom.db.migrate`. Never edit an applied migration; add a new one.
  Conventions in [migrations/README.md](migrations/README.md).
- **Tests are keyless.** A test that needs a real key carries `@pytest.mark.live` and
  is excluded from every default collection. Tests that need a backing store skip
  loudly on a missing endpoint env var rather than inventing a fallback.
- **Docs**: [docs/DECISIONS.md](docs/DECISIONS.md) for judgment calls (H-NNN),
  [docs/PHASE_LOG.md](docs/PHASE_LOG.md) at the end of every phase.

## Where things live

```
headroom/     api · dialects · providers · policy · cache · metering · core · db
migrations/   raw SQL, filename order
config/       models.yaml — dialects, context windows, dated prices (Phase 3)
ui/           Next.js dashboard (Phase 7)
experiments/  Phase 8 corpus, runners, results
deploy/aws/   Phase 9 Terraform · deploy/k8s/ Phase 10 Helm + runbook
tests/        keyless; MockProvider; a sabotage test for every scar
docs/         DECISIONS.md · PHASE_LOG.md · ARCHITECTURE.md · evidence/
```

## Commands

```bash
make up          # postgres+pgvector, dynamodb-local, gateway — waits for healthy
make down        # stop the stack (keeps the db volume)
make test        # pytest — keyless; `live` excluded by default
make lint        # ruff check + format --check
make typecheck   # mypy --strict
make migrate     # apply migrations/*.sql in filename order
uv sync          # local Python env
```

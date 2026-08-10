"""H3 — failover under load, formalised (BUILD_PLAN §P8.H3).

Three pre-registered clauses, and they are answered by two different kinds of evidence::

    chaos.py     the mock chain at three fault intensities   keyless, free, in CI
    livekill.py  the two-GPU kill, from the ledger it left   the operator's desk

**No new GPU session is required** (H-067). The recording exists — the operator's
2026-08-10 run left 492 ledger rows, 270 of them on the `vllm_a → vllm_b` chain across two
kill-and-restore cycles — and what was missing was never a picture but the numbers behind
one. `livekill.py` reads those rows and adjudicates; the rows themselves are committed to
`docs/evidence/p8-experiments/` so the analysis survives the next `make test`, which
truncates the ledger (H-029's caveat).
"""

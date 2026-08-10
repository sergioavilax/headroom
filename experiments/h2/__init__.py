"""H2 — Backline's suite through the gateway (BUILD_PLAN §P8.H2, as amended by H-047).

The only experiment in Phase 8 that spends real money, and the only one Claude Code does
not run. Three modules, in the order the runbook uses them::

    preflight.py  refuse to spend $8 against a misconfigured tenant   ~$0.20 with --smoke
    bench.py      the gateway's admission cost, on the MockProvider   keyless, $0.00
    analyze.py    the ledger and Backline's summary, adjudicated      free

`preflight.py` is the module that earns its place. H-047 requires the H2 tenant to have
caching **disabled entirely**, because a cached hit answers in microseconds without
touching a provider and would turn an overhead measurement into a hit-rate measurement.
That is the shipped default, so the pre-flight is checking that nobody changed it — and it
checks four more things that would each quietly ruin a $8 run: a rate limit that sheds a
suite request, a budget cap low enough to 402 mid-suite, a key scoped away from the judge
model, and a model the routing table does not carry.
"""

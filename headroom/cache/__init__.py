"""Response cache: exact and semantic, behind one interface.

Exact-hash hits plus pgvector cosine search over per-tenant namespaces, with
replay-as-stream for streaming callers. Truncated, errored, and mid-stream-cut
responses are never written (BUILD_PLAN §0.2 invariant 6). Filled in Phase 5.
"""

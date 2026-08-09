"""Turning a request into the three values the cache looks it up by.

The exact layer needs a hash of the whole request. The semantic layer needs the user's
question on its own, plus a hash of *everything else*. All three are built here, from one
canonicalisation, so the two layers can never disagree about what a request is.

**Canonicalisation is not rewriting, and the distinction is the one H-028 turns on.**
The proxy forwards the caller's bytes verbatim and there is deliberately no code that
could rebuild a request body — that is what makes assumption A5 hold structurally. What
happens here is different in kind: the parsed body is re-serialised into a canonical form
that is **hashed and thrown away**. Nothing produced in this module is ever sent
anywhere. If it were, this file would be the bug.

**The only normalisation is key order and whitespace.** Not one field is dropped. A
caller who sends ``user: "abc"`` or ``metadata`` gets their own cache entry, and that is
the conservative trade taken deliberately: a slightly lower hit rate against a
zero-length list of fields somebody has to be *sure* cannot change a response. Widening
it later is an argument someone has to make in a new decision entry, which is the right
amount of friction for a rule whose failure mode is serving the wrong answer.

**The namespace enters the hash as well as the predicate.** :func:`namespace_for` is the
single place a tenant becomes part of a cache key, and :func:`request_hash` salts the
digest with it. So two mechanisms keep tenants apart — the salt and the SQL ``WHERE`` —
and both live downstream of this one function. That is what
``tests/test_cache_isolation.py`` sabotages: patch this, and the scoping is gone
everywhere at once, which is exactly the property a leak test should have to defeat.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from headroom.core.cache import CacheNamespace, transport_for

__all__ = [
    "canonical_json",
    "context_hash",
    "namespace_for",
    "normalise_probe",
    "request_hash",
]


def namespace_for(*, tenant_id: str, dialect: str, model: str, stream: bool) -> CacheNamespace:
    """The four facts that must match before two requests can want the same answer.

    One function, called from one place (``headroom/cache/gate.py``), so "which tenant
    is this cache lookup for" has exactly one answer and exactly one place to get it
    wrong.
    """
    return CacheNamespace(
        tenant_id=tenant_id,
        dialect=dialect,
        model=model,
        transport=transport_for(stream=stream),
    )


def canonical_json(value: Any) -> bytes:
    """The one canonical byte form of a parsed request.

    ``sort_keys`` recursively, no whitespace, and ``ensure_ascii=False`` so a literal
    ``ö`` and its ``\\u00f6`` escape canonicalise to the *same* bytes — they are the same
    string, and two requests that differ only in how their JSON encoder felt about
    non-ASCII must not miss each other. (That is the H-016 observation pointed the other
    way: on the wire the difference is load-bearing and must be preserved; in a key it is
    noise and must be erased.)
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(*parts: bytes) -> str:
    """SHA-256 over length-prefixed parts, so no concatenation can be ambiguous.

    Without the lengths, a namespace ending in ``a`` followed by a body starting with
    ``b`` would hash identically to one ending in ``ab`` followed by a body starting with
    nothing. That is a contrived collision and it is also free to make impossible.
    """
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(str(len(part)).encode("ascii"))
        hasher.update(b":")
        hasher.update(part)
    return hasher.hexdigest()


def request_hash(namespace: CacheNamespace, body: Any) -> str:
    """The exact layer's key: this namespace, this request, nothing dropped."""
    return _digest(namespace.salt.encode("utf-8"), canonical_json(body))


def context_hash(namespace: CacheNamespace, redacted: Any) -> str:
    """The semantic layer's guard rail: the request with its question blanked out.

    ``redacted`` is the body with the single user turn's content replaced by a sentinel
    (``Dialect.cache_probe``). Everything else — the system prompt, the temperature, the
    ``max_tokens``, every field this gateway has never heard of — is inside the digest,
    so a semantic hit requires all of it to match exactly and allows similarity to move
    precisely one thing: what the user asked.

    Domain-separated from :func:`request_hash` by the marker below, so the two digests
    of the same request can never be equal by accident.
    """
    return _digest(b"context", namespace.salt.encode("utf-8"), canonical_json(redacted))


def normalise_probe(text: str) -> str:
    """The text that actually gets embedded: whitespace collapsed, ends stripped.

    Deliberately shallow. Case, punctuation, and stop words are the *model's* business —
    ``bge-small`` handles them far better than a hand-written normaliser would, and every
    rule added here is a rule that has to be reproduced by whatever regenerates the
    §P8.H1 corpus. Whitespace is the exception because it is the one difference that is
    reliably meaningless and reliably produced by clients pretty-printing prompts.
    """
    return " ".join(text.split())

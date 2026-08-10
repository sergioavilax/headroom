"""Authenticating a proxied request: from a header to a tenant, or to a refusal.

This is the module BUILD_PLAN Phase 2 is about. Three decisions shape it.

**Where the credential is read from.** Both dialects, both spellings, on both routes.
The Anthropic SDK sends ``x-api-key``; the OpenAI SDK sends ``authorization: Bearer``;
Azure-flavoured clients send ``api-key``. A gateway that accepted only the "right" one
per route would make ``base_url``-override integration (assumption A2 — the way
Backline points at Headroom in Phase 8) a matter of luck. All three are accepted
everywhere, and H-010 already guarantees none of them is forwarded upstream.

**A short cache, and only of successes.** ``AUTH_CACHE_TTL_S`` is **5.0 seconds** — a
number, in the code, as BUILD_PLAN's gate requires. Successful lookups are cached;
failures never are. That asymmetry is the whole design:

* a key created a second ago must work *now*, so "unknown" is never remembered;
* a key revoked a second ago must die, so revocation invalidates the entry in this
  process immediately and the TTL is only the bound for *other* processes (Phase 9
  runs several Fargate tasks against one RDS, and nothing else coordinates them).

So the guarantee is exact and testable: **a revoked key is dead on the next request in
the process that revoked it, and dead within 5 seconds everywhere else** —
``tests/test_auth_cache.py`` drives both halves on a fake clock rather than by sleeping.

**Nothing here ever holds a plaintext key.** The cache is keyed by the SHA-256 hash, so
the only place the caller's secret exists is the local variable that hashes it. That is
not decoration: an auth cache is exactly the sort of long-lived dict that ends up in a
heap dump or a repr.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from headroom.core.cache import CacheSettings
from headroom.core.context import RequestContext
from headroom.core.errors import (
    InactiveTenant,
    MalformedCredential,
    MissingCredential,
    ModelOutOfScope,
    ProviderOutOfScope,
    RevokedCredential,
    UnknownCredential,
)
from headroom.core.limits import RateLimit
from headroom.core.storage import KeyRecord, TenantStore
from headroom.policy.keys import hash_key, looks_like_key, scope_allows

__all__ = [
    "AUTH_CACHE_TTL_S",
    "AuthCache",
    "Authenticator",
    "Principal",
    "credential_from",
]

#: How long a successful key lookup may be reused, in seconds. Short because it is the
#: cross-process revocation bound (see the module docstring); non-zero because the
#: alternative is a database round trip on the first-token path of every request.
AUTH_CACHE_TTL_S: Final = 5.0

#: Headers a virtual key may arrive in, in the order they are consulted.
BEARER_PREFIX: Final = "bearer "
CREDENTIAL_HEADERS: Final = ("authorization", "x-api-key", "api-key")

#: Above this many live entries, expired ones are swept on the next write. Only
#: successful lookups are cached, so the natural bound is "keys in use" — this is a
#: backstop against a deployment with an improbable number of them, not a policy.
_SWEEP_ABOVE: Final = 1024


def credential_from(headers: Mapping[str, str]) -> str | None:
    """The virtual key presented on this request, if any.

    ``authorization`` is read as a bearer token when it says so and taken whole when it
    does not — a caller who sends the raw key in that header is doing something
    slightly wrong that is not worth a 401 they cannot diagnose.
    """
    for name in CREDENTIAL_HEADERS:
        raw = headers.get(name)
        if raw is None:
            continue
        value = raw.strip()
        if value.lower() == BEARER_PREFIX.strip():
            continue  # the scheme with no token is no credential at all
        if value.lower().startswith(BEARER_PREFIX):
            value = value[len(BEARER_PREFIX) :].strip()
        if value:
            return value
    return None


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a request turned out to be, and what it is allowed to reach.

    Immutable and cheap to copy, because it is what the auth cache stores: a mutable
    principal handed out of a cache is a permission that changes under its holder.
    """

    tenant_id: str
    tenant_name: str
    key_id: str
    key_prefix: str
    allowed_models: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    #: Phase 4b. Both scopes' token-bucket limits, carried here because the lookup that
    #: produced this principal already read both rows — so the rate limiter reads no
    #: configuration of its own on the request path (docs/DECISIONS.md H-037). They
    #: therefore inherit this cache's TTL exactly: **a limit change takes effect on the
    #: next request in the process that made it, and within `AUTH_CACHE_TTL_S` seconds
    #: everywhere else**, which is the same guarantee, and the same sentence, as a scope
    #: change. Note what is *not* cached: the buckets themselves, which are never read.
    tenant_limits: RateLimit = field(default_factory=RateLimit)
    key_limits: RateLimit = field(default_factory=RateLimit)
    #: Phase 5. The tenant's cache policy, riding the same lookup for the same reason —
    #: and with one consequence worth stating: switching caching **off** for a tenant is
    #: effective on the next request in the process that did it, and within
    #: ``AUTH_CACHE_TTL_S`` seconds everywhere else. For up to five seconds after an
    #: operator disables it, another process may still serve a hit. That window is
    #: acceptable because every entry it can serve was already eligible and already
    #: stored; what it must never do is *widen*, which is why disabling also purges
    #: (``headroom/api/cache.py``) rather than merely flipping a flag.
    tenant_cache: CacheSettings = field(default_factory=CacheSettings)

    @classmethod
    def of(cls, record: KeyRecord) -> Principal:
        return cls(
            tenant_id=record.tenant.id,
            tenant_name=record.tenant.name,
            key_id=record.key.id,
            key_prefix=record.key.key_prefix,
            allowed_models=tuple(record.key.allowed_models),
            allowed_providers=tuple(record.key.allowed_providers),
            tenant_limits=record.tenant.limits,
            key_limits=record.key.limits,
            tenant_cache=record.tenant.cache,
        )

    # --- scope --------------------------------------------------------------------

    def require_model(self, model: str) -> None:
        """403 unless this key may ask for ``model``."""
        if not scope_allows(self.allowed_models, model):
            raise ModelOutOfScope(
                f"key {self.key_prefix}… is not scoped to model {model!r} "
                f"(allowed: {_render(self.allowed_models)})"
            )

    def require_provider(self, provider: str) -> None:
        """403 unless this key may reach ``provider``."""
        if not scope_allows(self.allowed_providers, provider):
            raise ProviderOutOfScope(
                f"key {self.key_prefix}… is not scoped to provider {provider!r} "
                f"(allowed: {_render(self.allowed_providers)})"
            )

    def permits_provider(self, provider: str) -> bool:
        """The same question as :meth:`require_provider`, asked without raising.

        Phase 6 needs it for the failover chain: a fallback the key may not reach is
        dropped from the chain rather than refused with a 403, because the *route*
        resolved to a primary the key is allowed to use and only the alternates are being
        narrowed. Authorization outranks availability — a scope is not something an
        outage may widen (docs/DECISIONS.md H-049).
        """
        return scope_allows(self.allowed_providers, provider)

    def stamp(self, ctx: RequestContext) -> None:
        """Write this identity onto the request context.

        The moment BUILD_PLAN Phase 1 left a placeholder for. From here the tenant is
        in the request log line, and in Phase 3 in every ledger row.
        """
        ctx.tenant_id = self.tenant_id
        ctx.key_id = self.key_id


def _render(scope: Sequence[str]) -> str:
    return ", ".join(repr(entry) for entry in scope) if scope else "any"


@dataclass(slots=True)
class _Entry:
    principal: Principal
    expires_at: float


class AuthCache:
    """Successful key lookups, for :data:`AUTH_CACHE_TTL_S` seconds.

    Keyed by the key's **hash**, never its plaintext. A second index maps key id →
    hash, because revocation arrives from the admin API knowing an id and nothing else.

    The clock is injected so the revocation-window test can advance time instead of
    sleeping for it — a suite that waits out its own TTLs is a suite that gets its TTLs
    shortened until the property stops being tested.
    """

    __slots__ = ("_by_hash", "_clock", "_hash_by_key_id", "_ttl_s")

    def __init__(
        self,
        ttl_s: float = AUTH_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._by_hash: dict[str, _Entry] = {}
        self._hash_by_key_id: dict[str, str] = {}

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    def get(self, key_hash: str) -> Principal | None:
        entry = self._by_hash.get(key_hash)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._forget(key_hash)
            return None
        return entry.principal

    def put(self, key_hash: str, principal: Principal) -> None:
        if self._ttl_s <= 0:
            return
        if len(self._by_hash) > _SWEEP_ABOVE:
            self._sweep()
        self._by_hash[key_hash] = _Entry(principal, self._clock() + self._ttl_s)
        self._hash_by_key_id[principal.key_id] = key_hash

    def invalidate_key(self, key_id: str) -> None:
        """Drop the entry for a key id, if this process has one. Never raises."""
        key_hash = self._hash_by_key_id.pop(key_id, None)
        if key_hash is not None:
            self._by_hash.pop(key_hash, None)

    def invalidate_tenant(self, tenant_id: str) -> None:
        """Drop every entry belonging to a tenant — deactivation is a revocation."""
        for key_hash, entry in list(self._by_hash.items()):
            if entry.principal.tenant_id == tenant_id:
                self._forget(key_hash)

    def clear(self) -> None:
        self._by_hash.clear()
        self._hash_by_key_id.clear()

    def __len__(self) -> int:
        return len(self._by_hash)

    def _forget(self, key_hash: str) -> None:
        entry = self._by_hash.pop(key_hash, None)
        if entry is not None:
            self._hash_by_key_id.pop(entry.principal.key_id, None)

    def _sweep(self) -> None:
        now = self._clock()
        for key_hash, entry in list(self._by_hash.items()):
            if entry.expires_at <= now:
                self._forget(key_hash)


class Authenticator:
    """Turns a request's headers into a :class:`Principal`, or raises.

    Held by the ``Gateway`` and shared across requests, because the cache is the point.
    """

    __slots__ = ("cache", "store")

    def __init__(self, store: TenantStore, cache: AuthCache | None = None) -> None:
        self.store = store
        self.cache = cache if cache is not None else AuthCache()

    async def authenticate(self, headers: Mapping[str, str], ctx: RequestContext) -> Principal:
        """Identify the caller and stamp the tenant onto ``ctx``.

        Raises the 401 family for "we do not know you"; scope checks (403) happen
        afterwards, at the proxy, because they need the model and the resolved
        provider.
        """
        presented = credential_from(headers)
        if presented is None:
            raise MissingCredential(
                "no virtual key on this request; send it as `Authorization: Bearer hk_…` "
                "or `x-api-key: hk_…`"
            )
        if not looks_like_key(presented):
            raise MalformedCredential(
                "the credential presented is not a Headroom virtual key (they start with `hk_`)"
            )

        key_hash = hash_key(presented)
        principal = self.cache.get(key_hash)
        if principal is None:
            principal = Principal.of(await self._load(key_hash))
            self.cache.put(key_hash, principal)
        principal.stamp(ctx)
        return principal

    async def _load(self, key_hash: str) -> KeyRecord:
        """One store lookup, turned into either a record or the right 401."""
        record = await self.store.find_by_hash(key_hash)
        if record is None:
            raise UnknownCredential("this virtual key does not exist")
        if record.key.revoked:
            raise RevokedCredential("this virtual key has been revoked")
        if not record.tenant.active:
            raise InactiveTenant("this virtual key's tenant is not active")
        return record

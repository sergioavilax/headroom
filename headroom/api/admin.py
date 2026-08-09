"""The admin surface: ``/admin/tenants`` and ``/admin/keys``, behind a root token.

**The plaintext key exists exactly once, in one response.** ``POST /admin/keys`` mints
it, hands back ``key``, and stores only its hash. No other endpoint can return it,
because no other response model has a field for it: ``KeyView`` has no ``key``, and
``KeyCreated`` — the one shape that does — is returned by exactly one route. Losing a
key means minting a new one, and the README says so (docs/DECISIONS.md H-017).

**The root token comes from the environment and nothing else** (BUILD_PLAN §0.2
invariant 3). ``HEADROOM_ADMIN_TOKEN``, named in ``.env.example``, never valued there.
The failure mode that matters is the *unset* one: an admin API that opens itself when
no token is configured would be a fully-open tenant-and-key CRUD on any deployment that
forgot one line. So unset means **503, every route, always** — the admin API is off,
loudly, and the message names the variable (H-019).

**Keys are revoked, never deleted, and so are tenants.** Phase 3's ledger attributes
every request to a key id and a tenant id forever; deleting either turns a historical
invoice into an orphan. ``DELETE`` therefore revokes/deactivates and returns the object
in its new state rather than a bare 204 — a 204 would hide the fact that something
survived.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from headroom.api.deps import GatewayDep
from headroom.api.gateway import Gateway
from headroom.api.middleware import context_of
from headroom.core.config import ADMIN_TOKEN_ENV
from headroom.core.storage import Tenant, TenantNameConflict, TenantStore, VirtualKey
from headroom.policy.keys import display_prefix, hash_key, mint_key

__all__ = ["AdminError", "admin_error_handler", "router"]

router = APIRouter(prefix="/admin", tags=["admin"])


# --- errors -----------------------------------------------------------------------


class AdminError(Exception):
    """A refusal from the admin API, in Headroom's own error shape.

    The proxy speaks the *caller's dialect* when it fails, because the caller is an
    Anthropic or OpenAI SDK that has to parse it. Nothing on ``/admin`` is: this is
    Headroom's own API, so it uses Headroom's own envelope rather than pretending to
    be a model provider.
    """

    def __init__(self, status_code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.message = message


async def admin_error_handler(request: Request, exc: Exception) -> Response:
    """Render an :class:`AdminError`, carrying the request id for correlation."""
    assert isinstance(exc, AdminError)  # registered for this type only
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"type": exc.reason, "message": exc.message},
            "headroom": {"reason": exc.reason, "request_id": context_of(request).request_id},
        },
    )


def _not_found(what: str, entity_id: str) -> AdminError:
    return AdminError(status.HTTP_404_NOT_FOUND, f"{what}_not_found", f"no {what} {entity_id!r}")


# --- authentication ----------------------------------------------------------------


async def require_admin(request: Request, gateway: GatewayDep) -> None:
    """Gate every admin route on the root token.

    Compared with :func:`secrets.compare_digest`, so the check does not leak the token
    one byte at a time to a caller with a stopwatch.
    """
    configured = gateway.admin_token
    if not configured:
        raise AdminError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "admin_api_disabled",
            f"the admin API is disabled because {ADMIN_TOKEN_ENV} is not set on the gateway",
        )
    presented = request.headers.get("authorization", "")
    prefix = "bearer "
    if presented.lower().startswith(prefix):
        presented = presented[len(prefix) :]
    if not presented or not secrets.compare_digest(presented.strip(), configured):
        raise AdminError(
            status.HTTP_401_UNAUTHORIZED,
            "admin_unauthorized",
            f"send the root admin token as `Authorization: Bearer ${ADMIN_TOKEN_ENV}`",
        )


AdminAuth = Depends(require_admin)


def _store(gateway: GatewayDep) -> TenantStore:
    return gateway.store


# --- wire models -------------------------------------------------------------------


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class TenantUpdate(BaseModel):
    """``None`` means "leave it alone" — this is a PATCH, not a replace."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None


class TenantView(BaseModel):
    id: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, tenant: Tenant) -> TenantView:
        return cls(
            id=tenant.id,
            name=tenant.name,
            active=tenant.active,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    name: str = Field(min_length=1, max_length=200)
    #: Empty means unrestricted. An entry matches exactly, or as a prefix if it ends
    #: in ``*`` — see :func:`headroom.policy.keys.scope_allows`.
    allowed_models: list[str] = Field(default_factory=list)
    allowed_providers: list[str] = Field(default_factory=list)


class KeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    #: Replaced wholesale when present, never merged — a scope that accumulates by
    #: accident is a scope nobody can reason about.
    allowed_models: list[str] | None = None
    allowed_providers: list[str] | None = None


class KeyView(BaseModel):
    """A key as the admin API describes it. **There is no plaintext field here.**"""

    id: str
    tenant_id: str
    name: str
    #: ``hk_`` plus 8 characters — enough to recognise, not enough to use (H-017).
    key_prefix: str
    allowed_models: list[str]
    allowed_providers: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None

    @classmethod
    def of(cls, key: VirtualKey) -> KeyView:
        return cls(
            id=key.id,
            tenant_id=key.tenant_id,
            name=key.name,
            key_prefix=key.key_prefix,
            allowed_models=list(key.allowed_models),
            allowed_providers=list(key.allowed_providers),
            status="revoked" if key.revoked else "active",
            created_at=key.created_at,
            updated_at=key.updated_at,
            revoked_at=key.revoked_at,
        )


class KeyCreated(KeyView):
    """The creation response — the only shape in the codebase that carries a key.

    Returned by one route. After that response is written the value is gone: it was
    never stored, never logged, and cannot be recomputed from the hash.
    """

    key: str


# --- tenants ------------------------------------------------------------------------


@router.post(
    "/tenants",
    response_model=TenantView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminAuth],
)
async def create_tenant(body: TenantCreate, gateway: GatewayDep) -> TenantView:
    try:
        tenant = await _store(gateway).create_tenant(body.name)
    except TenantNameConflict as exc:
        raise AdminError(status.HTTP_409_CONFLICT, "tenant_name_conflict", str(exc)) from exc
    return TenantView.of(tenant)


@router.get("/tenants", response_model=list[TenantView], dependencies=[AdminAuth])
async def list_tenants(gateway: GatewayDep) -> list[TenantView]:
    return [TenantView.of(tenant) for tenant in await _store(gateway).list_tenants()]


@router.get("/tenants/{tenant_id}", response_model=TenantView, dependencies=[AdminAuth])
async def get_tenant(tenant_id: str, gateway: GatewayDep) -> TenantView:
    tenant = await _store(gateway).get_tenant(tenant_id)
    if tenant is None:
        raise _not_found("tenant", tenant_id)
    return TenantView.of(tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantView, dependencies=[AdminAuth])
async def update_tenant(tenant_id: str, body: TenantUpdate, gateway: GatewayDep) -> TenantView:
    try:
        tenant = await _store(gateway).update_tenant(tenant_id, name=body.name, active=body.active)
    except TenantNameConflict as exc:
        raise AdminError(status.HTTP_409_CONFLICT, "tenant_name_conflict", str(exc)) from exc
    if tenant is None:
        raise _not_found("tenant", tenant_id)
    _forget_tenant(gateway, tenant_id)
    return TenantView.of(tenant)


@router.delete("/tenants/{tenant_id}", response_model=TenantView, dependencies=[AdminAuth])
async def deactivate_tenant(tenant_id: str, gateway: GatewayDep) -> TenantView:
    """Deactivate, not delete. Every key of an inactive tenant stops authenticating."""
    tenant = await _store(gateway).update_tenant(tenant_id, active=False)
    if tenant is None:
        raise _not_found("tenant", tenant_id)
    _forget_tenant(gateway, tenant_id)
    return TenantView.of(tenant)


# --- keys ---------------------------------------------------------------------------


@router.post(
    "/keys",
    response_model=KeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminAuth],
)
async def create_key(body: KeyCreate, gateway: GatewayDep) -> KeyCreated:
    """Mint a key. **The plaintext in this response is the only copy that will exist.**"""
    plaintext = mint_key()
    key = await _store(gateway).create_key(
        tenant_id=body.tenant_id,
        name=body.name,
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
        allowed_models=body.allowed_models,
        allowed_providers=body.allowed_providers,
    )
    if key is None:
        raise _not_found("tenant", body.tenant_id)
    return KeyCreated(**KeyView.of(key).model_dump(), key=plaintext)


@router.get("/keys", response_model=list[KeyView], dependencies=[AdminAuth])
async def list_keys(
    gateway: GatewayDep,
    tenant_id: Annotated[str | None, Query(description="narrow to one tenant")] = None,
) -> list[KeyView]:
    return [KeyView.of(key) for key in await _store(gateway).list_keys(tenant_id)]


@router.get("/keys/{key_id}", response_model=KeyView, dependencies=[AdminAuth])
async def get_key(key_id: str, gateway: GatewayDep) -> KeyView:
    key = await _store(gateway).get_key(key_id)
    if key is None:
        raise _not_found("key", key_id)
    return KeyView.of(key)


@router.patch("/keys/{key_id}", response_model=KeyView, dependencies=[AdminAuth])
async def update_key(key_id: str, body: KeyUpdate, gateway: GatewayDep) -> KeyView:
    key = await _store(gateway).update_key(
        key_id,
        name=body.name,
        allowed_models=body.allowed_models,
        allowed_providers=body.allowed_providers,
    )
    if key is None:
        raise _not_found("key", key_id)
    # A narrowed scope has to bite now, not in five seconds: widening is harmless to
    # forget, narrowing is not, and one invalidation covers both.
    _forget_key(gateway, key_id)
    return KeyView.of(key)


@router.delete("/keys/{key_id}", response_model=KeyView, dependencies=[AdminAuth])
async def revoke_key(key_id: str, gateway: GatewayDep) -> KeyView:
    """Revoke, not delete — Phase 3's ledger keeps pointing at this id forever."""
    key = await _store(gateway).revoke_key(key_id)
    if key is None:
        raise _not_found("key", key_id)
    _forget_key(gateway, key_id)
    return KeyView.of(key)


# --- cache coherence ----------------------------------------------------------------
#
# The auth cache's TTL (5 s) is the bound for *other* processes. In this one, a
# revocation takes effect on the very next request — which is what an operator reaching
# for `DELETE /admin/keys/…` during an incident actually means by "revoke".


def _forget_key(gateway: Gateway, key_id: str) -> None:
    gateway.authenticator.cache.invalidate_key(key_id)


def _forget_tenant(gateway: Gateway, tenant_id: str) -> None:
    gateway.authenticator.cache.invalidate_tenant(tenant_id)

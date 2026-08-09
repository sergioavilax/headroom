"""``/admin/budgets`` — set a cap, and see what is left of it.

Four routes behind the same root admin token as the rest of ``/admin`` (H-019), and
one shape: **budget, spent, reserved, remaining**, which is the phase brief's
requirement verbatim and also the only four numbers that answer "why was that request
refused". ``committed`` (spent + reserved) is published beside them because that is the
figure the gate actually tests, and BUILD_PLAN §0.2 rule 5 is about nothing else.

**Money leaves as a string**, exactly as it does from ``/admin/usage`` — JSON has one
numeric type and it is a double, and a budget rendered through a float is a budget that
can be off by a cent for reasons nobody can explain.

**The single-tenant GET sweeps.** Reading a budget releases holds whose owner never
came back, so the ``reserved`` figure an operator sees during an incident is the live
one rather than one inflated by processes that died. It is a GET with a side effect,
taken deliberately: releasing an *already expired* hold changes nothing live, the
operation is idempotent, and the alternative is a number that is quietly wrong at
exactly the moment somebody is relying on it. The list route does not sweep — it is an
overview, and a ``Scan`` that wrote to every item it read would be a surprising thing
for a listing to do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from headroom.api.admin import AdminAuth, AdminError
from headroom.api.deps import GatewayDep
from headroom.core.budgets import WINDOWS, Budget, BudgetScope
from headroom.core.ledger import format_usd

__all__ = ["router"]

router = APIRouter(prefix="/admin/budgets", tags=["admin", "budgets"])


def _now() -> datetime:
    return datetime.now(UTC)


class BudgetSet(BaseModel):
    """``PUT /admin/budgets/{tenant_id}``."""

    model_config = ConfigDict(extra="forbid")

    #: A **quoted decimal string**, the same rule ``config/models.yaml`` enforces on
    #: rates (H-023) and for the same reason: JSON's only numeric type is a double, so
    #: a bare ``25.10`` has already lost exactness before this process sees it. The
    #: refusal below names the field and says what to send instead.
    usd: str
    window: str = Field(default="monthly")

    @field_validator("usd", mode="before")
    @classmethod
    def _quoted_money(cls, value: Any) -> str:
        if isinstance(value, float):
            raise ValueError(
                f"usd must be a quoted decimal string so it stays exact — {value!r} "
                f'arrived as a JSON number, which is a double (send "{value}")'
            )
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError('usd must be a quoted decimal string, e.g. "25.00"')
        return str(value)

    @field_validator("window")
    @classmethod
    def _known_window(cls, value: str) -> str:
        if value not in WINDOWS:
            raise ValueError(f"window must be one of {', '.join(WINDOWS)}, got {value!r}")
        return value

    def amount(self) -> Decimal:
        try:
            parsed = Decimal(self.usd)
        except InvalidOperation as exc:
            raise ValueError(f"usd is not a decimal number: {self.usd!r}") from exc
        if parsed < 0:
            raise ValueError(f"usd must not be negative, got {self.usd!r}")
        return parsed


class BudgetView(BaseModel):
    """One scope's cap and what is left of it, for this window."""

    model_config = ConfigDict(extra="forbid")

    scope: str
    scope_kind: str
    scope_id: str
    window: str
    #: ``2026-08`` for a monthly cap, ``total`` for a lifetime one. It is also the
    #: reset mechanism: a new month is a new id, and the first request of the month
    #: rolls the counters. There is no reset job to fail.
    window_id: str

    usd: str
    spent: str
    reserved: str
    remaining: str
    #: Spent **plus** reserved — what the gate compares against the cap. Published
    #: because a dashboard reading landed spend alone is D-019 with a nicer font.
    committed: str

    #: Requests admitted and not yet settled, right now.
    reservations: int
    #: Holds handed back because the process that took them never returned. Non-zero is
    #: worth an alarm in Phase 9: it means requests are dying between admission and
    #: settlement.
    expired_releases: int
    expired_released: str

    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def of(cls, budget: Budget) -> BudgetView:
        return cls(
            scope=budget.scope.key,
            scope_kind=budget.scope.kind,
            scope_id=budget.scope.id,
            window=budget.window,
            window_id=budget.window_id,
            usd=format_usd(budget.usd) or "0",
            spent=format_usd(budget.spent) or "0",
            reserved=format_usd(budget.reserved) or "0",
            remaining=format_usd(budget.remaining) or "0",
            committed=format_usd(budget.committed) or "0",
            reservations=budget.reservations,
            expired_releases=budget.expired_releases,
            expired_released=format_usd(budget.expired_released) or "0",
            created_at=budget.created_at,
            updated_at=budget.updated_at,
        )


async def _require_tenant(gateway: GatewayDep, tenant_id: str) -> None:
    """404 for a tenant that does not exist.

    Checked against the control plane rather than assumed, so a typo'd id cannot
    quietly create a budget for a tenant nobody will ever authenticate as — an orphan
    item that silently enforces nothing and shows up in the list forever.
    """
    if await gateway.store.get_tenant(tenant_id) is None:
        raise AdminError(status.HTTP_404_NOT_FOUND, "tenant_not_found", f"no tenant {tenant_id!r}")


@router.get("", response_model=list[BudgetView], dependencies=[AdminAuth])
async def list_budgets(gateway: GatewayDep) -> list[BudgetView]:
    """Every configured budget. Scopes with no cap do not appear — they are uncapped."""
    found = await gateway.budgets.store.list_budgets(when=_now())
    return [BudgetView.of(budget) for budget in found]


@router.get("/{tenant_id}", response_model=BudgetView, dependencies=[AdminAuth])
async def get_budget(tenant_id: str, gateway: GatewayDep) -> BudgetView:
    """One tenant's cap, spend, live reservations, and remaining headroom."""
    budget = await gateway.budgets.store.get(BudgetScope.tenant(tenant_id), when=_now())
    if budget is None:
        raise AdminError(
            status.HTTP_404_NOT_FOUND,
            "budget_not_found",
            f"no budget for tenant {tenant_id!r}; this tenant is uncapped",
        )
    return BudgetView.of(budget)


@router.put("/{tenant_id}", response_model=BudgetView, dependencies=[AdminAuth])
async def set_budget(
    tenant_id: str, body: Annotated[BudgetSet, ...], gateway: GatewayDep
) -> BudgetView:
    """Create or change a tenant's cap.

    ``PUT`` rather than ``POST`` because it is idempotent: a scope has at most one
    budget, and sending the same body twice leaves the same state. Raising or lowering
    the amount within a window moves ``remaining`` by the difference, so spend already
    recorded and holds already taken survive the change — lowering a cap below what has
    been spent leaves negative headroom, which refuses the next request, which is the
    correct behaviour and not an error.
    """
    await _require_tenant(gateway, tenant_id)
    try:
        amount = body.amount()
    except ValueError as exc:
        # The literal, not `status.HTTP_422_*`: Starlette 1.6 deprecates the old
        # spelling and the replacement is not in every version this runs against, and a
        # new deprecation warning in the suite is a real cost (Phase 0's gate counts them).
        raise AdminError(422, "invalid_budget", str(exc)) from exc
    budget = await gateway.budgets.store.set_budget(
        BudgetScope.tenant(tenant_id), usd=amount, window=body.window, when=_now()
    )
    return BudgetView.of(budget)


@router.delete("/{tenant_id}", response_model=BudgetView, dependencies=[AdminAuth])
async def clear_budget(tenant_id: str, gateway: GatewayDep) -> BudgetView:
    """Remove a cap; the tenant becomes uncapped. Returns the budget as it last stood.

    Unlike tenants and keys, a budget *is* deleted rather than tombstoned (H-022's rule
    does not extend here): nothing references it, the ledger already holds every row it
    ever gated, and a "revoked" cap would be a cap an operator has to reason about
    during the next incident.
    """
    scope = BudgetScope.tenant(tenant_id)
    budget = await gateway.budgets.store.get(scope, when=_now())
    if budget is None or not await gateway.budgets.store.clear_budget(scope):
        raise AdminError(
            status.HTTP_404_NOT_FOUND,
            "budget_not_found",
            f"no budget for tenant {tenant_id!r}",
        )
    return BudgetView.of(budget)

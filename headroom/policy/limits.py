"""The rate-limit gate: which buckets a request consumes, in what order, and why.

``headroom/db/buckets.py`` makes one bucket's arithmetic atomic. This file decides which
buckets a request touches and what it costs them — the same split the budget half has
between ``db/budgets.py`` and ``policy/budgets.py``.

Four decisions, and each is a question the plan or the brief asks by name.

**Which scopes?** BUILD_PLAN says *"per key and per tenant"*, and both are enforced from
the first line — unlike per-key *budgets*, which H-033 deferred. The asymmetry is real
rather than inconsistent: a budget admission takes a **reservation**, so enforcing two of
them means two holds and a compensating release when the second fails, which is a new home
for D-019. A bucket consumption holds nothing and settles never. Two consumptions are two
independent facts, and the failure mode when the second refuses is bounded (below), where
the budget's would be a stranded hold on a tenant's month.

**In what order?** ``key`` before ``tenant``, and ``requests`` before ``tokens``. Both
orderings put the cheaper, more likely refusal first, so that when a request is going to
be refused it has consumed as little as possible of anything shared. A key's bucket is
private to one credential; the tenant's is shared by all of them.

**What happens to what was already consumed when a later bucket refuses?** *Nothing.* It
stays consumed, and no compensating release is written. This is the decision most worth
arguing with, so here is the argument: a budget is a **stock** and a rate limit is a
**flow**. An over-charge against a budget persists for the rest of the month and must be
corrected, which is why settlement exists. An over-charge against a token bucket is erased
by the bucket's own refill within one emission interval — the refill *is* the compensating
transaction, it runs continuously, and it costs nothing. Writing a refund would mean a
second conditional write per bucket on every refusal, to repair an error that time repairs
for free, and would introduce the one thing this design does not have: an operation whose
absence breaks an invariant. The bounded cost is that a request refused by its third bucket
leaves up to two units consumed elsewhere — the limiter is fractionally *stricter* than
configured, which is the direction the whole phase errs in deliberately.

**Where does it sit relative to auth, budget, and routing?** After the scope checks and
**before the budget reservation** — see :meth:`RateLimiter.admit` and docs/DECISIONS.md
H-039. In one line: the rate limiter is the load shedder, and it protects the budget gate
from the bursts the budget gate serialises on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from headroom.core.context import RequestContext
from headroom.core.errors import RateLimited, RateLimitScopeExhausted
from headroom.core.limits import (
    DIM_REQUESTS,
    DIM_TOKENS,
    REFUSED_EXCEEDS_CAPACITY,
    SCOPE_KEY,
    SCOPE_TENANT,
    BucketKey,
    Consumption,
    RateLimit,
    RateLimitStore,
)
from headroom.policy.budgets import Estimate

__all__ = ["RATE_LIMIT_LIMITED", "RATE_LIMIT_OK", "RateLimiter", "token_cost"]

#: What the gate recorded on the context, for the log line and the Phase 7 dashboard.
#: ``None`` — the field's default — means no limit applied to this request at all.
RATE_LIMIT_OK: Final = "ok"
RATE_LIMIT_LIMITED: Final = "limited"


def token_cost(estimate: Estimate) -> int:
    """What a request costs its tokens-per-minute bucket, before it runs.

    The **same** bound the budget gate reserves against, in tokens rather than dollars:
    the prompt half from the body's own size and the generated half from ``max_tokens``
    (H-034). One estimate, computed once per request in ``headroom/api/proxy.py`` and
    handed to both gates, so the two halves of Phase 4 can never disagree about how large
    a request is.

    One property worth noticing: this works on models the *budget* cannot see. An unpriced
    model estimates $0 and is invisible to the cap (H-034's named trap), but its token
    counts are still real, so a tokens-per-minute limit keeps holding. The rate limiter
    needs no price book at all.
    """
    return estimate.input_tokens + estimate.output_tokens


@dataclass(frozen=True, slots=True)
class _Charge:
    """One bucket this request has to get past."""

    key: BucketKey
    limit_per_min: int
    cost: int


@dataclass(slots=True)
class RateLimiter:
    """Admission against the token buckets, wired to one :class:`RateLimitStore`.

    Held on the ``Gateway`` beside the budget gate, and used at exactly one point in
    ``headroom/api/proxy.py``. There is no settlement half — that is the whole difference
    between a flow and a stock.
    """

    store: RateLimitStore

    async def admit(
        self,
        ctx: RequestContext,
        *,
        tenant_limits: RateLimit,
        key_limits: RateLimit,
        estimate: Estimate,
    ) -> None:
        """Consume this request's units from every configured bucket, or refuse it.

        Raises :class:`~headroom.core.errors.RateLimited` (429) on the first bucket that
        says no. Because it raises, and because the proxy calls it before
        ``provider.open``, a refused request never reaches an upstream — asserted in
        ``tests/test_rate_limit_gate.py`` against the MockProvider's own record of what it
        was handed rather than reasoned about.

        **The gate order, decided here** (H-039). The proxy runs, in this sequence:
        authenticate (401) → read the body → model scope (403) → route → provider scope
        (403) → **rate limit (429)** → budget reservation (402) → open the upstream.

        * *After the scope checks*, for H-032's reason unchanged: a key reaching past its
          permissions should be told that, not told to slow down.
        * *Before the budget*, for three. A rate-limited request that had already reserved
          budget would have to hand the hold straight back — a compensating release on the
          hot path, which is the one shape this phase refuses to add. A burst is also
          exactly the traffic the budget gate is worst at: every request in it serialises
          on one DynamoDB item, so shedding it one step earlier is what keeps the cap's
          latency bounded under the load the limiter exists for. And it is cheaper: a
          bucket consumption is one conditional write against a per-scope item, a budget
          admission is one against the *single* item every request for that tenant
          contends on.
        * A request that is both over its limit and over its budget therefore answers 429,
          and answers 402 on the retry. That ordering is deliberate too: "slow down" is
          advice a client can act on immediately, and one wasted round trip is cheaper than
          hammering the budget item to find out.
        """
        charges = self._charges(ctx, tenant_limits, key_limits, estimate)
        if not charges:
            return

        for charge in charges:
            outcome = await self.store.consume(
                charge.key,
                limit_per_min=charge.limit_per_min,
                cost=charge.cost,
                when=ctx.started_at,
            )
            if not outcome.admitted:
                ctx.rate_limit_status = RATE_LIMIT_LIMITED
                ctx.rate_limit_scope = charge.key.label
                raise _refusal(outcome)
        ctx.rate_limit_status = RATE_LIMIT_OK

    @staticmethod
    def _charges(
        ctx: RequestContext, tenant_limits: RateLimit, key_limits: RateLimit, estimate: Estimate
    ) -> list[_Charge]:
        """The buckets this request must pass, narrowest and cheapest first.

        Only configured dimensions appear: an unlimited scope is never consumed and never
        written, so a deployment that sets no limits does no DynamoDB work at all on this
        path — which is what keeps this phase additive for every tenant nobody has capped.
        """
        tokens = token_cost(estimate)
        scopes = (
            (SCOPE_KEY, ctx.key_id, key_limits),
            (SCOPE_TENANT, ctx.tenant_id, tenant_limits),
        )
        charges: list[_Charge] = []
        for scope_kind, scope_id, limits in scopes:
            if scope_id is None or not limits.configured:
                continue
            for dimension, cost in ((DIM_REQUESTS, 1), (DIM_TOKENS, tokens)):
                limit = limits.per_min(dimension)
                if limit is None:
                    continue
                charges.append(
                    _Charge(
                        key=BucketKey(
                            scope_kind=scope_kind, scope_id=scope_id, dimension=dimension
                        ),
                        limit_per_min=limit,
                        cost=cost,
                    )
                )
        return charges

    async def aclose(self) -> None:
        await self.store.aclose()


def _refusal(outcome: Consumption) -> RateLimited:
    """The 429, with the numbers a developer needs to unblock themselves.

    Every figure is the caller's own — their limit, their bucket, their request's size —
    so quoting them discloses nothing they do not already own, and withholding them makes
    the most common support question ("why 429?") unanswerable without an operator. The
    same argument the budget refusal's message is built on.
    """
    key = outcome.key
    unit = "requests" if key.dimension == DIM_REQUESTS else "tokens"
    if outcome.refusal == REFUSED_EXCEEDS_CAPACITY:
        return RateLimitScopeExhausted(
            f"this request needs {outcome.cost} {unit}, which is more than the whole "
            f"{key.scope_kind} {unit}-per-minute allowance of {outcome.limit_per_min}; "
            f"waiting will not help — raise the limit or lower max_tokens",
            scope=key.label,
            limit_per_min=outcome.limit_per_min,
            remaining=outcome.available,
            retry_after_s=None,
        )
    return RateLimited(
        f"this request would exceed the {key.scope_kind}'s rate limit of "
        f"{outcome.limit_per_min} {unit} per minute: it needs {outcome.cost} and "
        f"{outcome.available} {'is' if outcome.available == 1 else 'are'} available; "
        f"retry in {outcome.retry_after_s}s",
        scope=key.label,
        limit_per_min=outcome.limit_per_min,
        remaining=outcome.available,
        retry_after_s=outcome.retry_after_s,
    )

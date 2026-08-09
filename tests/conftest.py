"""The gateway fixture every proxy test runs against — keyless, mock-only, isolated.

BUILD_PLAN §0.2 invariant 4: every test runs on the MockProvider without a key. The
fixture below builds a complete gateway whose only provider is the mock, installs it on
the real application object, and drives it over ASGI — so tests exercise middleware,
routing, the proxy, and the dialects together rather than calling internals. A test
that passes here is a test about the thing the operator will curl.

Each test gets its own script book and provider, so a fault injected in one test cannot
leak into the next — the failure mode that makes fault-injection suites untrustworthy.

Since Phase 2 it also gets its own **control plane**: a fresh in-memory store holding
one tenant and one unrestricted virtual key, an authenticator with its own cache, and a
root admin token. Isolation matters as much here as it does for faults — a cached auth
decision or a revoked key leaking between tests would make the revocation-window
assertions meaningless.

Since Phase 3 it also gets its own **ledger**, and one thing about it is deliberate:
the price book is loaded from the **committed** ``config/models.yaml`` rather than from
a fixture. The exact-cost assertions are therefore assertions about the file the
gateway ships with, so a fat-fingered rate in the real config fails the suite instead of
sailing through it. A test that needs a price *boundary* builds its own book in code —
the shipped mock entries are flat on purpose, so that test costs never move on a
calendar day (docs/DECISIONS.md H-023).

Since Phase 4 it also gets its own **budget store**, and by default it is the in-memory
one with **no cap configured**, which means the gate is a no-op for every test written
before this phase. That is the whole reason those tests did not have to change: an
unbudgeted tenant is admitted, nothing is held, and nothing is settled. A test that
wants a cap asks for one (``harness.set_budget("0.001")``); the stampede builds its own
harness against DynamoDB Local, because a dict cannot be raced.

The construction itself lives in ``tests/support/harness.py`` so that other fixtures can
reuse it — see :func:`headroom.tests.support.harness.gateway_harness`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from .support.harness import GatewayHarness, gateway_harness


@pytest.fixture
async def gateway() -> AsyncIterator[GatewayHarness]:
    """A keyless gateway wired to a fresh MockProvider, served over ASGI."""
    async with gateway_harness() as harness:
        yield harness

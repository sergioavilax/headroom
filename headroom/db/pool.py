"""The Postgres connection pool, created lazily and closed once.

**Lazily**, and that is the only interesting thing in this file. If the pool were built
during the application lifespan, a gateway would refuse to start without a database —
which would break two things the project already relies on. CI's ``image`` job builds
the container and smokes ``/healthz`` with no Postgres anywhere near it, and H-000 says
``/healthz`` is liveness only: a process that is up and serving says so, and does not
pretend to have checked its dependencies. A pool that connects on first *use* keeps
both of those true, and moves the failure to the request that actually needed the
database, where it can be reported honestly (503, ``control_plane_unavailable``).

The gateway itself never sees asyncpg: everything above this file talks to a
``TenantStore``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from headroom.core.errors import ConfigurationError, ControlPlaneUnavailable
from headroom.db.migrate import database_url

__all__ = ["DatabasePool", "translate_db_error"]

#: Connections held per gateway process. Small: every query in the control plane is a
#: single indexed statement, and Phase 9 runs several tasks against one RDS instance.
DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 10

#: Seconds to wait for a connection before giving up and answering 503. Deliberately
#: short — a caller waiting on the gateway's own bookkeeping is a caller whose
#: first-token latency is already ruined.
DEFAULT_TIMEOUT_S = 5.0

#: The schema is not there. Not transient, and not the caller's fault.
_SCHEMA_ERRORS: tuple[type[Exception], ...] = (
    asyncpg.UndefinedTableError,
    asyncpg.UndefinedColumnError,
)

#: The server is not reachable, or not ready. ``PostgresConnectionError`` is listed
#: explicitly because asyncpg derives it from ``PostgresError``, not from ``OSError``.
_UNREACHABLE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.InterfaceError,
)


def translate_db_error(exc: Exception) -> Exception:
    """Map a database failure onto the gateway's error taxonomy.

    Two outcomes, and the distinction is worth the function. A missing table means the
    operator has not run the migrations — a misconfiguration, 500, and the message says
    the command to run (the H-009 rule: a 500 that names the knob is not a generic
    500). Anything to do with reaching the server is transient and answers 503.
    """
    if isinstance(exc, _SCHEMA_ERRORS):
        return ConfigurationError(
            f"the control-plane schema is missing or out of date ({exc}); "
            "apply migrations with `make migrate`"
        )
    if isinstance(exc, _UNREACHABLE_ERRORS):
        return ControlPlaneUnavailable(f"cannot reach the control-plane database: {exc}")
    return exc


class DatabasePool:
    """An asyncpg pool that comes into existence the first time it is needed."""

    __slots__ = ("_lock", "_max_size", "_min_size", "_pool", "_timeout_s", "_url")

    def __init__(
        self,
        url: str | None = None,
        *,
        min_size: int = DEFAULT_MIN_SIZE,
        max_size: int = DEFAULT_MAX_SIZE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._url = url or database_url()
        self._min_size = min_size
        self._max_size = max_size
        self._timeout_s = timeout_s
        self._pool: asyncpg.Pool[Any] | None = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        return self._url

    async def _ensure(self) -> asyncpg.Pool[Any]:
        if self._pool is not None:
            return self._pool
        # Under the lock, and checked again inside it: a burst of first requests must
        # build one pool, not one pool each.
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        self._url,
                        min_size=self._min_size,
                        max_size=self._max_size,
                        timeout=self._timeout_s,
                        command_timeout=self._timeout_s,
                    )
                except Exception as exc:
                    raise translate_db_error(exc) from exc
            return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection[Any]]:
        """A pooled connection, with database failures already translated."""
        pool = await self._ensure()
        try:
            async with pool.acquire() as conn:
                yield conn
        except Exception as exc:
            translated = translate_db_error(exc)
            if translated is exc:
                raise
            raise translated from exc

    async def aclose(self) -> None:
        """Close the pool if one was ever opened. Safe to call more than once."""
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

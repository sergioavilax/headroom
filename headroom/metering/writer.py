"""Getting the ledger row to the database without ever making a caller wait for it.

The constraint is stated in the phase brief and it is not negotiable: *a slow database
must never block or delay the stream to the client.* First-token latency is the
product, and a synchronous ``INSERT`` after the last byte would put a Postgres round
trip on the tail of every request — invisible in a benchmark, ruinous under the
concurrency Phase 8 measures. So the row is handed to a bounded queue and a single
background task drains it.

**The delivery guarantee, stated plainly (docs/DECISIONS.md H-027):**

*At most once, in process, best effort.* A row that is queued when the process dies is
lost. There is no write-ahead log, no on-disk spool, no acknowledgement to the caller.
Three things make that acceptable rather than careless:

1. **A graceful stop loses nothing.** ``aclose`` drains the queue before the app exits,
   so a deploy, a scale-in, or a ``docker compose down`` costs no rows. Only a
   ``SIGKILL`` or a crash does.
2. **A lost row is reconstructible.** The same figures — tokens, cost, cost status,
   timings — go out on the structured request log line, which is written *before* the
   row is queued and lands in the container's stdout. That is why Phase 3 grows the
   log line rather than treating it as superseded by the ledger.
3. **The alternative is worse in the way that matters.** The durable version puts the
   write on the request path, and a database hiccup then becomes a latency incident
   for every caller. Phase 4's budget reservations are a different matter entirely and
   deliberately do *not* use this path: a stale ledger row is a reporting gap, a lost
   reservation is D-019's scar.

**Backpressure is a drop, and the drop is counted.** The queue is bounded; when it is
full the row is discarded and ``dropped`` increments, with a warning naming the request
id. An unbounded queue would trade a reporting gap for an out-of-memory kill, which
takes the gateway down with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Final

from headroom.core.ledger import LedgerEntry, LedgerStore

__all__ = ["LedgerWriter"]

LOGGER: Final = logging.getLogger("headroom.metering")


def _warn(event: str, **fields: Any) -> None:
    """One JSON line, same shape as the request log, so ``| jq`` reads both."""
    LOGGER.warning(json.dumps({"event": event, **fields}, separators=(",", ":")))


#: Rows in flight before the writer starts dropping. Sized as "a burst nobody is
#: watching": at a few hundred requests a second this is tens of seconds of backlog,
#: far longer than any transient database stall worth surviving, and small enough that
#: a permanently wedged database costs bounded memory instead of the process.
DEFAULT_QUEUE_SIZE: Final = 10_000

#: How long ``aclose`` waits for the backlog before giving up and reporting the loss.
#: Bounded because a shutdown that hangs on an unreachable database is a deploy that
#: never finishes, and the log line has the figures anyway.
DEFAULT_DRAIN_TIMEOUT_S: Final = 5.0


class LedgerWriter:
    """A bounded queue and one drain task, between the proxy and the ledger store."""

    __slots__ = ("_drain_timeout_s", "_queue", "_store", "_worker", "dropped", "failed", "written")

    def __init__(
        self,
        store: LedgerStore,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        drain_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S,
    ) -> None:
        self._store = store
        self._queue: asyncio.Queue[LedgerEntry] = asyncio.Queue(maxsize=queue_size)
        self._drain_timeout_s = drain_timeout_s
        self._worker: asyncio.Task[None] | None = None
        #: Rows that reached the store. Read by tests and by the Phase 7 health tile.
        self.written = 0
        #: Rows the store rejected. The row is gone; the log line is the fallback.
        self.failed = 0
        #: Rows discarded because the queue was full. Non-zero means the database
        #: cannot keep up and the ledger is now an undercount — a number worth alerting on.
        self.dropped = 0

    @property
    def pending(self) -> int:
        """Rows queued and not yet written."""
        return self._queue.qsize()

    def submit(self, entry: LedgerEntry) -> bool:
        """Queue a row. Never blocks, never raises, never awaits.

        Called from the request path, including from inside the streaming generator's
        final frames, so the one thing it must not do is yield control. Returns whether
        the row was accepted, for tests and for the drop counter's sake.
        """
        self._ensure_worker()
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.dropped += 1
            _warn(
                "ledger_row_dropped",
                request_id=entry.request_id,
                pending=self._queue.qsize(),
                dropped=self.dropped,
            )
            return False
        return True

    async def drain(self) -> None:
        """Wait until every queued row has been written. For tests and for shutdown."""
        self._ensure_worker()
        await self._queue.join()

    async def aclose(self) -> None:
        """Drain what is queued, then stop the worker. Safe to call more than once."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._queue.join(), timeout=self._drain_timeout_s)
        remaining = self._queue.qsize()
        if remaining:
            self.dropped += remaining
            _warn("ledger_shutdown_incomplete", unwritten=remaining)
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    # --- internals -------------------------------------------------------------

    def _ensure_worker(self) -> None:
        """Start the drain task on first use.

        Lazily, for the same reason the connection pool is lazy (``headroom/db/pool.py``):
        constructing a gateway must not require a running event loop or a reachable
        database. The first submitted row is the first moment both are guaranteed.
        """
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain_forever())

    async def _drain_forever(self) -> None:
        while True:
            entry = await self._queue.get()
            try:
                await self._store.record(entry)
                self.written += 1
            except asyncio.CancelledError:
                # Cancelled mid-write: the row is gone. Counted rather than silent, and
                # `task_done` still runs so a concurrent `join` cannot hang on it.
                self.dropped += 1
                self._queue.task_done()
                raise
            except Exception as exc:
                # A failed write must not kill the writer: the next request's row is
                # still worth having, and the log line already carries this one.
                self.failed += 1
                _warn("ledger_write_failed", request_id=entry.request_id, error=str(exc))
                self._queue.task_done()
                continue
            self._queue.task_done()

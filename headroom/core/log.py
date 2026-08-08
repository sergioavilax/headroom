"""One structured line per request, emitted when the context closes.

Deliberately the smallest thing that is still useful. Phase 3 writes the durable record
to the Postgres ledger and Phase 7 reads it; this exists so that between now and then a
compose stack, a container, and the operator's terminal all say the same thing about a
request — and so ``x-headroom-request-id`` from a caller's screenshot leads somewhere.

JSON rather than prose because the fields are the point: ``docker compose logs gateway
| jq 'select(.outcome != "ok")'`` is a debugging session, and a formatted sentence is
not.

The logger is configured explicitly at startup rather than left to inherit. Python's
default root level is ``WARNING``, so a request logger that merely exists emits
nothing — a docstring promising structured logs above a stream of silence.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Final

from headroom.core.context import RequestContext

__all__ = ["REQUEST_LOGGER", "configure_logging", "log_request"]

PACKAGE_LOGGER: Final = logging.getLogger("headroom")
REQUEST_LOGGER: Final = logging.getLogger("headroom.request")

#: Override to quieten a load test, or raise to ``DEBUG`` while chasing something.
LOG_LEVEL_ENV: Final = "HEADROOM_LOG_LEVEL"


def configure_logging(level: str | None = None) -> None:
    """Give Headroom's loggers a level and a handler. Idempotent; called at startup.

    Records go to stdout as bare JSON with no prefix, and do not propagate to the root
    logger — uvicorn installs its own handlers there, and propagating would print every
    line twice in one format and once in another.
    """
    resolved = (os.environ.get(LOG_LEVEL_ENV) or level or "INFO").upper()
    PACKAGE_LOGGER.setLevel(resolved)
    if not PACKAGE_LOGGER.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        PACKAGE_LOGGER.addHandler(handler)
    PACKAGE_LOGGER.propagate = False


def log_request(ctx: RequestContext) -> None:
    """Emit the completed request's fields as one JSON line."""
    if not REQUEST_LOGGER.isEnabledFor(logging.INFO):
        return
    REQUEST_LOGGER.info(json.dumps(ctx.as_log_fields(), separators=(",", ":")))

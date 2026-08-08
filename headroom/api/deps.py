"""The dependency seam between the ASGI app and the gateway it serves.

One function, and it exists for a reason: the app object is module-level (H-000, so
uvicorn and the test client can both import it), but the ``Gateway`` it uses must be
swappable. The test suite builds a mock-only gateway per test and installs it with
FastAPI's ``dependency_overrides``; production builds one in the lifespan. Neither
reaches into the other's globals.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from headroom.api.gateway import Gateway
from headroom.core.errors import ConfigurationError

__all__ = ["GatewayDep", "get_gateway"]


def get_gateway(request: Request) -> Gateway:
    """The gateway this app was started with."""
    gateway = getattr(request.app.state, "gateway", None)
    if not isinstance(gateway, Gateway):  # pragma: no cover - lifespan always sets it
        raise ConfigurationError(
            "no gateway on this application; it was not started through its lifespan"
        )
    return gateway


#: Annotated form rather than a ``Depends`` default, so route signatures stay readable
#: and the linter has no function call in a default argument to object to.
GatewayDep = Annotated[Gateway, Depends(get_gateway)]

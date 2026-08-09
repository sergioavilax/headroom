"""Finding the backing stores without an env incantation, and without lying about it.

Phase 0 made the service-backed tests skip unless ``DATABASE_URL`` and
``DYNAMODB_ENDPOINT_URL`` were exported by hand, which meant the documented workflow
(``make up && make test``) quietly ran two fewer tests than the operator thought. The
fix is to fall back to the compose endpoints — the ones ``make up`` just started, on
the H-006 host ports — while keeping the distinction that matters (docs/DECISIONS.md
H-012):

* **Inferred** endpoint, nothing listening → skip, saying where it looked. A fresh
  clone with no stack up must not fail; that is not a broken repo.
* **Explicit** endpoint, nothing listening → fail. Someone stated the store is there
  (CI does, in its workflow env), and a silent skip would make the job a liar about
  its own service containers.

So the local experience improves and the CI guarantee — these tests *run*, they do not
skip — is unchanged.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = ["Endpoint", "resolve_endpoint"]

#: The compose stack as seen from the host — H-006 moved these off 5432/8000 so
#: Headroom and Backline can run at the same time, which Phase 8's H2 requires.
COMPOSE_DATABASE_URL = "postgresql://headroom:headroom@localhost:5433/headroom"
COMPOSE_DYNAMODB_ENDPOINT_URL = "http://localhost:8001"

#: Short on purpose: this runs on a fresh clone with nothing up, and a multi-second
#: hang before an inevitable skip is how people learn to distrust a suite.
_PROBE_TIMEOUT_S = 0.4


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A resolved service endpoint and how it was arrived at."""

    url: str
    explicit: bool

    @property
    def reachable(self) -> bool:
        """Whether something is listening. A TCP connect, not a protocol handshake —
        the tests themselves do the real talking."""
        parts = urlsplit(self.url)
        if parts.hostname is None:
            return False
        port = parts.port or (5432 if parts.scheme.startswith("postgres") else 80)
        try:
            with socket.create_connection((parts.hostname, port), timeout=_PROBE_TIMEOUT_S):
                return True
        except OSError:
            return False

    @property
    def skip_reason(self) -> str | None:
        """Why this test should skip, or ``None`` if it should run (or fail loudly)."""
        if self.reachable or self.explicit:
            return None
        return f"nothing listening at {self.url} — run `make up` to start the stack"


def resolve_endpoint(env_var: str, compose_default: str) -> Endpoint:
    """The configured endpoint, or the compose one if nothing was configured."""
    configured = os.environ.get(env_var)
    if configured:
        return Endpoint(url=configured, explicit=True)
    return Endpoint(url=compose_default, explicit=False)

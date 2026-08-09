"""Which headers cross the gateway, in each direction, and why.

A proxy that forwards headers naively breaks in three specific ways, so each direction
gets its own rule.

**Credentials never cross.** The caller's ``authorization`` / ``x-api-key`` is stripped
before anything is sent upstream, and the provider's credential is added by the
provider. In Phase 1 nothing authenticates yet, but Phase 2 makes those client headers
*virtual keys* — ``hk_...`` values that mean something only to Headroom — and a gateway
that forwarded them would be leaking its own tenants' secrets to Anthropic. Writing the
rule now means Phase 2 adds keys rather than also fixing a leak.

**Framing headers never cross.** httpx transparently decompresses the response body, so
forwarding the upstream's ``content-encoding: gzip`` alongside already-decoded bytes
tells the client to gunzip plaintext. ``content-length`` is equally poisonous once the
body is re-framed as a stream. Both are dropped, along with the hop-by-hop headers that
by definition describe a single connection.

**Everything else does cross**, in both directions — a deny-list, not an allow-list.
Rate-limit headers, ``retry-after``, and the provider's own request id are exactly what
a caller needs to behave well, and a gateway that swallows them makes its callers worse
citizens than they were without it. The cost of the deny-list is having to notice a new
framing header; the cost of an allow-list is silently discarding signal, forever.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

__all__ = [
    "CONTROL_PREFIX",
    "control_headers",
    "forward_request_headers",
    "forward_response_headers",
]

#: Gateway-control headers. Stripped from what goes upstream and handed to the
#: provider separately — they steer Headroom, and a real provider must never see them.
CONTROL_PREFIX: Final = "x-headroom-"

#: Per RFC 9110 these describe one hop and must not be forwarded by a proxy.
_HOP_BY_HOP: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: The client's credentials and anything describing the inbound connection's framing.
_REQUEST_DENY: Final = _HOP_BY_HOP | {
    "authorization",
    "x-api-key",
    "api-key",
    "cookie",
    "host",
    "content-length",
    # httpx negotiates its own encoding with the upstream; echoing the client's
    # preference would claim support the gateway may not have decoded for.
    "accept-encoding",
}

#: httpx already decoded the body, so the upstream's framing no longer describes it.
_RESPONSE_DENY: Final = _HOP_BY_HOP | {"content-length", "content-encoding"}


def forward_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Client headers to send upstream: everything but credentials, framing, control."""
    return {
        name.lower(): value
        for name, value in headers.items()
        if name.lower() not in _REQUEST_DENY and not name.lower().startswith(CONTROL_PREFIX)
    }


def control_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The ``x-headroom-*`` headers, with the prefix removed."""
    return {
        name.lower().removeprefix(CONTROL_PREFIX): value
        for name, value in headers.items()
        if name.lower().startswith(CONTROL_PREFIX)
    }


def forward_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Upstream headers to send downstream: everything but framing and hop-by-hop."""
    return {
        name.lower(): value for name, value in headers.items() if name.lower() not in _RESPONSE_DENY
    }

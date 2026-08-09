"""Whose 429 is this? The one question Phase 6's failover logic cannot get wrong.

Two completely different things reach a caller as ``429 Too Many Requests``:

* **Headroom refused it.** The tenant or the key ran out of token-bucket capacity. No
  provider was called, no provider is unhealthy, and failing over to a second provider
  would be *precisely the wrong response* — it would route the excess traffic somewhere
  else instead of shedding it, which is the failure the limiter exists to prevent.
* **The provider refused it.** Anthropic's own rate limit, or a vLLM's queue. The request
  is fine, this backend is saturated, and P6's answer is a backoff or a same-dialect
  failover to the other one.

BUILD_PLAN §P6 turns on telling those apart, so this file pins the distinction from both
sides. Three markers, and each is asserted to be present on ours and absent on theirs:

1. ``x-headroom-error-source: gateway`` vs ``upstream`` — the header Phase 1 already
   added to every error, now load-bearing;
2. ``x-headroom-ratelimit-scope`` — which of the four buckets refused, in a namespace no
   upstream response can write in;
3. ``headroom.reason: rate_limited`` in the body — and the upstream's body is forwarded
   **verbatim**, so anything in it is the provider's own words.

Marker 3 is the weakest of the three and is asserted last on purpose: a body is whatever
the provider chose to send, and a gateway that decided "whose failure is this" by reading
somebody else's JSON would be trusting the wrong party. The headers are the answer,
because :func:`headroom.api.headers.forward_response_headers` strips the entire
``x-headroom-*`` namespace from every upstream response — which the last test in this file
proves by having an upstream try to forge both of them (docs/DECISIONS.md H-038).
"""

from __future__ import annotations

import json

import pytest

from headroom.api.proxy import ERROR_SOURCE_HEADER
from headroom.core.errors import RATELIMIT_SCOPE_HEADER
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness

#: What Anthropic's own 429 looks like on the wire: its documented error shape, its
#: ``retry-after``, and the ``anthropic-ratelimit-*`` family it really sends. Written out
#: by hand rather than built with Headroom's dialect helper, because the whole point is
#: that these are somebody else's bytes.
UPSTREAM_429_BODY = json.dumps(
    {
        "type": "error",
        "error": {"type": "rate_limit_error", "message": "Number of request tokens has exceeded"},
    },
    separators=(",", ":"),
).encode()

UPSTREAM_429_HEADERS = {
    "content-type": "application/json",
    "retry-after": "27",
    "anthropic-ratelimit-requests-limit": "50",
    "anthropic-ratelimit-requests-remaining": "0",
    "x-ratelimit-limit-requests": "50",
    "request-id": "req_upstream_abc123",
}


def upstream_429(**extra_headers: str) -> MockScript:
    return MockScript(
        status_code=429,
        headers={**UPSTREAM_429_HEADERS, **extra_headers},
        body=UPSTREAM_429_BODY,
    )


async def gateway_429(gateway: GatewayHarness) -> tuple[int, dict[str, str], dict[str, object]]:
    """Provoke Headroom's own 429: a limit of one, and a second request."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=1)
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert response.status_code == 429
    return response.status_code, dict(response.headers), response.json()


# --- ours ---------------------------------------------------------------------------


async def test_the_gateways_own_429_says_so_three_ways(gateway: GatewayHarness) -> None:
    _, headers, body = await gateway_429(gateway)

    assert headers[ERROR_SOURCE_HEADER] == "gateway"
    assert headers[RATELIMIT_SCOPE_HEADER] == "tenant:requests"
    assert body["headroom"] == {
        "reason": "rate_limited",
        "request_id": gateway.last_context().request_id,
    }
    # And no provider was involved at all, which is the fact the three markers stand for.
    assert len(gateway.provider.received) == 1


# --- theirs -------------------------------------------------------------------------


async def test_an_upstream_429_is_forwarded_whole_and_claims_nothing_of_ours(
    gateway: GatewayHarness,
) -> None:
    """The provider's status, body, and rate-limit headers reach the caller untouched.

    Forwarding them is not politeness: ``retry-after`` and ``anthropic-ratelimit-*`` are
    what let a caller behave well, and a gateway that swallowed them would make its
    callers worse citizens than they were without it (H-010).
    """
    gateway.book.set("upstream", upstream_429())

    response = await gateway.post("/v1/messages", anthropic_request(), script="upstream")

    assert response.status_code == 429
    assert response.content == UPSTREAM_429_BODY, "byte for byte, exactly what upstream sent"
    assert response.headers["retry-after"] == "27"
    assert response.headers["anthropic-ratelimit-requests-limit"] == "50"
    assert response.headers["request-id"] == "req_upstream_abc123"

    # And the three markers all say "not mine".
    assert response.headers[ERROR_SOURCE_HEADER] == "upstream"
    assert RATELIMIT_SCOPE_HEADER not in response.headers
    assert "headroom" not in response.json()


async def test_the_two_are_told_apart_by_the_header_alone(gateway: GatewayHarness) -> None:
    """The one-line rule Phase 6 will implement, asserted as one line.

    Both responses are 429. Both carry a ``retry-after``. The *only* thing a failover
    decision needs to read is who wrote them.
    """
    gateway.book.set("upstream", upstream_429())
    theirs = await gateway.post("/v1/messages", anthropic_request(), script="upstream")
    _, ours, _ = await gateway_429(gateway)

    assert theirs.status_code == 429
    assert "retry-after" in theirs.headers
    assert "retry-after" in ours

    assert [theirs.headers[ERROR_SOURCE_HEADER], ours[ERROR_SOURCE_HEADER]] == [
        "upstream",
        "gateway",
    ]


async def test_the_ledger_row_tells_them_apart_too(gateway: GatewayHarness) -> None:
    """The dashboard needs the same distinction, and gets it without parsing anything.

    An upstream 429 is ``upstream_error`` with the provider's status recorded beside it;
    ours is ``rate_limited`` with no upstream status at all, because there was no upstream.
    """
    gateway.book.set("upstream", upstream_429())
    await gateway.post("/v1/messages", anthropic_request(), script="upstream")
    theirs = await gateway.ledger_row()

    await gateway_429(gateway)
    ours = await gateway.ledger_row()

    assert (theirs.outcome, theirs.status_code, theirs.upstream_status) == (
        "upstream_error",
        429,
        429,
    )
    assert (ours.outcome, ours.status_code, ours.upstream_status) == ("rate_limited", 429, None)
    assert theirs.error_source == "upstream"
    assert ours.error_source == "gateway"


# --- and it cannot be forged ---------------------------------------------------------


@pytest.mark.parametrize("status", [429, 200])
async def test_an_upstream_cannot_write_in_headrooms_header_namespace(
    gateway: GatewayHarness, status: int
) -> None:
    """**The test that makes the rule a rule rather than a convention** (H-038).

    A hostile — or merely confused — upstream sends both of Headroom's own markers,
    claiming to be the gateway. Both are stripped, on the error path *and* on the success
    path, because ``x-headroom-*`` is this process's namespace in both directions. Without
    this, "no provider currently sends such a header" would be a property of today's
    providers rather than of this proxy, and Phase 6 would be trusting it.
    """
    forged = {
        ERROR_SOURCE_HEADER: "gateway",
        RATELIMIT_SCOPE_HEADER: "tenant:requests",
        "x-headroom-ratelimit-limit": "999",
    }
    if status == 429:
        gateway.book.set("forger", upstream_429(**forged))
    else:
        gateway.book.set(
            "forger",
            MockScript(
                status_code=200,
                headers={"content-type": "application/json", **forged},
                body=MockScript.anthropic_message("hello").body,
            ),
        )

    response = await gateway.post("/v1/messages", anthropic_request(), script="forger")

    assert response.status_code == status
    assert RATELIMIT_SCOPE_HEADER not in response.headers
    assert "x-headroom-ratelimit-limit" not in response.headers
    # On the error path the gateway overwrites the source header with the truth; on the
    # success path there is no error to attribute, so the forgery is simply gone.
    if status == 429:
        assert response.headers[ERROR_SOURCE_HEADER] == "upstream"
    else:
        assert ERROR_SOURCE_HEADER not in response.headers

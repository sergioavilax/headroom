"""The error taxonomy, and the rule that governs it: **never hide what happened.**

BUILD_PLAN Phase 1: *"upstream errors map to honest downstream errors with the
upstream's status preserved."* A gateway that turns every upstream failure into a
generic 500 destroys the caller's ability to react correctly — a 429 needs a backoff,
a 400 needs a fixed request, a 529 needs a retry, and all three look identical once
they have been flattened. Worse for this project specifically: Phase 6's failover and
Phase 8's H3 are *measured* on the difference between those cases.

So there are exactly two shapes of failure here, and both are specific:

* **The upstream answered with an error.** Its status and its body are forwarded
  verbatim — the gateway adds nothing and reshapes nothing. Those responses never
  reach this module; see ``headroom.api.proxy``.
* **There is no upstream answer to forward** (timeout, transport failure, a stream cut
  after the response began, a routing or configuration fault). Then the gateway has to
  invent a status, and the classes below fix which one, so the same fault always
  surfaces the same way. Each carries a stable ``reason`` that appears in the response
  body under ``headroom.reason`` and in the ledger — the machine-readable detail that
  a coarse HTTP status cannot carry.

Phase 2 adds a third shape that is neither: **the request never reached an upstream at
all**, because the caller could not be identified (401), was identified and denied
(403), or could not be identified *because Headroom's own store was down* (503). Those
live at the bottom of this module and follow the same rule — a fixed status per class,
a stable ``reason``, nothing invented per call site.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "AuthenticationFailed",
    "BudgetExceeded",
    "ConfigurationError",
    "ControlPlaneUnavailable",
    "HeadroomError",
    "InactiveTenant",
    "InvalidRequestBody",
    "MalformedCredential",
    "MissingCredential",
    "ModelNotRouted",
    "ModelOutOfScope",
    "ProviderError",
    "ProviderOutOfScope",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RateLimitScopeExhausted",
    "RateLimited",
    "RevokedCredential",
    "ScopeDenied",
    "UnknownCredential",
    "UpstreamStreamCut",
]

# Where the blame lies. Surfaced as the `x-headroom-error-source` response header, so
# an operator can tell "the provider rejected this" from "we broke" without reading
# the body — and so the Phase 7 dashboard can chart the two separately.
SOURCE_UPSTREAM: Final = "upstream"
SOURCE_GATEWAY: Final = "gateway"

# A stream that ended without its dialect's terminal marker. Not an exception — no
# error was raised anywhere, the bytes simply stopped — which is exactly why it needs
# a name of its own. See ``headroom.api.proxy``.
REASON_STREAM_INCOMPLETE: Final = "upstream_stream_incomplete"


class HeadroomError(Exception):
    """A failure the gateway must describe, because the upstream did not describe it."""

    status_code: int = 500
    reason: str = "gateway_error"
    source: str = SOURCE_GATEWAY

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def headers(self) -> dict[str, str]:
        """Extra response headers this failure carries. Empty for almost every one.

        Added in Phase 4b for the one refusal that has something a caller must act on
        *outside* the body — a rate limit's ``retry-after`` and the bucket that produced
        it. A property rather than a constructor argument so that no call site can
        invent headers for an error class that should not have any.
        """
        return {}


# --- no answer from upstream ------------------------------------------------------


class ProviderError(HeadroomError):
    """The provider could not deliver a response the gateway can forward."""

    source = SOURCE_UPSTREAM


class ProviderTimeout(ProviderError):
    """Upstream took longer than the configured timeout.

    504, because that is what a gateway that did not get an answer in time means. The
    request may well have been accepted and billed upstream — the caller is told the
    truth (we do not know) rather than a comfortable lie.
    """

    status_code = 504
    reason = "upstream_timeout"


class ProviderUnavailable(ProviderError):
    """Could not connect, or the connection failed before any response.

    502, the classic "bad gateway": the request never got far enough to have a status.
    """

    status_code = 502
    reason = "upstream_unavailable"


class UpstreamStreamCut(ProviderError):
    """The upstream connection died *after* the response began streaming.

    The 502 on this class is a fallback for the rare non-streamed path. On a streamed
    response the status line is long gone by the time this is raised, so it cannot be
    used — the caller is told with a terminal error event inside the stream instead,
    which is the whole subject of ``tests/test_mid_stream_cut.py``.
    """

    status_code = 502
    reason = "upstream_stream_cut"


# --- the gateway itself --------------------------------------------------------


class ModelNotRouted(HeadroomError):
    """No routing rule claims this model for this dialect.

    404 matches what both providers return for an unknown model, so an SDK's
    ``NotFoundError`` means the same thing whether or not Headroom is in the path.
    """

    status_code = 404
    reason = "model_not_routed"


class InvalidRequestBody(HeadroomError):
    """The body is not JSON, or is missing the fields routing needs.

    400 — and the gateway does not attempt to validate the request beyond what it must
    read to route it (BUILD_PLAN L4: passthrough-first). Everything else is the
    provider's business, and its rejection is more accurate than ours would be.
    """

    status_code = 400
    reason = "invalid_request_body"


class ConfigurationError(HeadroomError):
    """A route resolves to a provider this deployment cannot use — a missing key, say.

    500, because the operator misconfigured the gateway and nothing the caller does
    will fix it. The message names the exact environment variable that is missing:
    a 500 that says *which* knob is wrong is not the generic 500 the plan forbids.
    """

    status_code = 500
    reason = "gateway_misconfigured"


class ControlPlaneUnavailable(HeadroomError):
    """Headroom's own store — the one holding tenants and keys — is unreachable.

    503 and not 500 (Phase 2, extending the H-009 table): the request was well formed,
    the gateway is correctly configured, and the condition is transient, so the honest
    answer is "try again" rather than "you broke something" or "we are broken forever".
    A gateway that cannot authenticate must not serve — failing open here would hand
    every tenant's budget to whoever asked first.
    """

    status_code = 503
    reason = "control_plane_unavailable"


# --- the caller's credential (Phase 2) --------------------------------------------
#
# 401 and 403 answer two different questions and the split is exact: **401 means we do
# not know who you are** (no key, an unusable key, a key that has been switched off),
# **403 means we know exactly who you are and you may not have this** (a live key
# reaching past its scope). Every subclass keeps its own ``reason`` so the log line and
# the ``headroom`` block say which of the five it was, while the status stays coarse —
# telling an anonymous caller *why* their credential failed is how key-probing gets
# efficient.


class AuthenticationFailed(HeadroomError):
    """No usable virtual key on this request. 401."""

    status_code = 401
    reason = "authentication_failed"
    source = SOURCE_GATEWAY


class MissingCredential(AuthenticationFailed):
    """No ``authorization`` / ``x-api-key`` header at all."""

    reason = "missing_api_key"


class MalformedCredential(AuthenticationFailed):
    """Something was presented, but it is not shaped like an ``hk_`` key."""

    reason = "malformed_api_key"


class UnknownCredential(AuthenticationFailed):
    """A well-formed key that this deployment has never issued."""

    reason = "unknown_api_key"


class RevokedCredential(AuthenticationFailed):
    """The key existed and was revoked. Dead from the revocation onward."""

    reason = "revoked_api_key"


class InactiveTenant(AuthenticationFailed):
    """The key is live but its tenant is switched off.

    401 rather than 403, deliberately: the tenant is not *being denied a resource*,
    the tenant is not currently a tenant. Deactivating a tenant has to be as final as
    revoking every one of its keys, which is what makes it usable in an incident.
    """

    reason = "inactive_tenant"


class BudgetExceeded(HeadroomError):
    """The tenant's committed spend would pass its cap if this request ran. **402.**

    Three statuses were on the table and the choice matters more than it looks
    (docs/DECISIONS.md H-032).

    **429** is the tempting one and it is wrong: it means *slow down*, and every SDK in
    the world responds to it by retrying with backoff. A budget refusal does not heal
    with time inside its window, so a 429 would turn one refused request into a retry
    storm against the very item the gate serialises on — the failure mode this phase
    exists to prevent, arriving through the front door.

    **403** is defensible and loses information: it is already this gateway's answer for
    "your key is not scoped to that", and an operator reading a dashboard needs
    "out of money" and "out of scope" to be different bars on the chart.

    **402 Payment Required** says exactly what happened. It is a status no SDK retries
    automatically, and the dialects render it in their own vocabulary for insufficient
    funds — Anthropic's ``billing_error``, OpenAI's ``insufficient_quota`` — so a client
    library raises something a developer can act on rather than something generic.

    No ``retry-after``: the honest value would be "when your window rolls", which for a
    lifetime budget is never, and a header that says *retry* invites the retry this
    class exists to discourage. The window's reset is in the message instead.
    """

    status_code = 402
    reason = "budget_exceeded"
    source = SOURCE_GATEWAY


#: Headers a gateway rate-limit refusal carries, in Headroom's own namespace. The
#: namespace is the point: ``headroom/api/headers.py`` strips every ``x-headroom-*``
#: header from every upstream response, so a header in this family can only have been
#: written by this process. See :class:`RateLimited` and docs/DECISIONS.md H-038.
RATELIMIT_SCOPE_HEADER: Final = "x-headroom-ratelimit-scope"
RATELIMIT_LIMIT_HEADER: Final = "x-headroom-ratelimit-limit"
RATELIMIT_REMAINING_HEADER: Final = "x-headroom-ratelimit-remaining"
RATELIMIT_RESET_HEADER: Final = "x-headroom-ratelimit-reset"


class RateLimited(HeadroomError):
    """The tenant's or the key's token bucket had no room for this request. **429.**

    Unlike a budget refusal — which is 402 precisely *because* retrying does not help
    (H-032) — this one is the case 429 was invented for: it heals with time, the amount
    of time is known exactly, and the honest thing to do is to say so. So this class
    carries a ``retry-after`` and a budget refusal deliberately does not.

    **The distinction P6 needs.** A provider's own 429 also reaches the caller, forwarded
    verbatim with its status and body untouched (``headroom.api.proxy``), and the failover
    logic in Phase 6 has to tell "our limiter refused this" from "Anthropic refused this"
    — they call for opposite responses: shed load locally, or fail over to another
    provider. Three independent markers make that decision cheap and unambiguous, and any
    one of them is sufficient:

    * ``x-headroom-error-source: gateway`` — an upstream error always says ``upstream``;
    * ``headroom.reason: rate_limited`` in the body — a forwarded upstream body is never
      given a ``headroom`` block;
    * ``x-headroom-ratelimit-scope: tenant:requests`` — which bucket, and in a header
      namespace no upstream response is allowed to write in (H-010, extended in H-038).

    The dialect's own ``rate_limit_error`` type is used for the body, because the caller's
    SDK has to raise something it knows; the *precision* lives in ``headroom.reason``,
    which is H-009's rule unchanged.
    """

    status_code = 429
    reason = "rate_limited"
    source = SOURCE_GATEWAY

    def __init__(
        self,
        message: str,
        *,
        scope: str,
        limit_per_min: int,
        remaining: int,
        retry_after_s: int | None,
    ) -> None:
        super().__init__(message)
        self.scope = scope
        self.limit_per_min = limit_per_min
        self.remaining = remaining
        self.retry_after_s = retry_after_s

    @property
    def headers(self) -> dict[str, str]:
        built = {
            RATELIMIT_SCOPE_HEADER: self.scope,
            RATELIMIT_LIMIT_HEADER: str(self.limit_per_min),
            RATELIMIT_REMAINING_HEADER: str(self.remaining),
        }
        if self.retry_after_s is not None:
            # `retry-after` for every client that already knows it, and the namespaced
            # spelling beside it so a Headroom-aware caller (the Phase 6 failover logic,
            # the Phase 7 dashboard) never has to guess which hop set it.
            built["retry-after"] = str(self.retry_after_s)
            built[RATELIMIT_RESET_HEADER] = str(self.retry_after_s)
        return built


class RateLimitScopeExhausted(RateLimited):
    """The request is larger than the bucket's whole capacity: waiting cannot help.

    Reachable only on the tokens dimension — a request costs one unit of the requests
    bucket and every limit is at least 1 — when a caller's estimated token count exceeds
    the scope's entire tokens-per-minute allowance. It stays a 429 rather than becoming a
    4xx about the request's size, because the request is fine and the *limit* is what
    cannot accommodate it; but it carries **no ``retry-after``**, because every value
    would be a lie and the honest header is the absent one. The message says what to
    change (H-038).
    """

    reason = "rate_limit_exceeds_capacity"


class ScopeDenied(HeadroomError):
    """A valid, live key reaching past what it was scoped to. 403."""

    status_code = 403
    reason = "out_of_scope"
    source = SOURCE_GATEWAY


class ModelOutOfScope(ScopeDenied):
    """The key is not scoped to this model."""

    reason = "model_out_of_scope"


class ProviderOutOfScope(ScopeDenied):
    """The key is not scoped to the provider this model routes to."""

    reason = "provider_out_of_scope"

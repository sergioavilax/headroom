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

"""Loading ``config/routing.yaml``: which providers exist, and who serves which model.

Two rules shape this file.

**No secret is ever a config value.** A provider spec names the *environment variable*
that holds its credential (``api_key_env``), never the credential. The committed YAML
is therefore safe to read, diff, and publish, and BUILD_PLAN §0.2 invariant 3 is
enforced by the schema rather than by reviewer vigilance — there is no field to put a
key in.

**Configuration is validated at load, credentials are resolved at use.** A route
pointing at an undefined provider is a typo the operator should hear about at startup,
so it fails there. A missing ``ANTHROPIC_API_KEY`` is *not*: a gateway serving mock and
vLLM traffic must boot and run fine without one (invariant 4), and the honest place for
that error is the first request that actually needs Anthropic.

Phase 6 adds failover chains to a route, and one thing about *where* they are validated
is deliberate. Shape — undefined names, a repeated provider, an attempt budget above the
ceiling — is checked here, at load. **Whether a fallback can actually speak the rule's
dialect is checked in ``headroom/api/gateway.py``**, because that is the module which
imports the provider kinds and therefore the only one where the registry is guaranteed
populated. BUILD_PLAN L4 stops being a property of the data structure and becomes a
property the gateway refuses to start without.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from headroom.core.errors import ConfigurationError
from headroom.policy.routing import MAX_ATTEMPT_LIMIT, RouteRule, RoutingTable

__all__ = [
    "ADMIN_TOKEN_ENV",
    "CONFIG_PATH_ENV",
    "MODELS_CONFIG_PATH_ENV",
    "GatewayConfig",
    "ProviderSpec",
    "RouteSpec",
    "load_config",
]

#: Where the routing config lives in a source checkout and in the container image.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "routing.yaml"

#: Point this at another file to run a gateway with a different routing table — how
#: the test suite gets a mock-only gateway without editing the committed config.
CONFIG_PATH_ENV = "HEADROOM_ROUTING_CONFIG"

#: Where the dated price schedules live (Phase 3): ``config/models.yaml``, loaded by
#: ``headroom/metering/prices.py``. It is named here, beside the routing path, because
#: this module is where environment-variable names live — the loader itself is
#: metering's, since prices are reference data rather than policy (H-014's split).
MODELS_CONFIG_PATH_ENV = "HEADROOM_MODELS_CONFIG"

#: The root admin credential for ``/admin/*`` (Phase 2), read from the environment and
#: from nowhere else — invariant 3 again, and the reason it is named here beside the
#: other environment knobs rather than being a field some YAML could hold. Unset means
#: the admin API is **off**, not open (docs/DECISIONS.md H-019).
ADMIN_TOKEN_ENV = "HEADROOM_ADMIN_TOKEN"


class ProviderSpec(BaseModel):
    """One configured upstream."""

    model_config = ConfigDict(extra="forbid")

    #: Which implementation: a key registered in the provider registry.
    kind: str
    #: Fixed endpoint. Mutually usable with ``base_url_env``, which wins when set.
    base_url: str | None = None
    #: Environment variable holding the endpoint — for per-machine facts such as
    #: ``VLLM_BASE_URL``, which differs between the operator's desk and a container.
    base_url_env: str | None = None
    #: Environment variable holding the credential. Never the credential itself.
    api_key_env: str | None = None
    #: Dialect-specific pin, e.g. Anthropic's ``anthropic-version``.
    api_version: str | None = None
    #: Per-read timeout; doubles as the mid-stream stall detector.
    read_timeout_s: float | None = None

    def settings(self) -> dict[str, Any]:
        """Constructor keywords for the registered factory, minus the unset ones."""
        return {
            key: value
            for key, value in self.model_dump().items()
            if key != "kind" and value is not None
        }


class RouteSpec(BaseModel):
    """Models starting with ``prefix`` are served by ``provider``, then by ``fallbacks``.

    Phase 6 added the last two fields, and their defaults are the whole reason this phase
    is additive: a rule with no ``fallbacks`` and no ``max_attempts`` makes exactly one
    attempt, with no backoff and no breaker interference — bit for bit what Phase 5 did.
    Failover is opt-in, per route, in the file the operator already edits when a GPU
    moves (BUILD_PLAN §P6, docs/DECISIONS.md H-049).
    """

    model_config = ConfigDict(extra="forbid")

    #: Empty string is legal and means "everything else".
    prefix: str = ""
    provider: str
    #: Same-dialect alternates, in order. Cross-dialect fallbacks are rejected at
    #: gateway build (BUILD_PLAN L4), not merely discouraged in a comment.
    fallbacks: list[str] = Field(default_factory=list)
    #: Attempts one request may make. Absent means one per candidate. Above the chain
    #: length it wraps, which is how a single-provider route asks to be retried rather
    #: than abandoned. Bounded because an unbounded retry budget on the first-token path
    #: is a denial of service one config edit away.
    max_attempts: int | None = Field(default=None, ge=1, le=MAX_ATTEMPT_LIMIT)

    @model_validator(mode="after")
    def _chain_is_distinct_and_excludes_the_primary(self) -> Self:
        chain = [self.provider, *self.fallbacks]
        if len(set(chain)) != len(chain):
            raise ValueError(
                f"route {self.prefix!r} names a provider twice in its chain ({chain}); "
                "a repeat buys nothing the `max_attempts` wrap does not, and it would "
                "make `failover_hops` count a hop that never left the provider"
            )
        return self


class GatewayConfig(BaseModel):
    """The whole routing configuration."""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    #: dialect name -> ordered rules. Order in the file is irrelevant; the routing
    #: table sorts by specificity.
    routes: dict[str, list[RouteSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _routes_point_at_real_providers(self) -> Self:
        """Every name a rule can reach — primary *and* fallback — must be defined.

        A typo in a fallback is worse than a typo in a primary, because it stays
        invisible until the day the primary is down, which is the worst possible day to
        discover a configuration error.
        """
        for dialect, rules in self.routes.items():
            for rule in rules:
                for name in (rule.provider, *rule.fallbacks):
                    if name not in self.providers:
                        known = ", ".join(sorted(self.providers)) or "none"
                        raise ValueError(
                            f"route {dialect}:{rule.prefix!r} names provider "
                            f"{name!r}, which is not defined (defined: {known})"
                        )
        return self

    def routing_table(self) -> RoutingTable:
        return RoutingTable(
            {
                dialect: [
                    RouteRule(
                        prefix=rule.prefix,
                        provider=rule.provider,
                        fallbacks=tuple(rule.fallbacks),
                        max_attempts=rule.max_attempts,
                    )
                    for rule in rules
                ]
                for dialect, rules in self.routes.items()
            }
        )


def load_config(path: Path | str | None = None) -> GatewayConfig:
    """Read and validate the routing config.

    Resolution order: the explicit argument, then ``HEADROOM_ROUTING_CONFIG``, then the
    committed default. A missing or malformed file raises ``ConfigurationError`` — a
    gateway that silently falls back to some built-in default would route real traffic
    somewhere nobody chose.
    """
    resolved = Path(path or os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read routing config at {resolved}: {exc}") from exc
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"routing config at {resolved} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ConfigurationError(f"routing config at {resolved} must be a mapping")
    try:
        return GatewayConfig.model_validate(dict(parsed))
    except ValueError as exc:
        raise ConfigurationError(f"routing config at {resolved} is invalid: {exc}") from exc

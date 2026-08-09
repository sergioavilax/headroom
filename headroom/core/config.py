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
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from headroom.core.errors import ConfigurationError
from headroom.policy.routing import RouteRule, RoutingTable

__all__ = ["GatewayConfig", "ProviderSpec", "RouteSpec", "load_config"]

#: Where the routing config lives in a source checkout and in the container image.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "routing.yaml"

#: Point this at another file to run a gateway with a different routing table — how
#: the test suite gets a mock-only gateway without editing the committed config.
CONFIG_PATH_ENV = "HEADROOM_ROUTING_CONFIG"


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
    """Models starting with ``prefix`` are served by ``provider``."""

    model_config = ConfigDict(extra="forbid")

    #: Empty string is legal and means "everything else".
    prefix: str = ""
    provider: str


class GatewayConfig(BaseModel):
    """The whole routing configuration."""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    #: dialect name -> ordered rules. Order in the file is irrelevant; the routing
    #: table sorts by specificity.
    routes: dict[str, list[RouteSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _routes_point_at_real_providers(self) -> Self:
        for dialect, rules in self.routes.items():
            for rule in rules:
                if rule.provider not in self.providers:
                    known = ", ".join(sorted(self.providers)) or "none"
                    raise ValueError(
                        f"route {dialect}:{rule.prefix!r} names provider "
                        f"{rule.provider!r}, which is not defined (defined: {known})"
                    )
        return self

    def routing_table(self) -> RoutingTable:
        return RoutingTable(
            {
                dialect: [RouteRule(prefix=rule.prefix, provider=rule.provider) for rule in rules]
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

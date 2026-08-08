"""Routing, the provider registry, and the committed configuration.

The routing table is static in Phase 1 and deliberately shaped for Phase 6: rules
resolve to a provider *name*, so a failover chain is a change to what a rule holds
rather than a change to everything that reads one. The tests below pin the two
behaviours later phases will lean on — longest-prefix matching and per-dialect
isolation — plus the guard that makes BUILD_PLAN §0.2 invariant 3 structural: there is
no schema field a secret could be written into.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from headroom.api.gateway import build_gateway
from headroom.core.config import DEFAULT_CONFIG_PATH, GatewayConfig, load_config
from headroom.core.errors import ConfigurationError, ModelNotRouted
from headroom.policy.routing import RouteRule, RoutingTable
from headroom.providers.mock import MockProvider
from headroom.providers.registry import ProviderRegistry

# --------------------------------------------------------------------------------
# The routing table
# --------------------------------------------------------------------------------


def table() -> RoutingTable:
    return RoutingTable(
        {
            "anthropic": [
                RouteRule("claude-", "anthropic"),
                RouteRule("claude-haiku-", "cheap_pool"),
                RouteRule("mock-", "mock"),
            ],
            "openai": [RouteRule("mock-", "mock"), RouteRule("", "vllm")],
        }
    )


def test_the_longest_matching_prefix_wins() -> None:
    """The rule that lets an operator carve one model out of a family.

    File order is irrelevant — ``claude-haiku-`` is listed after ``claude-`` above and
    still wins, because specificity is decided at construction rather than by whoever
    edited the YAML last.
    """
    assert table().resolve("anthropic", "claude-haiku-4-5") == "cheap_pool"
    assert table().resolve("anthropic", "claude-opus-5") == "anthropic"


def test_the_empty_prefix_is_the_catch_all() -> None:
    """How a vLLM box serving whatever it has loaded gets addressed."""
    assert table().resolve("openai", "Qwen/Qwen3-27B") == "vllm"
    assert table().resolve("openai", "") == "vllm"


def test_routing_is_per_dialect() -> None:
    """BUILD_PLAN L4 puts cross-dialect translation out of scope, so a model routed in
    one dialect is not thereby routed in the other — which also makes Phase 6's
    failover pairs same-dialect by construction rather than by convention."""
    assert table().resolve("anthropic", "claude-opus-5") == "anthropic"
    with pytest.raises(ModelNotRouted):
        RoutingTable({"anthropic": [RouteRule("claude-", "anthropic")]}).resolve(
            "openai", "claude-opus-5"
        )


def test_an_unroutable_model_names_itself_and_the_alternatives() -> None:
    with pytest.raises(ModelNotRouted) as raised:
        table().resolve("anthropic", "gpt-4o")

    message = str(raised.value)
    assert "gpt-4o" in message
    assert "'claude-'" in message


def test_an_unknown_dialect_is_unroutable_rather_than_a_crash() -> None:
    with pytest.raises(ModelNotRouted):
        table().resolve("cohere", "command-r")


def test_equally_specific_rules_resolve_deterministically() -> None:
    """Two prefixes of the same length must not depend on dict ordering — a routing
    table that shuffles between restarts is unreproducible, and Phase 8 reports numbers
    from this path."""
    rules = [RouteRule("aa-", "first"), RouteRule("bb-", "second")]
    forward = RoutingTable({"anthropic": rules})
    backward = RoutingTable({"anthropic": list(reversed(rules))})

    assert forward.resolve("anthropic", "bb-x") == backward.resolve("anthropic", "bb-x")
    assert [rule.provider for rule in forward.rules_for("anthropic")] == [
        rule.provider for rule in backward.rules_for("anthropic")
    ]


# --------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------


def test_the_registry_resolves_by_name() -> None:
    registry = ProviderRegistry()
    provider = MockProvider("mock")
    registry.add(provider)

    assert registry.get("mock") is provider
    assert registry.names() == ["mock"]
    assert "mock" in registry


def test_a_duplicate_provider_name_is_a_configuration_bug() -> None:
    registry = ProviderRegistry()
    registry.add(MockProvider("mock"))

    with pytest.raises(ValueError, match="already registered"):
        registry.add(MockProvider("mock"))


def test_an_unknown_provider_name_lists_the_known_ones() -> None:
    registry = ProviderRegistry()
    registry.add(MockProvider("mock"))

    with pytest.raises(KeyError, match="registered: mock"):
        registry.get("vllm")


# --------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------


def test_the_committed_config_loads_and_routes_what_it_claims() -> None:
    """``config/routing.yaml`` is a deliverable: a typo in it should fail here, in CI,
    rather than on the operator's machine at ``make up``."""
    config = load_config(DEFAULT_CONFIG_PATH)
    routing = config.routing_table()

    assert set(config.providers) == {"mock", "anthropic", "vllm"}
    assert routing.resolve("anthropic", "claude-opus-5") == "anthropic"
    assert routing.resolve("anthropic", "mock-model-1") == "mock"
    assert routing.resolve("openai", "mock-model-1") == "mock"
    assert routing.resolve("openai", "Qwen/Qwen3-27B") == "vllm"


def test_the_committed_config_builds_a_gateway_without_any_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyless boot (invariant 4): the gateway starts with no key in the environment.

    A deployment routing only mock and vLLM traffic is a legitimate deployment. The
    error for a missing Anthropic key belongs on the first request that needs Anthropic,
    not on startup — and Phase 9's container must come up healthy before its secret is
    injected.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    gateway = build_gateway(load_config(DEFAULT_CONFIG_PATH))

    assert gateway.registry.names() == ["anthropic", "mock", "vllm"]
    assert gateway.provider_for("anthropic", "mock-model-1").kind == "mock"


def test_a_literal_credential_in_config_is_rejected_by_the_schema() -> None:
    """Invariant 3, enforced structurally: there is no field to put a key in.

    ``extra="forbid"`` means a well-meaning ``api_key:`` line fails to load instead of
    quietly working — and a config that quietly works is a config that gets committed.
    """
    with pytest.raises(ValueError, match="api_key"):
        GatewayConfig.model_validate(
            {"providers": {"anthropic": {"kind": "anthropic", "api_key": "sk-ant-secret"}}}
        )


def test_a_route_naming_an_undefined_provider_fails_at_load(tmp_path: Path) -> None:
    path = tmp_path / "routing.yaml"
    path.write_text(
        textwrap.dedent(
            """
            providers:
              mock:
                kind: mock
            routes:
              anthropic:
                - prefix: "claude-"
                  provider: typo_here
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="typo_here"):
        load_config(path)


def test_an_unknown_provider_kind_fails_when_the_gateway_is_built(tmp_path: Path) -> None:
    path = tmp_path / "routing.yaml"
    path.write_text(
        textwrap.dedent(
            """
            providers:
              somewhere:
                kind: telepathy
            routes: {}
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="telepathy"):
        build_gateway(load_config(path))


def test_a_missing_config_file_says_where_it_looked(tmp_path: Path) -> None:
    """No silent fallback to a built-in default: a gateway that invents its own routing
    table sends real traffic somewhere nobody chose."""
    with pytest.raises(ConfigurationError, match="cannot read routing config"):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml_says_so(tmp_path: Path) -> None:
    path = tmp_path / "routing.yaml"
    path.write_text("providers: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not valid YAML"):
        load_config(path)


def test_the_config_path_can_be_overridden_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How a test — or a Phase 9 task definition — runs a gateway with different routes."""
    path = tmp_path / "routing.yaml"
    path.write_text("providers:\n  mock:\n    kind: mock\nroutes: {}\n", encoding="utf-8")
    monkeypatch.setenv("HEADROOM_ROUTING_CONFIG", str(path))

    assert set(load_config().providers) == {"mock"}

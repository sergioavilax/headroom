"""Chains are configuration, and BUILD_PLAN L4 is what the loader refuses to break.

Two things are being tested and they are not the same thing.

**The shape of a chain** — undefined names, a provider repeated, an attempt budget over
the ceiling — is caught at *load*, by the Pydantic schema, because those are typos and a
typo in a fallback is the worst kind: invisible until the day the primary is down.

**Whether a chain crosses a dialect** is caught at *gateway build*, and the split is
deliberate. L4 puts cross-dialect translation permanently out of scope, and routing being
per dialect makes a chain same-dialect *structurally* — but nothing structural stops an
operator writing ``fallbacks: [anthropic]`` under an ``openai:`` route, which would hand
an OpenAI-dialect body to the Messages API on exactly the day the primary went down. That
check needs the provider-kind registry, and ``headroom/api/gateway.py`` is the module that
imports the kinds — so it is the only place where the table is guaranteed populated.

The attempt *sequence* is tested here too, because it is the retry policy: one attempt per
candidate by default (so a route with no fallbacks behaves exactly as Phase 5 did), and a
wrap when ``max_attempts`` exceeds the chain, which is how a single-provider route asks to
be retried rather than abandoned.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from headroom.api.gateway import build_gateway
from headroom.core.config import GatewayConfig, load_config
from headroom.core.errors import ConfigurationError
from headroom.policy.routing import MAX_ATTEMPT_LIMIT, RouteRule, RoutingTable


def _config(body: str, tmp_path: Path) -> Path:
    path = tmp_path / "routing.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------
# The attempt sequence
# --------------------------------------------------------------------------------


def test_a_rule_with_no_fallbacks_makes_exactly_one_attempt() -> None:
    """The default that makes this phase additive for every route nobody has chained."""
    rule = RouteRule(prefix="claude-", provider="anthropic")

    assert rule.chain == ("anthropic",)
    assert rule.attempts() == ("anthropic",)


def test_a_chain_is_tried_in_order_once_each() -> None:
    rule = RouteRule(prefix="", provider="vllm_a", fallbacks=("vllm_b", "vllm_c"))

    assert rule.attempts() == ("vllm_a", "vllm_b", "vllm_c")


def test_max_attempts_above_the_chain_wraps_fresh_candidates_first() -> None:
    """``a, b, a`` and not ``a, a, b``: a fresh candidate teaches more than a repeat.

    Trying ``vllm_b`` before coming back to ``vllm_a`` learns whether the problem is one
    box or the whole fleet, in the same number of round trips, and gets the caller an
    answer sooner in the overwhelmingly common case where it is one box.
    """
    pair = RouteRule(prefix="", provider="vllm_a", fallbacks=("vllm_b",), max_attempts=3)
    solo = RouteRule(prefix="", provider="vllm_a", max_attempts=3)

    assert pair.attempts() == ("vllm_a", "vllm_b", "vllm_a")
    assert solo.attempts() == ("vllm_a", "vllm_a", "vllm_a")


def test_max_attempts_below_the_chain_truncates_it() -> None:
    """An operator may cap the budget below the chain length; the head wins."""
    rule = RouteRule(prefix="", provider="a", fallbacks=("b", "c"), max_attempts=2)

    assert rule.attempts() == ("a", "b")


def test_resolve_still_answers_with_the_primary() -> None:
    """Phase 1's method means what it always meant, which is why nothing above it moved."""
    table = RoutingTable(
        {"openai": [RouteRule(prefix="", provider="vllm_a", fallbacks=("vllm_b",))]}
    )

    assert table.resolve("openai", "any-model") == "vllm_a"
    assert table.resolve_route("openai", "any-model").attempts == ("vllm_a", "vllm_b")


def test_a_scope_narrows_a_chain_and_never_widens_it() -> None:
    """Authorization outranks availability — a scope is not something an outage may widen."""
    route = RoutingTable(
        {"openai": [RouteRule(prefix="", provider="vllm_a", fallbacks=("vllm_b",))]}
    ).resolve_route("openai", "any-model")

    assert route.permitted(lambda name: True).attempts == ("vllm_a", "vllm_b")
    assert route.permitted(lambda name: name == "vllm_a").attempts == ("vllm_a",)
    # A predicate that allows nothing cannot produce an empty chain: the primary's own
    # 403 is the answer in that case, and it is decided before this ever runs.
    assert route.permitted(lambda name: False).attempts == ("vllm_a",)


def test_the_routing_table_can_list_every_reachable_provider() -> None:
    """Used to sanity-check configuration; a fallback counts as reachable."""
    table = RoutingTable(
        {
            "openai": [RouteRule(prefix="", provider="vllm_a", fallbacks=("vllm_b",))],
            "anthropic": [RouteRule(prefix="claude-", provider="anthropic")],
        }
    )

    assert table.providers() == {"vllm_a", "vllm_b", "anthropic"}


# --------------------------------------------------------------------------------
# Shape, caught at load
# --------------------------------------------------------------------------------


def test_a_fallback_naming_an_undefined_provider_fails_at_load(tmp_path: Path) -> None:
    """The worst typo to discover late: it stays invisible until the primary is down."""
    path = _config(
        """
        providers:
          mock:
            kind: mock
        routes:
          openai:
            - prefix: ""
              provider: mock
              fallbacks: [mokc]
        """,
        tmp_path,
    )

    with pytest.raises(ConfigurationError, match="mokc"):
        load_config(path)


def test_a_chain_cannot_name_the_same_provider_twice() -> None:
    """A repeat buys nothing ``max_attempts`` does not, and it lies in the ledger.

    ``failover_hops`` counts candidates passed over. A chain of ``[a, a]`` would report a
    hop for a request that never left ``a``, which is a wrong answer to the one question
    the column exists for.
    """
    with pytest.raises(ValueError, match="twice"):
        GatewayConfig.model_validate(
            {
                "providers": {"a": {"kind": "mock"}},
                "routes": {"openai": [{"prefix": "", "provider": "a", "fallbacks": ["a"]}]},
            }
        )


@pytest.mark.parametrize("attempts", [0, MAX_ATTEMPT_LIMIT + 1, 50])
def test_the_attempt_budget_is_bounded_in_both_directions(attempts: int) -> None:
    """Zero attempts is not a route, and an unbounded retry budget is a self-DoS.

    The ceiling is the interesting half: it is a number an operator can raise without a
    code change, which is exactly the kind of number somebody eventually sets to 50 — on
    the first-token path, during an incident.
    """
    with pytest.raises(ValueError, match="max_attempts"):
        GatewayConfig.model_validate(
            {
                "providers": {"a": {"kind": "mock"}},
                "routes": {"openai": [{"prefix": "", "provider": "a", "max_attempts": attempts}]},
            }
        )


def test_a_literal_credential_is_still_rejected_with_chains_in_the_schema() -> None:
    """Invariant 3 survived the widening: there is still no field to put a key in."""
    with pytest.raises(ValueError, match="api_key"):
        GatewayConfig.model_validate(
            {"providers": {"anthropic": {"kind": "anthropic", "api_key": "sk-ant-secret"}}}
        )


# --------------------------------------------------------------------------------
# BUILD_PLAN L4, caught at gateway build
# --------------------------------------------------------------------------------


def test_a_cross_dialect_fallback_refuses_to_build(tmp_path: Path) -> None:
    """The rule L4 locks, enforced rather than remembered.

    An Anthropic provider under an ``openai:`` route would receive a chat-completions body
    at the Messages API — cross-dialect translation, arriving through a config file
    instead of through a translation layer. The gateway refuses to start.
    """
    path = _config(
        """
        providers:
          vllm:
            kind: openai_compat
            base_url: http://localhost:8010
          anthropic:
            kind: anthropic
            base_url: https://api.anthropic.com
            api_key_env: ANTHROPIC_API_KEY
        routes:
          openai:
            - prefix: ""
              provider: vllm
              fallbacks: [anthropic]
        """,
        tmp_path,
    )

    with pytest.raises(ConfigurationError, match="cross-dialect"):
        build_gateway(load_config(path))


def test_a_cross_dialect_primary_refuses_too(tmp_path: Path) -> None:
    """The check is symmetric. A rule that only applied to the new feature would be a
    rule the old feature can still break."""
    path = _config(
        """
        providers:
          anthropic:
            kind: anthropic
            base_url: https://api.anthropic.com
            api_key_env: ANTHROPIC_API_KEY
        routes:
          openai:
            - prefix: ""
              provider: anthropic
        """,
        tmp_path,
    )

    with pytest.raises(ConfigurationError, match="cross-dialect"):
        build_gateway(load_config(path))


def test_a_same_dialect_chain_builds(tmp_path: Path) -> None:
    """The control: two OpenAI-compatible boxes chained together is exactly the point."""
    path = _config(
        """
        providers:
          vllm_a:
            kind: openai_compat
            base_url: http://localhost:8010
          vllm_b:
            kind: openai_compat
            base_url: http://localhost:8011
        routes:
          openai:
            - prefix: ""
              provider: vllm_a
              fallbacks: [vllm_b]
        """,
        tmp_path,
    )

    gateway = build_gateway(load_config(path))

    assert gateway.registry.names() == ["vllm_a", "vllm_b"]
    assert gateway.routing.resolve_route("openai", "anything").attempts == ("vllm_a", "vllm_b")


def test_the_mock_kind_may_chain_in_either_dialect(tmp_path: Path) -> None:
    """It genuinely speaks both, which is what lets a keyless chaos suite exist at all."""
    path = _config(
        """
        providers:
          mock_a:
            kind: mock
          mock_b:
            kind: mock
        routes:
          anthropic:
            - prefix: "mock-"
              provider: mock_a
              fallbacks: [mock_b]
          openai:
            - prefix: "mock-"
              provider: mock_a
              fallbacks: [mock_b]
        """,
        tmp_path,
    )

    gateway = build_gateway(load_config(path))

    for dialect in ("anthropic", "openai"):
        assert gateway.routing.resolve_route(dialect, "mock-x").attempts == ("mock_a", "mock_b")

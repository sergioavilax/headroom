"""The routing table: model prefix → provider chain, per dialect.

Static in Phase 1, exactly as BUILD_PLAN specifies, and widened in Phase 6 exactly where
H-013 said it would be — *"Phase 6 widens what a rule holds (primary plus same-dialect
fallbacks); the proxy is untouched"*. Two properties made that possible and still hold:

* **Rules resolve to provider *names*, not provider objects.** A rule now holds a
  primary plus an ordered list of fallbacks; nothing above or below this module learned
  a new concept, because a name was always the currency.
* **Routing is per dialect.** BUILD_PLAN L4 forbids cross-dialect translation, so a
  failover pair is same-dialect *by construction* — a chain lives inside one dialect's
  rule list and cannot address another's. ``headroom/api/gateway.py`` tightens that from
  "by construction" to "checked at startup": a provider kind declares which dialects it
  speaks, and a rule naming one that cannot speak the rule's dialect fails to build.

**Longest prefix wins.** With ``claude-`` → anthropic and ``claude-haiku-`` →
cheap_pool, the more specific rule takes the request, which is the only ordering that
lets an operator carve one model out of a family without rewriting the family's rule.
The empty prefix ``""`` matches everything and is therefore the catch-all — useful for
a vLLM box serving whatever model it happens to have loaded.

**A chain is a list of candidates; the *attempt sequence* is derived from it.** They are
different lists and the difference is the whole retry policy. By default one attempt per
candidate, so a route with no fallbacks behaves exactly as it did in Phase 5 — one call,
no retry, no backoff, nothing new on the path. An operator who wants a provider retried
rather than abandoned writes ``max_attempts`` larger than the chain, and the sequence
wraps: ``a, b, a``. Preferring a *fresh* candidate before coming back to one that has
already failed is the ordering that learns the most per attempt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from headroom.core.errors import ModelNotRouted

__all__ = ["MAX_ATTEMPT_LIMIT", "Route", "RouteRule", "RoutingTable"]

#: The most attempts one request may make, whatever the config says. A retry budget on
#: the first-token path is a self-inflicted denial of service if it is unbounded, and a
#: number an operator can raise without a code change is a number somebody eventually
#: sets to 50. Enforced by the config schema (``headroom/core/config.py``).
MAX_ATTEMPT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class RouteRule:
    """Models whose id starts with ``prefix`` go to ``provider``, then to ``fallbacks``.

    Phase 1 shipped the first two fields and Phase 6 added the last two, with defaults
    that reproduce Phase 1 behaviour exactly: no fallbacks, one attempt, no backoff.
    """

    prefix: str
    provider: str
    #: Same-dialect alternates, tried in order after the primary (BUILD_PLAN L4).
    fallbacks: tuple[str, ...] = ()
    #: How many attempts one request may make. ``None`` means one per candidate.
    max_attempts: int | None = None

    @property
    def chain(self) -> tuple[str, ...]:
        """The distinct candidates, primary first."""
        return (self.provider, *self.fallbacks)

    def attempts(self) -> tuple[str, ...]:
        """The attempt sequence: the chain, wrapping when more attempts are configured.

        ``max_attempts`` above the chain length is how a single-provider route asks for a
        genuine retry — ``("vllm_a", "vllm_a", "vllm_a")`` — while a two-provider route
        with three attempts reads ``("vllm_a", "vllm_b", "vllm_a")``: every fresh
        candidate before any repeat, because a fresh candidate teaches more.
        """
        chain = self.chain
        limit = len(chain) if self.max_attempts is None else self.max_attempts
        return tuple(chain[index % len(chain)] for index in range(max(1, limit)))


@dataclass(frozen=True, slots=True)
class Route:
    """One resolved routing decision: who serves, and who serves if they cannot."""

    rule: RouteRule
    #: The attempt sequence, already filtered by the caller's provider scope.
    attempts: tuple[str, ...] = ()

    @property
    def primary(self) -> str:
        """The provider this model is routed to. What a 403 is decided against."""
        return self.rule.provider

    def permitted(self, allows: Callable[[str], bool]) -> Route:
        """This route with every candidate the key may not reach removed.

        Authorization outranks availability: a key scoped to ``vllm_a`` and not to
        ``vllm_b`` may not be served by ``vllm_b`` merely because ``vllm_a`` is down —
        that would make a scope something an outage can widen. The primary is checked
        separately by ``Principal.require_provider`` and answers 403 as it always has,
        so filtering here can only ever *narrow* an already-authorised chain.

        ``allows`` is any ``(name) -> bool`` predicate; the proxy passes the principal's
        own, so this module stays free of an auth import.
        """
        kept = tuple(name for name in self.attempts if allows(name))
        # The primary is authorised by the time this runs, so `kept` cannot be empty in
        # the proxy. Belt and braces for a direct caller: an empty attempt sequence would
        # reach the executor as a ValueError rather than as a routing decision.
        return Route(rule=self.rule, attempts=kept or (self.primary,))


class RoutingTable:
    """Resolves (dialect, model) to a provider name, and to the chain behind it."""

    __slots__ = ("_by_dialect",)

    def __init__(self, rules: Mapping[str, Iterable[RouteRule]]) -> None:
        # Sorted once, at construction: longest prefix first, then alphabetically so
        # two equally specific rules resolve deterministically rather than by dict
        # insertion order. Resolution is then a linear scan of a short list.
        self._by_dialect: dict[str, tuple[RouteRule, ...]] = {
            dialect: tuple(sorted(dialect_rules, key=lambda r: (-len(r.prefix), r.prefix)))
            for dialect, dialect_rules in rules.items()
        }

    def rules_for(self, dialect: str) -> Sequence[RouteRule]:
        return self._by_dialect.get(dialect, ())

    def dialects(self) -> list[str]:
        return sorted(self._by_dialect)

    def resolve(self, dialect: str, model: str) -> str:
        """The provider name serving this model, or ``ModelNotRouted``.

        Unchanged from Phase 1 and still the answer to "where is this routed" — the
        *primary*, which is what a scope check and a 403 are decided against.
        """
        return self.resolve_route(dialect, model).primary

    def resolve_route(self, dialect: str, model: str) -> Route:
        """The whole routing decision: the rule, and the attempt sequence it implies."""
        for rule in self._by_dialect.get(dialect, ()):
            if model.startswith(rule.prefix):
                return Route(rule=rule, attempts=rule.attempts())
        raise ModelNotRouted(
            f"no route for model {model!r} in the {dialect!r} dialect "
            f"(configured prefixes: {self._prefixes(dialect)})"
        )

    def providers(self) -> set[str]:
        """Every provider name any rule can reach. Used to validate configuration."""
        return {
            name for rules in self._by_dialect.values() for rule in rules for name in rule.chain
        }

    def _prefixes(self, dialect: str) -> str:
        rules = self._by_dialect.get(dialect, ())
        if not rules:
            return "none"
        return ", ".join(repr(rule.prefix) for rule in rules)

"""The routing table: model prefix → provider, per dialect.

Static in Phase 1, exactly as BUILD_PLAN specifies, but shaped so that Phase 6 extends
rather than replaces it. Two properties make that work:

* **Rules resolve to a provider *name*, not a provider object.** Phase 6 turns the
  single name into a chain (primary plus same-dialect fallbacks) by widening what a
  rule holds; nothing above or below this module learns a new concept.
* **Routing is per dialect.** BUILD_PLAN L4 forbids cross-dialect translation, so a
  failover pair is same-dialect by construction — the plan's constraint is enforced by
  the data structure rather than remembered by a reviewer.

**Longest prefix wins.** With ``claude-`` → anthropic and ``claude-haiku-`` →
cheap_pool, the more specific rule takes the request, which is the only ordering that
lets an operator carve one model out of a family without rewriting the family's rule.
The empty prefix ``""`` matches everything and is therefore the catch-all — useful for
a vLLM box serving whatever model it happens to have loaded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from headroom.core.errors import ModelNotRouted

__all__ = ["RouteRule", "RoutingTable"]


@dataclass(frozen=True, slots=True)
class RouteRule:
    """Models whose id starts with ``prefix`` go to ``provider``."""

    prefix: str
    provider: str


class RoutingTable:
    """Resolves (dialect, model) to a provider name."""

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
        """The provider name serving this model, or ``ModelNotRouted``."""
        for rule in self._by_dialect.get(dialect, ()):
            if model.startswith(rule.prefix):
                return rule.provider
        raise ModelNotRouted(
            f"no route for model {model!r} in the {dialect!r} dialect "
            f"(configured prefixes: {self._prefixes(dialect)})"
        )

    def _prefixes(self, dialect: str) -> str:
        rules = self._by_dialect.get(dialect, ())
        if not rules:
            return "none"
        return ", ".join(repr(rule.prefix) for rule in rules)

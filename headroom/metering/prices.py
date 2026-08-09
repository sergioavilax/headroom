"""Dated price schedules: what a model cost **on the day the request happened**.

This module is the whole reason Phase 3 exists. Backline's **D-017** scar was a cost
meter that kept billing at sticker prices after a vendor published new ones, and the
reason it could is that it had a `price` where it needed a *history*. So here a model
does not have a price; it has an ordered list of ``(effective_from, in, out)`` rows,
and resolving one requires a date.

Three properties follow, and each is asserted rather than assumed:

**A price change never reprices history.** Appending a row with a later
``effective_from`` cannot alter what a request from last week resolves to — the lookup
is "the latest row whose date is on or before the request's date", so an unrelated
future row is not a candidate. The ledger then copies the resolved rates into its own
row, so even editing this file afterwards cannot move a landed cost.

**An unknown model is unpriced, never free.** ``resolve`` returns ``None`` rather than
falling back to a default, a nearest neighbour, or zero. A model nobody entered has an
unknown cost, and "unknown" is a state the ledger can represent; a silent $0.00 is the
same lie D-017 told with different arithmetic.

**Money never touches a float.** Rates parse from *strings* into :class:`~decimal.Decimal`
and a bare YAML float is rejected at load with a message naming the model — because
``0.1 + 0.2`` is the canonical demonstration that binary floating point cannot hold
decimal money, and a price file is exactly where that becomes an invoice.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from headroom.core.config import MODELS_CONFIG_PATH_ENV
from headroom.core.errors import ConfigurationError

__all__ = [
    "DEFAULT_MODELS_PATH",
    "ModelPrices",
    "PriceBook",
    "PriceRow",
    "load_price_book",
]

#: Where the price file lives in a source checkout and in the container image.
DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

#: Exact match, or longest matching prefix — the routing table's rule (H-013), applied
#: to reference data so a whole model family can be priced by one entry.
MATCH_EXACT = "exact"
MATCH_PREFIX = "prefix"


@dataclass(frozen=True, slots=True)
class PriceRow:
    """One model's rates, and the day they took effect.

    Frozen and Decimal-valued. It is handed to the cost calculator and then copied
    verbatim into the ledger row, which is what makes a landed cost immune to a later
    edit of ``config/models.yaml``.
    """

    effective_from: date
    usd_per_mtok_in: Decimal
    usd_per_mtok_out: Decimal

    @property
    def is_free(self) -> bool:
        """Both rates are zero — a local GPU, priced honestly rather than unpriced."""
        return not self.usd_per_mtok_in and not self.usd_per_mtok_out


@dataclass(frozen=True, slots=True)
class ModelPrices:
    """One model's reference data and its full price history, oldest row first."""

    model: str
    dialects: tuple[str, ...]
    context_window: int | None
    rows: tuple[PriceRow, ...]
    match: str = MATCH_EXACT

    def price_at(self, when: date) -> PriceRow | None:
        """The row in effect on ``when``, or ``None`` if the history starts later.

        Walks backwards so the newest applicable row wins, and stops at the first
        candidate — the list is short and sorted at load, so this is a handful of
        comparisons on the request path.
        """
        for row in reversed(self.rows):
            if row.effective_from <= when:
                return row
        return None


class PriceBook:
    """Every priced model, resolved by id.

    Immutable after construction: prices are read once at startup, exactly like the
    routing table, so "what is this gateway billing right now" has one answer for the
    life of the process rather than a time-varying one.
    """

    __slots__ = ("_exact", "_prefixes")

    def __init__(self, models: Iterable[ModelPrices] = ()) -> None:
        self._exact: dict[str, ModelPrices] = {}
        prefixes: list[ModelPrices] = []
        for entry in models:
            if entry.match == MATCH_PREFIX:
                prefixes.append(entry)
            else:
                self._exact[entry.model] = entry
        # Longest first, then alphabetically: two equally specific prefixes must not
        # reshuffle across restarts and quietly reprice an experiment mid-run.
        self._prefixes: tuple[ModelPrices, ...] = tuple(
            sorted(prefixes, key=lambda entry: (-len(entry.model), entry.model))
        )

    def resolve(self, model: str) -> ModelPrices | None:
        """The pricing entry for ``model``: exact match, then longest prefix, then None."""
        exact = self._exact.get(model)
        if exact is not None:
            return exact
        for entry in self._prefixes:
            if model.startswith(entry.model):
                return entry
        return None

    def price_for(self, model: str, when: date) -> PriceRow | None:
        """The rates that applied to ``model`` on ``when``, or ``None`` if unpriced."""
        entry = self.resolve(model)
        return None if entry is None else entry.price_at(when)

    def models(self) -> tuple[ModelPrices, ...]:
        """Every entry, exact ones first, each family's prefix entry after them."""
        return tuple(self._exact.values()) + self._prefixes

    def __len__(self) -> int:
        return len(self._exact) + len(self._prefixes)


# --- loading ------------------------------------------------------------------------


def _decimal(value: Any, field: str) -> Decimal:
    """Parse a rate, refusing anything that has already been through a float.

    ``bool`` is checked before ``int`` because ``bool`` *is* an ``int`` in Python and
    ``usd_per_mtok_in: true`` would otherwise become ``Decimal("1")``.
    """
    if isinstance(value, float):
        raise ValueError(
            f"{field} must be quoted so it stays exact — YAML read {value!r} as a "
            f'float, and money in binary floating point is wrong by construction (use "{value}")'
        )
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError(f"{field} must be a quoted decimal string, got {type(value).__name__}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a decimal number: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{field} must not be negative, got {value!r}")
    return parsed


class PriceRowSpec(BaseModel):
    """One ``prices:`` entry as written in the file."""

    model_config = ConfigDict(extra="forbid")

    effective_from: date
    usd_per_mtok_in: Decimal
    usd_per_mtok_out: Decimal

    @field_validator("usd_per_mtok_in", "usd_per_mtok_out", mode="before")
    @classmethod
    def _exact_money(cls, value: Any, info: Any) -> Decimal:
        return _decimal(value, str(info.field_name))

    @field_validator("effective_from", mode="before")
    @classmethod
    def _plain_date(cls, value: Any) -> Any:
        # A bare `2026-08-08` is already a date to PyYAML; a quoted one is a string.
        # A datetime would silently carry a time nobody wrote, so it is refused.
        if isinstance(value, datetime):
            raise ValueError("effective_from is a date, not a timestamp — drop the time part")
        return value

    def row(self) -> PriceRow:
        return PriceRow(
            effective_from=self.effective_from,
            usd_per_mtok_in=self.usd_per_mtok_in,
            usd_per_mtok_out=self.usd_per_mtok_out,
        )


class ModelSpec(BaseModel):
    """One ``models:`` entry as written in the file."""

    model_config = ConfigDict(extra="forbid")

    dialects: list[str] = Field(default_factory=list)
    context_window: int | None = None
    match: str = MATCH_EXACT
    prices: list[PriceRowSpec] = Field(default_factory=list)

    @field_validator("match")
    @classmethod
    def _known_match(cls, value: str) -> str:
        if value not in (MATCH_EXACT, MATCH_PREFIX):
            raise ValueError(f"match must be {MATCH_EXACT!r} or {MATCH_PREFIX!r}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _one_row_per_date(self) -> Self:
        dates = [row.effective_from for row in self.prices]
        if len(set(dates)) != len(dates):
            raise ValueError(
                "two price rows share an effective_from; which one applies would depend "
                "on file order, and a price must not be decided by a diff"
            )
        return self


class ModelsConfig(BaseModel):
    """The whole price file."""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelSpec] = Field(default_factory=dict)

    def price_book(self) -> PriceBook:
        return PriceBook(
            ModelPrices(
                model=model_id,
                dialects=tuple(spec.dialects),
                context_window=spec.context_window,
                # Sorted here, once, so `price_at` can walk backwards and stop early
                # and the file's own row order is never load-bearing.
                rows=tuple(
                    row.row() for row in sorted(spec.prices, key=lambda row: row.effective_from)
                ),
                match=spec.match,
            )
            for model_id, spec in self.models.items()
        )


def load_price_book(path: Path | str | None = None) -> PriceBook:
    """Read and validate the price file.

    Resolution order matches the routing loader exactly (H-014): the explicit
    argument, then ``HEADROOM_MODELS_CONFIG``, then the committed default. A missing
    or malformed file raises ``ConfigurationError`` rather than yielding an empty
    book — a gateway that silently prices nothing would write a ledger full of NULLs
    and nobody would notice until an invoice arrived.
    """
    resolved = Path(path or os.environ.get(MODELS_CONFIG_PATH_ENV) or DEFAULT_MODELS_PATH)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read model prices at {resolved}: {exc}") from exc
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"model prices at {resolved} are not valid YAML: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ConfigurationError(f"model prices at {resolved} must be a mapping")
    try:
        return ModelsConfig.model_validate(dict(parsed)).price_book()
    except ValueError as exc:
        raise ConfigurationError(f"model prices at {resolved} are invalid: {exc}") from exc


def price_book_from(models: Sequence[ModelPrices]) -> PriceBook:
    """Build a book in code. For tests that need a price boundary of their own."""
    return PriceBook(models)

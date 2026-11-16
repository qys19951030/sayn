"""Type comparison logic used when classifying type differences.

The boundary is deliberately fixed and transparent:

* types inside the same family (integer, decimal, string, ...) are ordered
  by the range/precision they can hold; moving up the order is a safe
  *widening*, moving down is a *narrowing* that may truncate or lose
  precision;
* types from different families (e.g. ``VARCHAR`` -> ``INTEGER``) are
  *incompatible*: there is no containment relationship, so they are always
  treated as unsafe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .errors import SchemaAuditError

# Family name -> ordered base types, narrowest first.
# Ranks are positions inside each family list.
_TYPE_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "integer": ("TINYINT", "SMALLINT", "INTEGER", "BIGINT"),
    "float": ("FLOAT", "REAL", "DOUBLE"),
    "decimal": ("NUMERIC", "DECIMAL"),
    "string": ("CHAR", "VARCHAR", "TEXT"),
    "binary": ("BINARY", "VARBINARY", "BLOB"),
    "date": ("DATE", "DATETIME", "TIMESTAMP"),
    "boolean": ("BOOLEAN",),
    "json": ("JSON",),
}

_BASE_TO_FAMILY: Dict[str, str] = {
    base: family for family, bases in _TYPE_FAMILIES.items() for base in bases
}

_WIDTH_TOKEN = re.compile(r"\s+")


class TypeComparisonError(SchemaAuditError):
    """Raised when a type string cannot be interpreted."""


@dataclass(frozen=True)
class TypeClassification:
    """Result of comparing an incoming type against the live type."""

    live_type: str
    incoming_type: str
    family: Optional[str]
    direction: str  # one of: equal, widening, narrowing, incompatible

    @property
    def is_equal(self) -> bool:
        return self.direction == "equal"

    @property
    def is_widening(self) -> bool:
        return self.direction == "widening"

    @property
    def is_narrowing(self) -> bool:
        return self.direction == "narrowing"

    @property
    def is_incompatible(self) -> bool:
        return self.direction == "incompatible"


def normalise_type(db_type: str) -> str:
    """Normalise a database type string before comparison.

    Upper-cases the type, collapses internal whitespace and keeps any length
    or precision suffix (e.g. ``VARCHAR(10)``) untouched.
    """
    if db_type is None:
        raise TypeComparisonError("Cannot normalise a None type")
    return _WIDTH_TOKEN.sub(" ", str(db_type).strip().upper())


def _base_name(db_type: str) -> str:
    base = normalise_type(db_type).split("(", 1)[0].strip()
    # Rendering aliases used by different engines for the same family rank.
    aliases = {"DOUBLE PRECISION": "DOUBLE"}
    return aliases.get(base, base)


class TypeComparator:
    """Classifies a pair of column types as equal, widening or narrowing."""

    def classify(self, live_type: str, incoming_type: str) -> TypeClassification:
        live_norm = normalise_type(live_type)
        incoming_norm = normalise_type(incoming_type)

        if live_norm == incoming_norm:
            return TypeClassification(
                live_norm, incoming_norm, self._family_of(live_norm), "equal"
            )

        live_family = self._family_of(live_norm)
        incoming_family = self._family_of(incoming_norm)

        if live_family is None or incoming_family is None:
            # An unknown base type has no defined ordering: never claim a
            # safe evolution for something we cannot reason about.
            return TypeClassification(
                live_norm, incoming_norm, None, "incompatible"
            )

        if live_family != incoming_family:
            return TypeClassification(
                live_norm, incoming_norm, None, "incompatible"
            )

        family_order = _TYPE_FAMILIES[live_family]
        live_base = _base_name(live_norm)
        incoming_base = _base_name(incoming_norm)
        live_rank = family_order.index(live_base)
        incoming_rank = family_order.index(incoming_base)

        if live_base == incoming_base:
            # Same base, different width suffix (e.g. VARCHAR(10)/VARCHAR(20)
            # or NUMERIC(5,2)/NUMERIC(10,2)): compare the suffix numerically.
            return self._compare_same_base(
                live_norm, incoming_norm, live_family
            )

        direction = "widening" if incoming_rank > live_rank else "narrowing"
        return TypeClassification(
            live_norm, incoming_norm, live_family, direction
        )

    def _compare_same_base(
        self, live_type: str, incoming_type: str, family: str
    ) -> TypeClassification:
        live_params = self._params(live_type)
        incoming_params = self._params(incoming_type)

        if live_params is None or incoming_params is None:
            return TypeClassification(
                live_type, incoming_type, family, "incompatible"
            )

        if incoming_params > live_params:
            direction = "widening"
        elif incoming_params < live_params:
            direction = "narrowing"
        else:
            direction = "equal"
        return TypeClassification(live_type, incoming_type, family, direction)

    @staticmethod
    def _params(db_type: str) -> Optional[Tuple[int, ...]]:
        match = re.match(r"^[A-Z ]+\(([0-9, ]+)\)$", db_type)
        if match is None:
            return None
        try:
            return tuple(int(part.strip()) for part in match.group(1).split(","))
        except ValueError:
            return None

    @staticmethod
    def _family_of(normalised_type: str) -> Optional[str]:
        return _BASE_TO_FAMILY.get(_base_name(normalised_type))

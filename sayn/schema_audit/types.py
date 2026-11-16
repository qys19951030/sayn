"""Type comparison logic used when classifying type differences."""

from __future__ import annotations


class TypeComparator:
    """Classifies a pair of column types as equal, widening or narrowing."""


# Public module-level helper placeholder (implemented incrementally).
def normalise_type(db_type: str) -> str:
    """Normalise a database type string before comparison."""
    raise NotImplementedError

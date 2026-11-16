"""Tests for the fixed type comparison boundary."""

import pytest

from sayn.schema_audit.types import (
    TypeComparator,
    TypeComparisonError,
    normalise_type,
)


@pytest.fixture
def comparator():
    return TypeComparator()


def test_normalise_type():
    assert normalise_type("  varchar(10) ") == "VARCHAR(10)"
    assert normalise_type("double precision") == "DOUBLE PRECISION"

    with pytest.raises(TypeComparisonError):
        normalise_type(None)


def test_equal_types(comparator):
    result = comparator.classify("INTEGER", "integer")
    assert result.is_equal
    assert result.family == "integer"


def test_integer_widening_and_narrowing(comparator):
    widening = comparator.classify("INTEGER", "BIGINT")
    assert widening.is_widening
    assert widening.family == "integer"

    narrowing = comparator.classify("BIGINT", "INTEGER")
    assert narrowing.is_narrowing


def test_varchar_length_is_width(comparator):
    assert comparator.classify("VARCHAR(10)", "VARCHAR(255)").is_widening
    assert comparator.classify("VARCHAR(255)", "VARCHAR(10)").is_narrowing


def test_decimal_params_compared(comparator):
    assert comparator.classify(
        "NUMERIC(5,2)", "NUMERIC(10,2)"
    ).is_widening
    assert comparator.classify(
        "NUMERIC(10,2)", "NUMERIC(5,2)"
    ).is_narrowing


def test_cross_family_is_incompatible(comparator):
    result = comparator.classify("VARCHAR(20)", "INTEGER")
    assert result.is_incompatible
    assert result.family is None

    assert comparator.classify("DATE", "TIMESTAMP").is_widening
    assert comparator.classify("BOOLEAN", "INTEGER").is_incompatible


def test_unknown_and_unparametrised_types_are_unsafe(comparator):
    assert comparator.classify("GEOMETRY", "INTEGER").is_incompatible
    assert comparator.classify("VARCHAR(10)", "VARCHAR").is_incompatible


def test_engine_aliases_share_family(comparator):
    assert comparator.classify(
        "DOUBLE PRECISION", "DOUBLE PRECISION"
    ).is_equal
    assert comparator.classify("REAL", "DOUBLE PRECISION").is_widening


def test_classification_carries_normalised_types(comparator):
    result = comparator.classify("varchar(5)", "VARCHAR(10)")
    assert result.live_type == "VARCHAR(5)"
    assert result.incoming_type == "VARCHAR(10)"

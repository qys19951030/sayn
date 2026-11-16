"""Tests for the configurable disposal policy."""

import pytest

from sayn.schema_audit.diff import SchemaDiffer
from sayn.schema_audit.errors import PolicyError
from sayn.schema_audit.models import Column, DiffKind, TableSchema
from sayn.schema_audit.policy import PolicyMode, PolicyDecision, SafetyPolicy


def col(name, db_type, nullable=True, default=None):
    return Column(name, db_type, nullable=nullable, default=default, position=0)


def diffs_for(live, incoming, previous=None):
    return SchemaDiffer().diff(live, incoming, previous)


def test_unknown_policy_rejected():
    with pytest.raises(PolicyError) as exc:
        SafetyPolicy("nope")
    assert "nope" in str(exc.value)
    assert PolicyMode.BLOCK in str(exc.value)


def test_block_policy_allows_clean_table():
    schema = TableSchema("t", [col("a", "INTEGER")])
    decision = SafetyPolicy().evaluate(diffs_for(schema, schema))

    assert isinstance(decision, PolicyDecision)
    assert decision.safe_to_load is True
    assert decision.blocking_diffs == []
    assert decision.blocking_columns == []


def test_block_policy_stops_on_narrowing_and_names_columns():
    live = TableSchema("t", [col("a", "VARCHAR(255)"), col("b", "INTEGER")])
    incoming = TableSchema("t", [col("a", "VARCHAR(10)"), col("b", "BIGINT")])

    decision = SafetyPolicy(PolicyMode.BLOCK).evaluate(
        diffs_for(live, incoming)
    )

    assert decision.safe_to_load is False
    assert decision.enforced is True
    assert decision.blocking_columns == ["a"]
    assert decision.blocking_diffs[0].kind == DiffKind.TYPE_MISMATCH_NARROWING


def test_block_policy_primary_key_reports_synthetic_label():
    previous = TableSchema("t", [col("a", "INTEGER")], ["a"])
    live = TableSchema("t", [col("a", "INTEGER")], [])

    decision = SafetyPolicy().evaluate(diffs_for(live, live, previous))

    assert decision.safe_to_load is False
    assert decision.blocking_columns == ["primary_key"]


def test_record_only_keeps_boundary_visible_but_does_not_enforce():
    live = TableSchema("t", [col("a", "VARCHAR(255)")])
    incoming = TableSchema("t", [col("a", "VARCHAR(10)")])

    decision = SafetyPolicy(PolicyMode.RECORD_ONLY).evaluate(
        diffs_for(live, incoming)
    )

    assert decision.safe_to_load is False
    assert decision.enforced is False
    assert len(decision.blocking_diffs) == 1
    assert decision.blocking_columns == ["a"]


def test_safe_diffs_are_safe_under_both_modes():
    live = TableSchema("t", [col("a", "INTEGER")])
    incoming = TableSchema("t", [col("a", "BIGINT"), col("b", "DATE")])

    for mode in PolicyMode.ALL:
        decision = SafetyPolicy(mode).evaluate(diffs_for(live, incoming))
        assert decision.safe_to_load is True
        assert decision.blocking_diffs == []

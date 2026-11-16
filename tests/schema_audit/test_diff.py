"""Tests for per-table structural diffing and the fixed blocking boundary."""

import pytest

from sayn.schema_audit.diff import SchemaDiffer
from sayn.schema_audit.models import Column, DiffKind, TableSchema


@pytest.fixture
def differ():
    return SchemaDiffer()


def col(name, db_type, nullable=True, default=None, position=None):
    return Column(name, db_type, nullable=nullable, default=default, position=position)


def schema(table, columns, primary_key=None):
    if columns and columns[0].position is None:
        columns = [
            Column(c.name, c.db_type, c.nullable, c.default, i)
            for i, c in enumerate(columns)
        ]
    return TableSchema(table, columns, primary_key or [])


def kinds(diffs):
    return [d.kind for d in diffs]


def by_kind(diffs, kind):
    return [d for d in diffs if d.kind == kind]


def test_identical_schemas_have_no_diffs(differ):
    s = schema("t", [col("a", "INTEGER"), col("b", "VARCHAR(10)")], ["a"])
    assert differ.diff(s, s, s) == []


def test_added_column_is_recorded_but_not_blocking(differ):
    live = schema("t", [col("a", "INTEGER")])
    incoming = schema("t", [col("a", "INTEGER"), col("b", "VARCHAR(5)")])

    diffs = differ.diff(live, incoming)
    added = by_kind(diffs, DiffKind.ADDED_COLUMN)
    assert len(added) == 1
    assert added[0].column == "b"
    assert added[0].incoming_type == "VARCHAR(5)"
    assert added[0].blocking is False


def test_missing_nullable_column_is_safe(differ):
    live = schema("t", [col("a", "INTEGER"), col("b", "VARCHAR(5)", nullable=True)])
    incoming = schema("t", [col("a", "INTEGER")])

    diffs = differ.diff(live, incoming)
    missing = by_kind(diffs, DiffKind.MISSING_COLUMN)
    assert len(missing) == 1
    assert missing[0].column == "b"
    assert missing[0].blocking is False
    assert missing[0].details["nullable"] is True


def test_missing_not_null_column_with_default_is_safe(differ):
    live = schema(
        "t", [col("a", "INTEGER"), col("b", "VARCHAR(5)", nullable=False, default="'x'")]
    )
    incoming = schema("t", [col("a", "INTEGER")])

    missing = by_kind(differ.diff(live, incoming), DiffKind.MISSING_COLUMN)
    assert missing[0].blocking is False


def test_missing_not_null_column_without_default_blocks(differ):
    live = schema(
        "t", [col("a", "INTEGER"), col("b", "VARCHAR(5)", nullable=False)]
    )
    incoming = schema("t", [col("a", "INTEGER")])

    missing = by_kind(differ.diff(live, incoming), DiffKind.MISSING_COLUMN)
    assert missing[0].blocking is True
    assert missing[0].details["unsafe_reason"] == "not_null_without_default"
    assert missing[0].live_type == "VARCHAR(5)"


def test_widening_type_diff_is_safe(differ):
    live = schema("t", [col("a", "INTEGER")])
    incoming = schema("t", [col("a", "BIGINT")])

    type_diffs = [
        d for d in differ.diff(live, incoming) if d.kind.startswith("type_mismatch")
    ]
    assert len(type_diffs) == 1
    assert type_diffs[0].kind == DiffKind.TYPE_MISMATCH_WIDENING
    assert type_diffs[0].live_type == "INTEGER"
    assert type_diffs[0].incoming_type == "BIGINT"
    assert type_diffs[0].blocking is False


def test_narrowing_type_diff_blocks(differ):
    live = schema("t", [col("a", "VARCHAR(255)")])
    incoming = schema("t", [col("a", "VARCHAR(10)")])

    type_diffs = [
        d for d in differ.diff(live, incoming) if d.kind.startswith("type_mismatch")
    ]
    assert type_diffs[0].kind == DiffKind.TYPE_MISMATCH_NARROWING
    assert type_diffs[0].blocking is True


def test_incompatible_type_diff_blocks(differ):
    live = schema("t", [col("a", "VARCHAR(20)")])
    incoming = schema("t", [col("a", "INTEGER")])

    type_diffs = [
        d for d in differ.diff(live, incoming) if d.kind.startswith("type_mismatch")
    ]
    assert type_diffs[0].kind == DiffKind.TYPE_MISMATCH_INCOMPATIBLE
    assert type_diffs[0].blocking is True


def test_order_change_uses_previous_snapshot(differ):
    previous = schema("t", [col("a", "INTEGER"), col("b", "VARCHAR(5)")])
    live = schema("t", [col("b", "VARCHAR(5)"), col("a", "INTEGER")])

    diffs = differ.diff(live, live, previous)
    order = by_kind(diffs, DiffKind.ORDER_CHANGED)
    assert len(order) == 2
    assert {d.column for d in order} == {"a", "b"}
    assert all(d.blocking is False for d in order)


def test_no_order_diff_without_previous_snapshot(differ):
    live = schema("t", [col("b", "VARCHAR(5)"), col("a", "INTEGER")])
    assert by_kind(differ.diff(live, live), DiffKind.ORDER_CHANGED) == []


def test_primary_key_set_change_blocks(differ):
    previous = schema("t", [col("a", "INTEGER"), col("b", "INTEGER")], ["a"])
    live = schema("t", [col("a", "INTEGER"), col("b", "INTEGER")], ["b"])

    pk = by_kind(differ.diff(live, live, previous), DiffKind.PRIMARY_KEY_CHANGED)
    assert len(pk) == 1
    assert pk[0].blocking is True
    assert pk[0].details["added"] == ["b"]
    assert pk[0].details["removed"] == ["a"]


def test_primary_key_order_change_blocks(differ):
    previous = schema(
        "t", [col("a", "INTEGER"), col("b", "INTEGER")], ["a", "b"]
    )
    live = schema(
        "t", [col("a", "INTEGER"), col("b", "INTEGER")], ["b", "a"]
    )

    pk = by_kind(differ.diff(live, live, previous), DiffKind.PRIMARY_KEY_CHANGED)
    assert pk[0].blocking is True
    assert pk[0].details["reordered"] is True


def test_primary_key_unchanged_emits_nothing(differ):
    previous = schema("t", [col("a", "INTEGER")], ["a"])
    assert (
        by_kind(differ.diff(previous, previous, previous), DiffKind.PRIMARY_KEY_CHANGED)
        == []
    )


def test_combined_scenario_lists_every_category_once(differ):
    previous = schema(
        "t",
        [col("id", "INTEGER"), col("old", "VARCHAR(10)"), col("wide", "VARCHAR(100)")],
        ["id"],
    )
    live = schema(
        "t",
        [
            col("id", "INTEGER"),
            col("wide", "VARCHAR(100)"),
            col("old", "VARCHAR(10)", nullable=False),
            col("shrinking", "VARCHAR(100)"),
        ],
        ["id"],
    )
    incoming = schema(
        "t",
        [
            col("id", "INTEGER"),
            col("wide", "VARCHAR(100)"),
            col("shrinking", "VARCHAR(10)"),
            col("brand_new", "DATE"),
        ],
        ["id"],
    )

    diffs = differ.diff(live, incoming, previous)
    emitted = set(kinds(diffs))
    assert DiffKind.ADDED_COLUMN in emitted
    assert DiffKind.MISSING_COLUMN in emitted
    assert DiffKind.TYPE_MISMATCH_NARROWING in emitted
    assert DiffKind.ORDER_CHANGED in emitted

    # old is NOT NULL without default and missing -> blocking;
    # shrinking narrows -> blocking. Everything else stays safe.
    blocking = {d.column for d in diffs if d.blocking}
    assert blocking == {"old", "shrinking"}

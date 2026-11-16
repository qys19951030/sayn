"""Tests for schema_audit data models and their (de)serialisation."""

import pytest

from sayn.schema_audit.models import (
    AuditResult,
    Column,
    Diff,
    DiffKind,
    LedgerEntry,
    TableSchema,
)


def test_column_round_trip():
    column = Column(
        name="id",
        db_type="INTEGER",
        nullable=False,
        default="0",
        position=2,
    )
    raw = column.to_dict()

    assert raw == {
        "name": "id",
        "type": "INTEGER",
        "nullable": False,
        "default": "0",
        "position": 2,
    }
    assert Column.from_dict(raw) == column


def test_column_from_dict_defaults():
    column = Column.from_dict({"name": "c", "type": "VARCHAR(5)"})

    assert column.nullable is True
    assert column.default is None
    assert column.position is None


def test_table_schema_helpers():
    schema = TableSchema(
        table="t",
        columns=[
            Column("a", "INTEGER", position=0),
            Column("b", "VARCHAR(10)", position=1),
        ],
        primary_key=["a"],
    )

    assert schema.column_names == ["a", "b"]
    assert schema.get_column("b").db_type == "VARCHAR(10)"
    assert schema.get_column("missing") is None

    restored = TableSchema.from_dict(schema.to_dict())
    assert restored == schema


def test_diff_round_trip_with_details():
    diff = Diff(
        kind=DiffKind.MISSING_COLUMN,
        column="cust_id",
        live_type=None,
        incoming_type="INTEGER",
        blocking=True,
        message="missing column cust_id",
        details={"nullable": False, "default": None},
    )

    assert Diff.from_dict(diff.to_dict()) == diff


def test_audit_result_dict_shape():
    result = AuditResult(table="t", task_name="task_x")

    assert result.has_differences is False
    raw = result.to_dict()
    assert raw["table"] == "t"
    assert raw["diffs"] == []
    assert raw["safe_to_load"] is True


def test_ledger_entry_round_trip_and_schema():
    entry = LedgerEntry(
        target_table="sales",
        columns=[Column("id", "INTEGER", nullable=False, position=0)],
        primary_key=["id"],
        rows_loaded=42,
        loaded_at="2026-11-16T10:00:00Z",
        task_name="load_sales",
        diffs=[
            Diff(
                kind=DiffKind.ADDED_COLUMN,
                column="note",
                live_type=None,
                incoming_type="VARCHAR(20)",
                blocking=False,
                message="added note",
            )
        ],
    )

    restored = LedgerEntry.from_dict(entry.to_dict())
    assert restored == entry

    snapshot = entry.schema()
    assert snapshot.table == "sales"
    assert snapshot.column_names == ["id"]
    assert snapshot.primary_key == ["id"]


def test_diff_kind_vocabulary_is_closed():
    assert len(DiffKind.ALL) == 7
    assert set(DiffKind.ALL) == {
        DiffKind.ADDED_COLUMN,
        DiffKind.MISSING_COLUMN,
        DiffKind.TYPE_MISMATCH_WIDENING,
        DiffKind.TYPE_MISMATCH_NARROWING,
        DiffKind.TYPE_MISMATCH_INCOMPATIBLE,
        DiffKind.ORDER_CHANGED,
        DiffKind.PRIMARY_KEY_CHANGED,
    }

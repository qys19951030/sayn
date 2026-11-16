"""Tests for the append-only ledger and ledger-only replay."""

import json

import pytest

from sayn.schema_audit.diff import SchemaDiffer
from sayn.schema_audit.errors import LedgerError, PreconditionError
from sayn.schema_audit.ledger import LedgerReplayError, LoadLedger
from sayn.schema_audit.models import (
    Column,
    Diff,
    DiffKind,
    LedgerEntry,
)


def make_entry(table, loaded_at, columns, primary_key, diffs=None, rows=1):
    return LedgerEntry(
        target_table=table,
        columns=columns,
        primary_key=primary_key,
        rows_loaded=rows,
        loaded_at=loaded_at,
        task_name=f"task_{table}",
        diffs=diffs or [],
    )


def col(name, db_type, position):
    return Column(name, db_type, nullable=True, position=position)


@pytest.fixture
def ledger(tmp_path):
    return LoadLedger(tmp_path / "ledger.jsonl")


def test_writable_precondition_creates_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "ledger.jsonl"
    ledger = LoadLedger(target)
    ledger.ensure_writable()
    assert target.parent.is_dir()


def test_writable_precondition_rejects_directory_path(tmp_path):
    ledger = LoadLedger(tmp_path)
    with pytest.raises(PreconditionError):
        ledger.ensure_writable()


def test_writable_precondition_reports_bad_location(tmp_path):
    # A regular file standing in for the parent directory makes mkdir fail
    # reliably, including when tests run as root.
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    ledger = LoadLedger(blocker / "nested" / "ledger.jsonl")
    with pytest.raises(PreconditionError) as exc:
        ledger.ensure_writable()
    assert "cannot be written" in str(exc.value)


def test_append_then_read_round_trip(ledger):
    entry = make_entry(
        "t",
        "2026-11-16T10:00:00Z",
        [col("a", "INTEGER", 0)],
        ["a"],
        rows=7,
    )
    ledger.ensure_writable()
    ledger.append(entry)

    raw_lines = ledger.path.read_text().strip().splitlines()
    assert len(raw_lines) == 1
    assert json.loads(raw_lines[0])["target_table"] == "t"

    restored = ledger.read_all()
    assert restored == [entry]


def test_entries_append_without_overwriting(ledger):
    ledger.ensure_writable()
    for index in range(3):
        ledger.append(
            make_entry(
                "t",
                f"2026-11-16T10:0{index}:00Z",
                [col("a", "INTEGER", 0)],
                ["a"],
                rows=index,
            )
        )

    assert len(ledger.read_all()) == 3
    assert [e.rows_loaded for e in ledger.read_all()] == [0, 1, 2]


def test_history_filters_by_table_and_sorts_on_time(ledger):
    ledger.ensure_writable()
    ledger.append(
        make_entry("b", "2026-11-16T12:00:00Z", [col("x", "DATE", 0)], [])
    )
    ledger.append(
        make_entry("a", "2026-11-16T12:00:00Z", [col("x", "DATE", 0)], [])
    )
    ledger.append(
        make_entry("a", "2026-11-16T09:00:00Z", [col("x", "DATE", 0)], [])
    )

    history = ledger.history_for("a")
    assert [e.loaded_at for e in history] == [
        "2026-11-16T09:00:00Z",
        "2026-11-16T12:00:00Z",
    ]


def test_replay_recomputes_order_change_from_ledger_only(ledger):
    ledger.ensure_writable()
    first = make_entry(
        "t",
        "2026-11-16T09:00:00Z",
        [col("a", "INTEGER", 0), col("b", "INTEGER", 1)],
        ["a"],
    )
    order_diffs = SchemaDiffer().diff(
        live_schema := TableSchemaBuilder.build(
            "t",
            [col("b", "INTEGER", 0), col("a", "INTEGER", 1)],
            ["a"],
        ),
        live_schema,
        first.schema(),
    )
    second = make_entry(
        "t",
        "2026-11-16T10:00:00Z",
        [col("b", "INTEGER", 0), col("a", "INTEGER", 1)],
        ["a"],
        diffs=order_diffs,
        rows=5,
    )
    ledger.append(first)
    ledger.append(second)

    change = ledger.replay_latest("t")

    assert change.matches_record is True
    assert change.change_kinds == [
        DiffKind.ORDER_CHANGED,
        DiffKind.ORDER_CHANGED,
    ]
    assert change.previous_loaded_at == "2026-11-16T09:00:00Z"
    assert change.current_loaded_at == "2026-11-16T10:00:00Z"


def test_replay_detects_record_that_does_not_match_snapshots(ledger):
    ledger.ensure_writable()
    first = make_entry(
        "t", "2026-11-16T09:00:00Z", [col("a", "INTEGER", 0)], ["a"]
    )
    # Same snapshots, but the stored record claims an order change that
    # never happened: replay must refuse to certify it.
    second = make_entry(
        "t",
        "2026-11-16T10:00:00Z",
        [col("a", "INTEGER", 0)],
        ["a"],
        diffs=[
            Diff(
                kind=DiffKind.ORDER_CHANGED,
                column="a",
                live_type=None,
                incoming_type=None,
                blocking=False,
                message="fabricated",
            )
        ],
    )
    ledger.append(first)
    ledger.append(second)

    change = ledger.replay_latest("t")
    assert change.matches_record is False
    assert change.recomputed_diffs == []


def test_replay_primary_key_change(ledger):
    ledger.ensure_writable()
    first = make_entry(
        "t", "2026-11-16T09:00:00Z", [col("a", "INTEGER", 0)], ["a"]
    )
    live = TableSchemaBuilder.build("t", [col("a", "INTEGER", 0)], [])
    pk_diffs = SchemaDiffer().diff(live, live, first.schema())
    second = make_entry(
        "t",
        "2026-11-16T10:00:00Z",
        [col("a", "INTEGER", 0)],
        [],
        diffs=pk_diffs,
    )
    ledger.append(first)
    ledger.append(second)

    change = ledger.replay_latest("t")
    assert change.change_kinds == [DiffKind.PRIMARY_KEY_CHANGED]
    assert change.matches_record is True
    assert change.recomputed_diffs[0].blocking is True


def test_replay_with_single_load_names_the_missing_piece(ledger):
    ledger.ensure_writable()
    ledger.append(
        make_entry("t", "2026-11-16T09:00:00Z", [col("a", "INTEGER", 0)], ["a"])
    )

    with pytest.raises(LedgerReplayError) as exc:
        ledger.replay_changes("t")
    assert "at least two adjacent loads" in str(exc.value)


def test_replay_with_unknown_table(ledger):
    ledger.ensure_writable()
    with pytest.raises(LedgerReplayError):
        ledger.replay_changes("ghost")


def test_corrupt_line_reports_line_number(ledger):
    ledger.ensure_writable()
    ledger.path.write_text("not-json\n")

    with pytest.raises(LedgerError) as exc:
        ledger.read_all()
    assert "line 1" in str(exc.value)


def test_entry_missing_required_field_is_reported(ledger):
    ledger.ensure_writable()
    ledger.path.write_text(json.dumps({"target_table": "t"}) + "\n")

    with pytest.raises(LedgerReplayError) as exc:
        ledger.read_all()
    assert "missing required field" in str(exc.value)


def test_replay_with_empty_snapshot_names_missing_piece(ledger):
    ledger.ensure_writable()
    complete = make_entry(
        "t", "2026-11-16T09:00:00Z", [col("a", "INTEGER", 0)], ["a"]
    )
    hollow = LedgerEntry.from_dict(
        {
            "target_table": "t",
            "task_name": "task_t",
            "loaded_at": "2026-11-16T10:00:00Z",
            "rows_loaded": 1,
            "columns": [],
            "primary_key": [],
            "diffs": [],
        }
    )
    ledger.append(complete)
    ledger.append(hollow)

    with pytest.raises(LedgerReplayError) as exc:
        ledger.replay_changes("t")
    message = str(exc.value)
    assert "columns snapshot at 2026-11-16T10:00:00Z" in message
    assert "missing" in message


class TableSchemaBuilder:
    @staticmethod
    def build(table, columns, primary_key):
        from sayn.schema_audit.models import TableSchema

        return TableSchema(table, columns, primary_key)

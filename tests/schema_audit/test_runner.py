"""End-to-end tests for the SchemaAuditor runner on sqlite."""

import pytest

from sayn.database.creator import create as create_db
from sayn.schema_audit.diff import SchemaDiffer
from sayn.schema_audit.errors import PolicyError, PreconditionError
from sayn.schema_audit.ledger import LoadLedger
from sayn.schema_audit.models import (
    Column,
    DiffKind,
    LoadContext,
    TableSchema,
)
from sayn.schema_audit.runner import AuditRequest, SchemaAuditor


@pytest.fixture
def database(tmp_path):
    settings = {"type": "sqlite", "database": str(tmp_path / "audit.db")}
    db = create_db("test", "test", settings)
    db._activate_connection()
    yield db
    db.engine.dispose()


def _ddl(database, name, sql):
    with database.engine.begin() as connection:
        connection.exec_driver_sql(sql.format(name=name))


def incoming(table, columns, primary_key=None):
    return TableSchema(table, columns, primary_key or [])


def request_for(table, schema, task="task_x", rows=10, source="src.t"):
    return AuditRequest(
        target_table=table,
        context=LoadContext(
            task_name=task, source_object=source, rows_loaded=rows
        ),
        incoming=schema,
    )


def auditor(database, tmp_path, policy="block"):
    return SchemaAuditor(
        database,
        tmp_path / "ledger" / "audit.jsonl",
        policy=policy,
        clock=lambda: "2026-11-16T10:00:00+00:00",
    )


def test_unknown_policy_rejected_at_construction(database, tmp_path):
    with pytest.raises(PolicyError):
        SchemaAuditor(database, tmp_path / "l.jsonl", policy="nope")


def test_precondition_missing_source_aborts_before_any_write(
    database, tmp_path
):
    ledger_path = tmp_path / "ledger.jsonl"
    audit = SchemaAuditor(database, ledger_path)
    request = request_for(
        "ghost", incoming("ghost", [Column("a", "INTEGER", position=0)])
    )

    report = audit.run_batch([request])

    assert report.ok is False
    assert isinstance(report.precondition_error, PreconditionError)
    assert "ghost" in str(report.precondition_error)
    assert report.results == []
    assert not ledger_path.exists()


def test_precondition_unwritable_ledger_aborts(database, tmp_path):
    _ddl(database, "t", "CREATE TABLE {name} (a INTEGER)")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")

    audit = SchemaAuditor(database, blocker / "ledger.jsonl")
    request = request_for(
        "t", incoming("t", [Column("a", "INTEGER", position=0)])
    )

    report = audit.run_batch([request])
    assert report.ok is False
    assert "cannot be written" in str(report.precondition_error)


def test_precondition_dead_connection_aborts(tmp_path):
    from sqlalchemy import create_engine

    class DeadDb:
        name = "dead"
        engine = create_engine("sqlite:////nonexistent_dir_xyz/dead.db")

    audit = SchemaAuditor(DeadDb(), tmp_path / "l.jsonl")
    report = audit.run_batch(
        [request_for("t", incoming("t", [Column("a", "INTEGER", position=0)]))]
    )
    assert report.ok is False
    assert "Cannot connect" in str(report.precondition_error)


def test_clean_batch_loads_and_appends_ledger(database, tmp_path):
    _ddl(database, "t", "CREATE TABLE {name} (a INTEGER)")
    audit = auditor(database, tmp_path)
    request = request_for(
        "t",
        incoming("t", [Column("a", "INTEGER", position=0)]),
        rows=12,
    )

    report = audit.run_batch([request])

    assert report.ok is True
    result = report.results[0]
    assert result.loaded is True
    assert result.rows_loaded == 12
    assert result.has_differences is False

    entries = LoadLedger(audit.ledger.path).read_all()
    assert len(entries) == 1
    assert entries[0].target_table == "t"
    assert entries[0].rows_loaded == 12
    assert entries[0].task_name == "task_x"
    assert entries[0].columns[0].name == "a"
    assert entries[0].loaded_at == "2026-11-16T10:00:00+00:00"


def test_safe_evolution_loads_and_records_the_change(database, tmp_path):
    _ddl(
        database,
        "t",
        "CREATE TABLE {name} (a INTEGER, b VARCHAR(10))",
    )
    audit = auditor(database, tmp_path)
    request = request_for(
        "t",
        incoming(
            "t",
            [
                Column("a", "BIGINT", position=0),
                Column("b", "VARCHAR(10)", position=1),
                Column("c", "DATE", position=2),
            ],
        ),
    )

    report = audit.run_batch([request])
    result = report.results[0]

    assert result.loaded is True
    kinds = {d.kind for d in result.diffs}
    assert DiffKind.ADDED_COLUMN in kinds
    assert DiffKind.TYPE_MISMATCH_WIDENING in kinds
    assert all(not d.blocking for d in result.diffs)

    entry = LoadLedger(audit.ledger.path).read_all()[0]
    assert {d.kind for d in entry.diffs} == kinds


def test_unsafe_diff_blocks_load_and_writes_nothing(database, tmp_path):
    _ddl(database, "t", "CREATE TABLE {name} (a VARCHAR(255))")
    ledger_path = tmp_path / "ledger.jsonl"
    audit = SchemaAuditor(database, ledger_path)
    request = request_for(
        "t",
        incoming("t", [Column("a", "VARCHAR(10)", position=0)]),
    )

    report = audit.run_batch([request])
    result = report.results[0]

    assert result.loaded is False
    assert result.safe_to_load is False
    assert result.blocking_diffs[0].kind == DiffKind.TYPE_MISMATCH_NARROWING
    assert result.blocking_diffs[0].column == "a"
    assert not ledger_path.exists()


def test_blocking_missing_not_null_column_names_field(database, tmp_path):
    _ddl(
        database,
        "t",
        "CREATE TABLE {name} (a INTEGER NOT NULL, b INTEGER)",
    )
    audit = auditor(database, tmp_path)
    request = request_for(
        "t", incoming("t", [Column("b", "INTEGER", position=0)])
    )

    result = audit.run_batch([request]).results[0]
    assert result.loaded is False
    blocking = result.blocking_diffs[0]
    assert blocking.kind == DiffKind.MISSING_COLUMN
    assert blocking.column == "a"
    assert blocking.details["nullable"] is False


def test_batch_stops_whole_table_but_other_tables_are_independent(
    database, tmp_path
):
    _ddl(database, "safe_t", "CREATE TABLE {name} (a INTEGER)")
    _ddl(database, "bad_t", "CREATE TABLE {name} (a VARCHAR(255))")
    audit = auditor(database, tmp_path)

    report = audit.run_batch(
        [
            request_for(
                "safe_t",
                incoming("safe_t", [Column("a", "INTEGER", position=0)]),
                task="task_safe",
            ),
            request_for(
                "bad_t",
                incoming("bad_t", [Column("a", "VARCHAR(10)", position=0)]),
                task="task_bad",
            ),
        ]
    )

    by_table = {r.table: r for r in report.results}
    assert by_table["safe_t"].loaded is True
    assert by_table["bad_t"].loaded is False

    summary = report.summary()
    assert summary.tables_audited == 2
    assert summary.tables_loaded == 1
    assert summary.tables_not_loaded == 1

    ledger_entries = LoadLedger(audit.ledger.path).read_all()
    assert [e.target_table for e in ledger_entries] == ["safe_t"]


def test_record_only_policy_loads_even_when_unsafe(database, tmp_path):
    _ddl(database, "t", "CREATE TABLE {name} (a VARCHAR(255))")
    audit = auditor(database, tmp_path, policy="record_only")
    request = request_for(
        "t",
        incoming("t", [Column("a", "VARCHAR(10)", position=0)]),
    )

    result = audit.run_batch([request]).results[0]
    assert result.loaded is True
    assert result.safe_to_load is False
    assert len(LoadLedger(audit.ledger.path).read_all()) == 1


def test_second_run_uses_previous_snapshot_for_order_detection(
    database, tmp_path
):
    _ddl(database, "t", "CREATE TABLE {name} (a INTEGER, b INTEGER)")
    audit = auditor(database, tmp_path)

    first_schema = incoming(
        "t",
        [Column("a", "INTEGER", position=0), Column("b", "INTEGER", position=1)],
    )
    audit.run_batch([request_for("t", first_schema, rows=1)])

    # Live table physically recreated with reversed column order.
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE t")
        connection.exec_driver_sql("CREATE TABLE t (b INTEGER, a INTEGER)")

    second_schema = incoming(
        "t",
        [Column("b", "INTEGER", position=0), Column("a", "INTEGER", position=1)],
    )
    result = audit.run_batch(
        [request_for("t", second_schema, rows=2)]
    ).results[0]

    assert result.loaded is True
    assert DiffKind.ORDER_CHANGED in {d.kind for d in result.diffs}

    history = LoadLedger(audit.ledger.path).history_for("t")
    assert [e.rows_loaded for e in history] == [1, 2]
    assert [e.loaded_at for e in history] == [
        "2026-11-16T10:00:00+00:00",
        "2026-11-16T10:00:00+00:00",
    ]


def test_ledger_replay_matches_recorded_diffs_after_two_loads(
    database, tmp_path
):
    _ddl(database, "t", "CREATE TABLE {name} (a INTEGER)")
    audit = auditor(database, tmp_path)
    audit.run_batch(
        [
            request_for(
                "t",
                incoming("t", [Column("a", "INTEGER", position=0)]),
                rows=1,
            )
        ]
    )

    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE t")
        connection.exec_driver_sql("CREATE TABLE t (b INTEGER, a INTEGER)")
    audit.run_batch(
        [
            request_for(
                "t",
                incoming(
                    "t",
                    [
                        Column("b", "INTEGER", position=0),
                        Column("a", "INTEGER", position=1),
                    ],
                ),
                rows=2,
            )
        ]
    )

    replay = LoadLedger(audit.ledger.path).replay_latest("t")
    assert replay.matches_record is True
    assert DiffKind.ORDER_CHANGED in replay.change_kinds

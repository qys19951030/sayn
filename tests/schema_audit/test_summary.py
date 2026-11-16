"""Tests for the human-readable batch summary."""

from sayn.schema_audit.models import AuditResult, Diff, DiffKind
from sayn.schema_audit.summary import SummaryBuilder


def make_diff(kind, column=None, blocking=False):
    return Diff(
        kind=kind,
        column=column,
        live_type="VARCHAR(10)" if column else None,
        incoming_type="VARCHAR(20)" if column else None,
        blocking=blocking,
        message="m",
    )


def make_results():
    clean = AuditResult(table="clean_t", task_name="task_clean", loaded=True, rows_loaded=3)
    safe = AuditResult(
        table="safe_t",
        task_name="task_safe",
        diffs=[
            make_diff(DiffKind.ADDED_COLUMN, "new_col"),
            make_diff(DiffKind.TYPE_MISMATCH_WIDENING, "wide_col"),
        ],
        loaded=True,
        rows_loaded=11,
        safe_to_load=True,
    )
    blocked = AuditResult(
        table="blocked_t",
        task_name="task_blocked",
        diffs=[
            make_diff(DiffKind.TYPE_MISMATCH_NARROWING, "narrow_col", blocking=True),
            make_diff(DiffKind.PRIMARY_KEY_CHANGED, blocking=True),
        ],
        blocking_diffs=[
            make_diff(DiffKind.TYPE_MISMATCH_NARROWING, "narrow_col", blocking=True),
            make_diff(DiffKind.PRIMARY_KEY_CHANGED, blocking=True),
        ],
        safe_to_load=False,
        loaded=False,
    )
    return [clean, safe, blocked]


def test_summary_counts():
    summary = SummaryBuilder().build(make_results())

    assert summary.tables_audited == 3
    assert summary.tables_with_differences == 2
    assert summary.tables_loaded == 2
    assert summary.tables_not_loaded == 1
    assert summary.diff_counts[DiffKind.ADDED_COLUMN] == 1
    assert summary.blocking_counts[DiffKind.PRIMARY_KEY_CHANGED] == 1
    assert [r.table for r in summary.blocked_tables] == ["blocked_t"]


def test_summary_dict_names_and_numbers_match_results():
    raw = SummaryBuilder().build(make_results()).to_dict()

    table_names = {entry["table"] for entry in raw["tables"]}
    assert table_names == {"clean_t", "safe_t", "blocked_t"}

    blocked_entry = next(
        entry for entry in raw["tables"] if entry["table"] == "blocked_t"
    )
    assert blocked_entry["loaded"] is False
    assert "narrow_col" in blocked_entry["blocking_columns"]
    assert "primary_key" in blocked_entry["blocking_columns"]

    safe_entry = next(
        entry for entry in raw["tables"] if entry["table"] == "safe_t"
    )
    assert safe_entry["rows_loaded"] == 11
    assert DiffKind.ADDED_COLUMN in safe_entry["diff_kinds"]


def test_rendered_text_contains_every_table_and_category():
    text = SummaryBuilder().render_text(make_results())

    assert "Tables audited:            3" in text
    assert "Tables not loaded:         1" in text
    for table in ("clean_t", "safe_t", "blocked_t"):
        assert table in text
    assert "added column" in text
    assert "narrowing, unsafe" in text
    assert "primary key change" in text
    assert "blocked on narrow_col, primary_key" in text
    assert "blocked_t" in text
    assert "NOT loaded" in text


def test_rendered_clean_batch():
    text = SummaryBuilder().render_text(
        [AuditResult(table="only_t", task_name="t", loaded=True, rows_loaded=1)]
    )
    assert "Tables audited:            1" in text
    assert "Tables with differences:   0" in text
    assert "did not land" not in text

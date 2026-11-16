"""Human-readable audit summaries.

The summary is rendered from the same :class:`AuditResult` objects the
machine consumer receives, so table names, field names and counts can never
drift between the two outputs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .models import AuditResult, Diff, DiffKind


# Short, fixed human labels for every diff category.
KIND_LABELS: Dict[str, str] = {
    DiffKind.ADDED_COLUMN: "added column",
    DiffKind.MISSING_COLUMN: "missing column",
    DiffKind.TYPE_MISMATCH_WIDENING: "type mismatch (widening, safe)",
    DiffKind.TYPE_MISMATCH_NARROWING: "type mismatch (narrowing, unsafe)",
    DiffKind.TYPE_MISMATCH_INCOMPATIBLE: "type mismatch (incompatible, unsafe)",
    DiffKind.ORDER_CHANGED: "column order change",
    DiffKind.PRIMARY_KEY_CHANGED: "primary key change",
}


@dataclass
class BatchSummary:
    """Counts and roll-ups for one audit batch."""

    tables_audited: int = 0
    tables_with_differences: int = 0
    tables_loaded: int = 0
    tables_not_loaded: int = 0
    diff_counts: Counter = field(default_factory=Counter)
    blocking_counts: Counter = field(default_factory=Counter)
    results: List[AuditResult] = field(default_factory=list)

    @property
    def diff_tables(self) -> List[AuditResult]:
        return [result for result in self.results if result.has_differences]

    @property
    def blocked_tables(self) -> List[AuditResult]:
        return [
            result
            for result in self.results
            if result.has_differences and not result.loaded
        ]

    def to_dict(self) -> Dict[str, object]:
        return {
            "tables_audited": self.tables_audited,
            "tables_with_differences": self.tables_with_differences,
            "tables_loaded": self.tables_loaded,
            "tables_not_loaded": self.tables_not_loaded,
            "diff_counts": dict(self.diff_counts),
            "blocking_counts": dict(self.blocking_counts),
            "tables": [
                {
                    "table": result.table,
                    "task_name": result.task_name,
                    "has_differences": result.has_differences,
                    "loaded": result.loaded,
                    "rows_loaded": result.rows_loaded,
                    "diff_kinds": [diff.kind for diff in result.diffs],
                    "blocking_columns": [
                        diff.column if diff.column is not None else "primary_key"
                        for diff in result.blocking_diffs
                    ],
                }
                for result in self.results
            ],
        }


class SummaryBuilder:
    """Builds a readable summary consistent with the machine results."""

    def build(self, results: Sequence[AuditResult]) -> BatchSummary:
        summary = BatchSummary(results=list(results))
        summary.tables_audited = len(results)
        summary.tables_with_differences = len(summary.diff_tables)
        summary.tables_loaded = sum(1 for result in results if result.loaded)
        summary.tables_not_loaded = summary.tables_audited - summary.tables_loaded

        for result in results:
            summary.diff_counts.update(diff.kind for diff in result.diffs)
            summary.blocking_counts.update(
                diff.kind for diff in result.blocking_diffs
            )
        return summary

    def render_text(self, results: Sequence[AuditResult]) -> str:
        summary = self.build(results)
        lines: List[str] = []
        lines.append("Schema audit summary")
        lines.append("=" * 20)
        lines.append(f"Tables audited:            {summary.tables_audited}")
        lines.append(
            f"Tables with differences:   {summary.tables_with_differences}"
        )
        lines.append(f"Tables loaded:             {summary.tables_loaded}")
        lines.append(f"Tables not loaded:         {summary.tables_not_loaded}")

        if summary.diff_counts:
            lines.append("")
            lines.append("Difference categories:")
            for kind in DiffKind.ALL:
                if summary.diff_counts.get(kind):
                    lines.append(
                        f"  - {KIND_LABELS[kind]}: {summary.diff_counts[kind]}"
                    )

        if summary.diff_tables:
            lines.append("")
            lines.append("Tables with structural differences:")
            for result in summary.diff_tables:
                lines.append(self._render_table(result))

        clean_tables = [
            result for result in results if not result.has_differences
        ]
        if clean_tables:
            lines.append("")
            lines.append("Tables without differences:")
            for result in clean_tables:
                rows = (
                    f", {result.rows_loaded} rows"
                    if result.loaded and result.rows_loaded is not None
                    else ""
                )
                lines.append(
                    f"  - {result.table} (task {result.task_name}) "
                    f"[{'loaded' if result.loaded else 'NOT loaded'}{rows}]"
                )

        blocked = summary.blocked_tables
        if blocked:
            lines.append("")
            lines.append("Tables that did not land:")
            for result in blocked:
                columns = self._blocking_columns(result.blocking_diffs)
                lines.append(
                    f"  - {result.table} (task {result.task_name}): "
                    f"blocked on {', '.join(columns)}"
                )

        return "\n".join(lines)

    def _render_table(self, result: AuditResult) -> str:
        category_counts = Counter(diff.kind for diff in result.diffs)
        categories = ", ".join(
            f"{KIND_LABELS[kind]} x{count}"
            for kind, count in sorted(category_counts.items())
        )
        status = "loaded" if result.loaded else "NOT loaded"
        rows = (
            f", {result.rows_loaded} rows"
            if result.loaded and result.rows_loaded is not None
            else ""
        )
        return f"  - {result.table} (task {result.task_name}) [{status}{rows}]: {categories}"

    @staticmethod
    def _blocking_columns(diffs: Sequence[Diff]) -> List[str]:
        columns: List[str] = []
        for diff in diffs:
            label = diff.column if diff.column is not None else "primary_key"
            if label not in columns:
                columns.append(label)
        return columns

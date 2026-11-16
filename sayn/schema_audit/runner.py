"""Entry point orchestrating precondition checks, audits and the ledger.

The auditor is deliberately decoupled from the load pipeline: callers hand
it a live SAYN ``Database``, a ledger path, a policy and a list of audit
requests; it never executes a load itself. Under the default ``block``
policy a table with an unsafe diff is reported as not loaded and no ledger
row is written for it, so a batch either lands cleanly or leaves the ledger
untouched for the blocked table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from .diff import SchemaDiffer
from .errors import PreconditionError, SchemaAuditError
from .ledger import LoadLedger
from .models import AuditResult, LedgerEntry, LoadContext, TableSchema
from .policy import PolicyMode, SafetyPolicy
from .reader import SchemaReader
from .summary import BatchSummary, SummaryBuilder


@dataclass(frozen=True)
class AuditRequest:
    """One table to audit: how to read it and which load it belongs to."""

    target_table: str
    context: LoadContext
    incoming: TableSchema
    schema: Optional[str] = None


@dataclass
class BatchReport:
    """Everything produced by one batch run."""

    results: List[AuditResult] = field(default_factory=list)
    precondition_error: Optional[PreconditionError] = None

    @property
    def ok(self) -> bool:
        return self.precondition_error is None

    def summary(self) -> BatchSummary:
        return SummaryBuilder().build(self.results)

    def render_summary(self) -> str:
        return SummaryBuilder().render_text(self.results)


class SchemaAuditor:
    """Runs structural audits for a batch of target tables."""

    def __init__(
        self,
        database,
        ledger_path,
        policy: str = PolicyMode.BLOCK,
        differ: Optional[SchemaDiffer] = None,
        reader: Optional[SchemaReader] = None,
        clock=None,
    ) -> None:
        self.database = database
        self.ledger = LoadLedger(ledger_path, differ=differ)
        self.policy = SafetyPolicy(policy)
        self.differ = differ or SchemaDiffer()
        self.reader = reader or SchemaReader()
        # Injectable clock for deterministic tests; defaults to UTC ISO time.
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------------
    # Preconditions: all of them before anything is written
    # ------------------------------------------------------------------
    def check_preconditions(self, requests: Sequence[AuditRequest]) -> None:
        self.reader.check_connection(self.database)

        for request in requests:
            if not self.reader.object_exists(
                self.database, request.target_table, request.schema
            ):
                raise PreconditionError(
                    f"Source object '{self._qualify(request)}' does not exist "
                    f"on database '{getattr(self.database, 'name', '?')}'"
                )

        # Policy value is validated by SafetyPolicy at construction, but a
        # named check keeps the precondition contract explicit.
        if self.policy.mode not in PolicyMode.ALL:
            raise PreconditionError(
                f"Unknown disposal policy '{self.policy.mode}'"
            )

        self.ledger.ensure_writable()

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------
    def run_batch(self, requests: Sequence[AuditRequest]) -> BatchReport:
        report = BatchReport()
        try:
            self.check_preconditions(requests)
        except PreconditionError as exc:
            # Nothing has been audited or written: hand the named error back
            # instead of producing a half-written ledger.
            report.precondition_error = exc
            return report

        for request in requests:
            report.results.append(self._audit_one(request))
        return report

    def _audit_one(self, request: AuditRequest) -> AuditResult:
        live = self.reader.read_schema(
            self.database, request.target_table, request.schema
        )
        previous = self._previous_snapshot(request.target_table)
        diffs = self.differ.diff(live, request.incoming, previous)
        decision = self.policy.evaluate(diffs)

        if decision.enforced and not decision.safe_to_load:
            # Unsafe under the blocking policy: not a single row lands and
            # nothing is appended to the ledger for this table.
            return AuditResult(
                table=request.target_table,
                task_name=request.context.task_name,
                diffs=diffs,
                blocking_diffs=list(decision.blocking_diffs),
                safe_to_load=False,
                loaded=False,
                rows_loaded=None,
            )

        rows_loaded = self._resolved_rows(request)
        loaded_at = request.context.loaded_at or self._clock()
        entry = LedgerEntry(
            target_table=request.target_table,
            columns=list(live.columns),
            primary_key=list(live.primary_key),
            rows_loaded=rows_loaded,
            loaded_at=loaded_at,
            task_name=request.context.task_name,
            diffs=diffs,
        )
        self.ledger.append(entry)

        return AuditResult(
            table=request.target_table,
            task_name=request.context.task_name,
            diffs=diffs,
            blocking_diffs=list(decision.blocking_diffs),
            safe_to_load=decision.safe_to_load,
            loaded=True,
            rows_loaded=rows_loaded,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _previous_snapshot(self, target_table: str) -> Optional[TableSchema]:
        history = self.ledger.history_for(target_table)
        if not history:
            return None
        return history[-1].schema()

    @staticmethod
    def _resolved_rows(request: AuditRequest) -> int:
        rows = request.context.rows_loaded
        if rows is None:
            raise SchemaAuditError(
                f"Cannot write a ledger entry for '{request.target_table}': "
                "rows_loaded is required for a completed load"
            )
        if rows < 0:
            raise SchemaAuditError(
                f"Cannot write a ledger entry for '{request.target_table}': "
                "rows_loaded cannot be negative"
            )
        return rows

    @staticmethod
    def _qualify(request: AuditRequest) -> str:
        return (
            f"{request.schema}.{request.target_table}"
            if request.schema
            else request.target_table
        )

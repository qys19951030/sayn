"""Schema audit and load ledger package.

Read-only structural reconciliation between a live target table and the
data about to be loaded. The package never executes loads, never touches
the DAG and never performs DDL: it produces structured diffs, a load
ledger and human-readable summaries.
"""

from .errors import SchemaAuditError

__all__ = ["SchemaAuditError"]

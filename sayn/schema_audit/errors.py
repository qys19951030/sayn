"""Errors raised by the schema_audit package."""


class SchemaAuditError(Exception):
    """Base class for all schema_audit errors."""


class PreconditionError(SchemaAuditError):
    """Raised when a run precondition cannot be satisfied."""


class PolicyError(SchemaAuditError):
    """Raised when the configured disposal policy is not recognised."""


class LedgerError(SchemaAuditError):
    """Raised when the ledger cannot be written or cannot support a replay."""

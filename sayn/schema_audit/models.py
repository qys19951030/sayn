"""Structural data models shared across the schema_audit package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Column:
    """A single column as seen on one side of a comparison."""


@dataclass(frozen=True)
class TableSchema:
    """An ordered snapshot of a table's columns and primary key."""


@dataclass(frozen=True)
class LoadContext:
    """Parameters describing the load being audited."""


@dataclass(frozen=True)
class Diff:
    """One structural difference between two table schemas."""


@dataclass(frozen=True)
class AuditResult:
    """Outcome of auditing one target table."""


@dataclass(frozen=True)
class LedgerEntry:
    """One record appended to the load ledger."""

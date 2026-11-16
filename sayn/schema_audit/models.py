"""Structural data models shared across the schema_audit package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class DiffKind:
    """Fixed vocabulary of structural difference categories."""

    ADDED_COLUMN = "added_column"
    MISSING_COLUMN = "missing_column"
    TYPE_MISMATCH_WIDENING = "type_mismatch_widening"
    TYPE_MISMATCH_NARROWING = "type_mismatch_narrowing"
    TYPE_MISMATCH_INCOMPATIBLE = "type_mismatch_incompatible"
    ORDER_CHANGED = "order_changed"
    PRIMARY_KEY_CHANGED = "primary_key_changed"

    ALL = (
        ADDED_COLUMN,
        MISSING_COLUMN,
        TYPE_MISMATCH_WIDENING,
        TYPE_MISMATCH_NARROWING,
        TYPE_MISMATCH_INCOMPATIBLE,
        ORDER_CHANGED,
        PRIMARY_KEY_CHANGED,
    )


@dataclass(frozen=True)
class Column:
    """A single column as seen on one side of a comparison.

    Attributes:
        name: column name
        db_type: database type as rendered by the engine, e.g. ``VARCHAR(10)``
        nullable: whether the column accepts NULL
        default: server-side default rendered as text, or None
        position: ordinal position in the table, starting at 0
    """

    name: str
    db_type: str
    nullable: bool = True
    default: Optional[str] = None
    position: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.db_type,
            "nullable": self.nullable,
            "default": self.default,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Column":
        return cls(
            name=raw["name"],
            db_type=raw["type"],
            nullable=bool(raw.get("nullable", True)),
            default=raw.get("default"),
            position=raw.get("position"),
        )


@dataclass(frozen=True)
class TableSchema:
    """An ordered snapshot of a table's columns and primary key."""

    table: str
    columns: List[Column] = field(default_factory=list)
    primary_key: List[str] = field(default_factory=list)

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def get_column(self, name: str) -> Optional[Column]:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "columns": [c.to_dict() for c in self.columns],
            "primary_key": list(self.primary_key),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "TableSchema":
        return cls(
            table=raw["table"],
            columns=[Column.from_dict(c) for c in raw.get("columns", [])],
            primary_key=list(raw.get("primary_key", [])),
        )


@dataclass(frozen=True)
class LoadContext:
    """Parameters describing the load being audited."""

    task_name: str
    source_object: Optional[str] = None
    rows_loaded: Optional[int] = None
    loaded_at: Optional[str] = None


@dataclass(frozen=True)
class Diff:
    """One structural difference between two table schemas.

    ``live_type`` / ``incoming_type`` carry the concrete types on each side
    so every diff names the fields and types involved. ``blocking`` records
    the fixed safety boundary; ``details`` holds category-specific facts
    (nullability, defaults, previous order, ...).
    """

    kind: str
    column: Optional[str]
    live_type: Optional[str]
    incoming_type: Optional[str]
    blocking: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "column": self.column,
            "live_type": self.live_type,
            "incoming_type": self.incoming_type,
            "blocking": self.blocking,
            "message": self.message,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Diff":
        return cls(
            kind=raw["kind"],
            column=raw.get("column"),
            live_type=raw.get("live_type"),
            incoming_type=raw.get("incoming_type"),
            blocking=bool(raw.get("blocking", False)),
            message=raw.get("message", ""),
            details=dict(raw.get("details", {})),
        )


@dataclass(frozen=True)
class AuditResult:
    """Outcome of auditing one target table."""

    table: str
    task_name: str
    diffs: List[Diff] = field(default_factory=list)
    blocking_diffs: List[Diff] = field(default_factory=list)
    safe_to_load: bool = True
    loaded: bool = False
    rows_loaded: Optional[int] = None

    @property
    def has_differences(self) -> bool:
        return len(self.diffs) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "task_name": self.task_name,
            "safe_to_load": self.safe_to_load,
            "loaded": self.loaded,
            "rows_loaded": self.rows_loaded,
            "diffs": [d.to_dict() for d in self.diffs],
            "blocking_diffs": [d.to_dict() for d in self.blocking_diffs],
        }


@dataclass(frozen=True)
class LedgerEntry:
    """One record appended to the load ledger after a completed load."""

    target_table: str
    columns: List[Column]
    primary_key: List[str]
    rows_loaded: int
    loaded_at: str
    task_name: str
    diffs: List[Diff] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_table": self.target_table,
            "task_name": self.task_name,
            "loaded_at": self.loaded_at,
            "rows_loaded": self.rows_loaded,
            "columns": [c.to_dict() for c in self.columns],
            "primary_key": list(self.primary_key),
            "diffs": [d.to_dict() for d in self.diffs],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LedgerEntry":
        return cls(
            target_table=raw["target_table"],
            task_name=raw["task_name"],
            loaded_at=raw["loaded_at"],
            rows_loaded=raw["rows_loaded"],
            columns=[Column.from_dict(c) for c in raw.get("columns", [])],
            primary_key=list(raw.get("primary_key", [])),
            diffs=[Diff.from_dict(d) for d in raw.get("diffs", [])],
        )

    def schema(self) -> TableSchema:
        """Reconstruct the structural snapshot stored in this entry."""
        return TableSchema(
            table=self.target_table,
            columns=list(self.columns),
            primary_key=list(self.primary_key),
        )

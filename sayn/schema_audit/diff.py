"""Per-table structural diffing between live and incoming schemas.

Two comparisons happen at once:

* **live vs incoming** drives column presence and type diffs, i.e. the
  safety of the pending load;
* **previous snapshot vs live** drives order and primary-key diffs, which
  describe how the deployed table drifted since the last audited load.

The blocking boundary applied here is fixed:

* primary key changes are always blocking;
* narrowing or incompatible type changes are blocking;
* a missing column is blocking only when it is ``NOT NULL`` without a
  server default.

Every other diff is a safe evolution that is recorded but lets the load
proceed.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .models import Column, Diff, DiffKind, TableSchema
from .types import TypeComparator


class SchemaDiffer:
    """Produces the classified list of differences for one table."""

    def __init__(self, type_comparator: Optional[TypeComparator] = None) -> None:
        self.type_comparator = type_comparator or TypeComparator()

    def diff(
        self,
        live: TableSchema,
        incoming: TableSchema,
        previous: Optional[TableSchema] = None,
    ) -> List[Diff]:
        diffs: List[Diff] = []
        diffs.extend(self._diff_columns(live, incoming))
        diffs.extend(self._diff_order(previous, live))
        diffs.extend(self._diff_primary_key(previous, live))
        return diffs

    # ------------------------------------------------------------------
    # Column presence and types
    # ------------------------------------------------------------------
    def _diff_columns(
        self, live: TableSchema, incoming: TableSchema
    ) -> List[Diff]:
        out: List[Diff] = []
        live_names = set(live.column_names)
        incoming_names = set(incoming.column_names)

        for column in incoming.columns:
            if column.name not in live_names:
                out.append(self._added_column(column))

        for column in live.columns:
            if column.name not in incoming_names:
                out.append(self._missing_column(column))

        for incoming_column in incoming.columns:
            live_column = live.get_column(incoming_column.name)
            if live_column is None:
                continue
            type_diff = self._type_diff(live_column, incoming_column)
            if type_diff is not None:
                out.append(type_diff)
        return out

    @staticmethod
    def _added_column(column: Column) -> Diff:
        return Diff(
            kind=DiffKind.ADDED_COLUMN,
            column=column.name,
            live_type=None,
            incoming_type=column.db_type,
            blocking=False,
            message=(
                f"New column '{column.name}' ({column.db_type}) is present in "
                "the incoming data but not in the live table"
            ),
            details={"nullable": column.nullable, "default": column.default},
        )

    @staticmethod
    def _missing_column(column: Column) -> Diff:
        blocking = (not column.nullable) and column.default is None
        reason = (
            "NOT NULL without a default: existing rows cannot satisfy it"
            if blocking
            else "nullable or has a default"
        )
        return Diff(
            kind=DiffKind.MISSING_COLUMN,
            column=column.name,
            live_type=column.db_type,
            incoming_type=None,
            blocking=blocking,
            message=(
                f"Column '{column.name}' ({column.db_type}) exists in the live "
                f"table but is absent from the incoming data; {reason}"
            ),
            details={
                "nullable": column.nullable,
                "default": column.default,
                "unsafe_reason": "not_null_without_default" if blocking else None,
            },
        )

    def _type_diff(
        self, live_column: Column, incoming_column: Column
    ) -> Optional[Diff]:
        classification = self.type_comparator.classify(
            live_column.db_type, incoming_column.db_type
        )
        if classification.is_equal:
            return None

        if classification.is_widening:
            kind = DiffKind.TYPE_MISMATCH_WIDENING
            blocking = False
            adjective = "widening"
        elif classification.is_narrowing:
            kind = DiffKind.TYPE_MISMATCH_NARROWING
            blocking = True
            adjective = "narrowing"
        else:
            kind = DiffKind.TYPE_MISMATCH_INCOMPATIBLE
            blocking = True
            adjective = "incompatible"

        return Diff(
            kind=kind,
            column=live_column.name,
            live_type=classification.live_type,
            incoming_type=classification.incoming_type,
            blocking=blocking,
            message=(
                f"Column '{live_column.name}' type changes "
                f"{classification.live_type} -> {classification.incoming_type} "
                f"({adjective})"
            ),
            details={
                "family": classification.family,
                "direction": classification.direction,
            },
        )

    # ------------------------------------------------------------------
    # Ordering, measured against the previous audited snapshot
    # ------------------------------------------------------------------
    def _diff_order(
        self, previous: Optional[TableSchema], live: TableSchema
    ) -> List[Diff]:
        if previous is None:
            return []

        previous_order = [
            name for name in previous.column_names if name in set(live.column_names)
        ]
        live_order = [
            name for name in live.column_names if name in set(previous.column_names)
        ]
        if previous_order == live_order:
            return []

        previous_positions = {
            name: position for position, name in enumerate(previous.column_names)
        }
        moved: List[Tuple[str, Optional[int], Optional[int]]] = []
        for new_position, name in enumerate(live.column_names):
            if name in previous_positions and previous_positions[name] != new_position:
                moved.append((name, previous_positions[name], new_position))

        return [
            Diff(
                kind=DiffKind.ORDER_CHANGED,
                column=name,
                live_type=None,
                incoming_type=None,
                blocking=False,
                message=(
                    f"Column '{name}' moved from position {old_position} to "
                    f"{new_position} since the previous snapshot"
                ),
                details={
                    "previous_position": old_position,
                    "live_position": new_position,
                },
            )
            for name, old_position, new_position in moved
        ]

    # ------------------------------------------------------------------
    # Primary key, measured against the previous audited snapshot
    # ------------------------------------------------------------------
    def _diff_primary_key(
        self, previous: Optional[TableSchema], live: TableSchema
    ) -> List[Diff]:
        if previous is None:
            return []
        if previous.primary_key == live.primary_key:
            return []

        previous_set = set(previous.primary_key)
        live_set = set(live.primary_key)
        added = [name for name in live.primary_key if name not in previous_set]
        removed = [name for name in previous.primary_key if name not in live_set]
        reordered = (
            not added
            and not removed
            and sorted(previous_set) == sorted(live_set)
        )

        return [
            Diff(
                kind=DiffKind.PRIMARY_KEY_CHANGED,
                column=None,
                live_type=", ".join(live.primary_key) or None,
                incoming_type=", ".join(previous.primary_key) or None,
                blocking=True,
                message=self._pk_message(
                    previous.primary_key, live.primary_key, added, removed, reordered
                ),
                details={
                    "previous_primary_key": list(previous.primary_key),
                    "live_primary_key": list(live.primary_key),
                    "added": added,
                    "removed": removed,
                    "reordered": reordered,
                },
            )
        ]

    @staticmethod
    def _pk_message(previous_pk, live_pk, added, removed, reordered) -> str:
        if reordered:
            return (
                "Primary key column order changed: "
                f"{previous_pk} -> {live_pk}"
            )
        parts = []
        if added:
            parts.append(f"added {added}")
        if removed:
            parts.append(f"removed {removed}")
        return (
            "Primary key changed "
            f"({'; '.join(parts)}): {previous_pk} -> {live_pk}"
        )

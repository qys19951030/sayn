"""Append-only JSON-lines load ledger with replay support.

One line per completed load, oldest first. The ledger is the only input
needed to answer "what changed structurally between two loads of a table":
each entry stores the full column/type snapshot, the primary key and the
exact diffs that were recorded at load time, so a replay can recompute the
diff independently and verify it matches.

A ledger that does not contain enough information for a replay never
returns a plausible-looking answer: :class:`LedgerReplayError` names the
missing piece instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .diff import SchemaDiffer
from .errors import LedgerError, PreconditionError
from .models import Diff, DiffKind, LedgerEntry, TableSchema

# Every entry must carry these keys for a replay to be possible.
_LEDGER_REQUIRED_KEYS = (
    "target_table",
    "task_name",
    "loaded_at",
    "rows_loaded",
    "columns",
    "primary_key",
    "diffs",
)


class LedgerReplayError(LedgerError):
    """Raised when the ledger holds insufficient data for a replay."""


class LoadLedger:
    """Reads and appends structural snapshots, one JSON record per line."""

    def __init__(self, path, differ: Optional[SchemaDiffer] = None) -> None:
        self.path = Path(path)
        self.differ = differ or SchemaDiffer()

    # ------------------------------------------------------------------
    # Precondition
    # ------------------------------------------------------------------
    def ensure_writable(self) -> None:
        """Validate the ledger location before any audit work starts."""
        try:
            parent = self.path.parent
            parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                if not self.path.is_file():
                    raise PreconditionError(
                        f"Ledger path '{self.path}' exists but is not a file"
                    )
                if not os.access(self.path, os.W_OK):
                    raise PreconditionError(
                        f"Ledger file '{self.path}' is not writable"
                    )
            else:
                # Touch and remove so permission problems surface up front.
                self.path.touch()
                self.path.unlink()
        except OSError as exc:
            raise PreconditionError(
                f"Ledger path '{self.path}' cannot be written: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Append / read
    # ------------------------------------------------------------------
    def append(self, entry: LedgerEntry) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        except OSError as exc:
            raise LedgerError(
                f"Failed to append ledger entry to '{self.path}': {exc}"
            ) from exc

    def read_all(self) -> List[LedgerEntry]:
        if not self.path.exists():
            return []
        entries: List[LedgerEntry] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    entries.append(self._parse_line(line, line_number))
        except OSError as exc:
            raise LedgerError(
                f"Failed to read ledger '{self.path}': {exc}"
            ) from exc
        return entries

    def history_for(self, target_table: str) -> List[LedgerEntry]:
        history = [
            entry
            for entry in self.read_all()
            if entry.target_table == target_table
        ]
        return sorted(history, key=lambda entry: entry.loaded_at)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------
    def replay_changes(
        self, target_table: str
    ) -> List["ReplayedChange"]:
        """Recompute, from the ledger alone, the changes across adjacent loads.

        Returns one :class:`ReplayedChange` per consecutive pair of loads of
        ``target_table`` ordered by load time.
        """
        history = self.history_for(target_table)
        if len(history) < 2:
            raise LedgerReplayError(
                f"Cannot replay changes for '{target_table}': the ledger holds "
                f"{len(history)} load(s), at least two adjacent loads are needed"
            )

        changes: List[ReplayedChange] = []
        for previous, current in zip(history, history[1:]):
            self._validate_replay_inputs(target_table, previous, current)
            # Column presence/type diffs were computed against the incoming
            # data, which the ledger does not store; order and primary-key
            # diffs are fully determined by the two stored snapshots.
            recomputed = self.differ.diff(
                current.schema(), current.schema(), previous.schema()
            )
            recorded_snapshot_diffs = [
                diff
                for diff in current.diffs
                if diff.kind
                in (DiffKind.ORDER_CHANGED, DiffKind.PRIMARY_KEY_CHANGED)
            ]
            changes.append(
                ReplayedChange(
                    target_table=target_table,
                    previous_loaded_at=previous.loaded_at,
                    current_loaded_at=current.loaded_at,
                    recorded_diffs=list(current.diffs),
                    recomputed_diffs=recomputed,
                    matches_record=self._diffs_equal(
                        recorded_snapshot_diffs, recomputed
                    ),
                )
            )
        return changes

    def replay_latest(self, target_table: str) -> "ReplayedChange":
        """Replay only the most recent pair of adjacent loads of a table."""
        return self.replay_changes(target_table)[-1]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _parse_line(self, line: str, line_number: int) -> LedgerEntry:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(
                f"Ledger '{self.path}' line {line_number} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise LedgerError(
                f"Ledger '{self.path}' line {line_number} must be a JSON object"
            )
        missing = [key for key in _LEDGER_REQUIRED_KEYS if key not in raw]
        if missing:
            raise LedgerReplayError(
                f"Ledger '{self.path}' line {line_number} is missing required "
                f"field(s): {', '.join(missing)}"
            )
        return LedgerEntry.from_dict(raw)

    def _validate_replay_inputs(
        self,
        target_table: str,
        previous: LedgerEntry,
        current: LedgerEntry,
    ) -> None:
        missing: List[str] = []
        if not previous.columns:
            missing.append(
                f"columns snapshot at {previous.loaded_at}"
            )
        if not current.columns:
            missing.append(
                f"columns snapshot at {current.loaded_at}"
            )
        if previous.primary_key is None or current.primary_key is None:
            missing.append("primary key")
        if missing:
            raise LedgerReplayError(
                f"Cannot replay changes for '{target_table}' between "
                f"{previous.loaded_at} and {current.loaded_at}: the ledger is "
                f"missing {', '.join(missing)}"
            )

    @staticmethod
    def _diffs_equal(recorded: List[Diff], recomputed: List[Diff]) -> bool:
        def signature(diff: Diff) -> Tuple:
            return (
                diff.kind,
                diff.column,
                diff.live_type,
                diff.incoming_type,
                diff.blocking,
            )

        return sorted(signature(d) for d in recorded) == sorted(
            signature(d) for d in recomputed
        )


@dataclass(frozen=True)
class ReplayedChange:
    """The verified structural change between two adjacent loads."""

    target_table: str
    previous_loaded_at: str
    current_loaded_at: str
    recorded_diffs: List[Diff]
    recomputed_diffs: List[Diff]
    matches_record: bool

    @property
    def change_kinds(self) -> List[str]:
        return [diff.kind for diff in self.recomputed_diffs]

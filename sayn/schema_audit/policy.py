"""Disposal policy: which diffs block a load and which are safe evolution.

The *safety boundary itself is fixed* (see :mod:`sayn.schema_audit.diff`):
primary-key changes, narrowing/incompatible type changes and missing
``NOT NULL`` columns without a default are always flagged ``blocking`` on
the diff. This module only decides what the batch does about them:

* ``block`` (default): a blocking diff stops the whole batch, no rows land;
* ``record_only``: every diff is written to the ledger regardless, the
  caller taking responsibility for an unsafe evolution.

Unknown policy values are rejected up front rather than silently treated
as one of the known modes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

from .errors import PolicyError
from .models import Diff


class PolicyMode:
    BLOCK = "block"
    RECORD_ONLY = "record_only"

    ALL = (BLOCK, RECORD_ONLY)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of applying the policy to a table's diffs."""

    mode: str
    safe_to_load: bool
    blocking_diffs: List[Diff] = field(default_factory=list)
    enforced: bool = True

    @property
    def blocking_columns(self) -> List[str]:
        """Concrete columns responsible for blocking the load."""
        columns = []
        for diff in self.blocking_diffs:
            label = diff.column if diff.column is not None else "primary_key"
            if label not in columns:
                columns.append(label)
        return columns


class SafetyPolicy:
    """Applies the configured disposal mode to a list of diffs."""

    def __init__(self, mode: str = PolicyMode.BLOCK) -> None:
        if mode not in PolicyMode.ALL:
            raise PolicyError(
                f"Unknown schema audit policy '{mode}'. "
                f"Allowed values: {', '.join(PolicyMode.ALL)}"
            )
        self.mode = mode

    def evaluate(self, diffs: Sequence[Diff]) -> PolicyDecision:
        blocking = [diff for diff in diffs if diff.blocking]

        if self.mode == PolicyMode.RECORD_ONLY:
            # The boundary is still reported; only the enforcement changes.
            return PolicyDecision(
                mode=self.mode,
                safe_to_load=len(blocking) == 0,
                blocking_diffs=blocking,
                enforced=False,
            )

        return PolicyDecision(
            mode=self.mode,
            safe_to_load=len(blocking) == 0,
            blocking_diffs=blocking,
            enforced=True,
        )

"""Per-table structural diffing between live and incoming schemas."""

from __future__ import annotations


class SchemaDiffer:
    """Produces the classified list of differences for one table."""

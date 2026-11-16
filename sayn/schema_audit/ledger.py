"""Append-only JSON-lines load ledger with replay support."""

from __future__ import annotations


class LoadLedger:
    """Reads and appends structural snapshots, one JSON record per line."""

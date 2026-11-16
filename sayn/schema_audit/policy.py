"""Disposal policy: which diffs block a load and which are safe evolution."""

from __future__ import annotations


class SafetyPolicy:
    """Applies the fixed safety boundary to a list of diffs."""

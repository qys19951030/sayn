"""Standalone command line entry point for schema_audit.

This is deliberately separate from the SAYN CLI: the load pipeline never
imports or calls this module, so projects without a schema_audit
configuration keep following the existing behaviour.
"""

from __future__ import annotations


def main() -> int:
    """Console-script entry point."""
    raise NotImplementedError


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""Smoke tests for the schema_audit package skeleton."""

import importlib

import pytest

MODULES = [
    "sayn.schema_audit",
    "sayn.schema_audit.errors",
    "sayn.schema_audit.models",
    "sayn.schema_audit.types",
    "sayn.schema_audit.diff",
    "sayn.schema_audit.policy",
    "sayn.schema_audit.ledger",
    "sayn.schema_audit.summary",
    "sayn.schema_audit.reader",
    "sayn.schema_audit.runner",
    "sayn.schema_audit.cli",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_modules_import(module_name):
    importlib.import_module(module_name)


def test_public_error_base():
    from sayn.schema_audit import SchemaAuditError
    from sayn.schema_audit.errors import (
        PreconditionError,
        PolicyError,
        LedgerError,
    )

    assert issubclass(PreconditionError, SchemaAuditError)
    assert issubclass(PolicyError, SchemaAuditError)
    assert issubclass(LedgerError, SchemaAuditError)

"""Standalone command line entry point for schema_audit.

Deliberately separate from the SAYN CLI: the load pipeline never imports
this module, so projects without a schema_audit configuration keep the
existing behaviour. The command itself is still read-only with respect to
database data — it reflects structure and writes the local ledger only.

A minimal YAML configuration looks like::

    database:
      type: sqlite
      database: /path/to/warehouse.db
    ledger_path: /path/to/load_ledger.jsonl
    policy: block            # block | record_only
    tables:
      - target_table: orders
        task_name: load_orders
        source_object: staging.orders
        rows_loaded: 0       # known row count, if auditing post-load
        incoming:            # structure of the data about to be loaded
          columns:
            - {name: id, type: INTEGER, nullable: false}
            - {name: note, type: VARCHAR(20), nullable: true}
          primary_key: [id]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from ruamel.yaml import YAML

from .errors import SchemaAuditError
from .models import Column, LoadContext, TableSchema
from .runner import AuditRequest, SchemaAuditor

_POLICY_VALUES = ("block", "record_only")


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise SchemaAuditError(f"Configuration file '{path}' does not exist")
    raw = YAML(typ="safe").load(path.read_text())
    if not isinstance(raw, dict):
        raise SchemaAuditError(
            f"Configuration file '{path}' must contain a YAML mapping"
        )
    return raw


def _build_database(db_config: Dict[str, Any]):
    # Imported lazily so importing schema_audit never pulls the full
    # database layer into unrelated processes.
    from sayn.database.creator import create as create_db

    settings = dict(db_config)
    database = create_db("schema_audit", "schema_audit", settings)
    database._activate_connection()
    return database


def _parse_incoming(table: str, spec: Dict[str, Any]) -> TableSchema:
    columns: List[Column] = []
    for position, raw_column in enumerate(spec.get("columns", [])):
        columns.append(
            Column(
                name=raw_column["name"],
                db_type=raw_column["type"],
                nullable=bool(raw_column.get("nullable", True)),
                default=raw_column.get("default"),
                position=position,
            )
        )
    return TableSchema(
        table=table,
        columns=columns,
        primary_key=list(spec.get("primary_key", [])),
    )


def _build_requests(tables_config: List[Dict[str, Any]]) -> List[AuditRequest]:
    requests: List[AuditRequest] = []
    for spec in tables_config:
        table = spec["target_table"]
        incoming = _parse_incoming(table, spec.get("incoming", {}))
        context = LoadContext(
            task_name=spec.get("task_name", "schema_audit_cli"),
            source_object=spec.get("source_object"),
            rows_loaded=spec.get("rows_loaded"),
            loaded_at=spec.get("loaded_at"),
        )
        requests.append(
            AuditRequest(
                target_table=table,
                context=context,
                incoming=incoming,
                schema=spec.get("schema"),
            )
        )
    return requests


def run_audit(config_path: str) -> int:
    """Programmatic entry used by both the CLI and tests."""
    config = _load_config(Path(config_path))

    policy = config.get("policy", "block")
    if policy not in _POLICY_VALUES:
        raise SchemaAuditError(
            f"Unknown disposal policy '{policy}'. "
            f"Allowed values: {', '.join(_POLICY_VALUES)}"
        )
    if "ledger_path" not in config:
        raise SchemaAuditError("Configuration is missing required 'ledger_path'")
    if "database" not in config:
        raise SchemaAuditError("Configuration is missing required 'database'")

    database = _build_database(config["database"])
    try:
        auditor = SchemaAuditor(database, config["ledger_path"], policy=policy)
        report = auditor.run_batch(_build_requests(config.get("tables", [])))
    finally:
        database.engine.dispose()

    if not report.ok:
        click.echo(f"Precondition failed: {report.precondition_error}", err=True)
        return 2

    click.echo(report.render_summary())
    return 0 if all(result.loaded for result in report.results) else 1


@click.command(name="schema-audit")
@click.argument("config_path", type=click.Path(exists=False))
@click.pass_context
def main(ctx: click.Context, config_path: str):
    """Audit table structures described in CONFIG_PATH YAML."""
    try:
        code = run_audit(config_path)
    except SchemaAuditError as exc:
        click.echo(f"schema-audit error: {exc}", err=True)
        ctx.exit(2)
    ctx.exit(code)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main.main(standalone_mode=False))

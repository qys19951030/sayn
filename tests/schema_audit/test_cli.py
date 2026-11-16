"""Tests for the standalone schema-audit CLI."""

import pytest
from click.testing import CliRunner
from ruamel.yaml import YAML

from sayn.database.creator import create as create_db
from sayn.schema_audit.cli import main, run_audit
from sayn.schema_audit.ledger import LoadLedger


@pytest.fixture
def database(tmp_path):
    db_path = tmp_path / "warehouse.db"
    settings = {"type": "sqlite", "database": str(db_path)}
    db = create_db("test", "test", settings)
    db._activate_connection()
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE orders (id INTEGER NOT NULL PRIMARY KEY, note VARCHAR(20))"
        )
    db.engine.dispose()
    return db_path


def write_config(path, payload):
    with path.open("w") as handle:
        YAML(typ="safe").dump(payload, handle)


def base_config(tmp_path, database, **overrides):
    config = {
        "database": {"type": "sqlite", "database": str(database)},
        "ledger_path": str(tmp_path / "ledger.jsonl"),
        "policy": "block",
        "tables": [
            {
                "target_table": "orders",
                "task_name": "load_orders",
                "source_object": "staging.orders",
                "rows_loaded": 5,
                "incoming": {
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False},
                        {"name": "note", "type": "VARCHAR(20)", "nullable": True},
                    ],
                    "primary_key": ["id"],
                },
            }
        ],
    }
    config.update(overrides)
    return config


def test_cli_clean_run_returns_zero_and_writes_ledger(tmp_path, database):
    config_path = tmp_path / "audit.yaml"
    config = base_config(tmp_path, database)
    write_config(config_path, config)

    result = CliRunner().invoke(main, [str(config_path)], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "Tables audited:            1" in result.output
    assert "orders" in result.output

    entries = LoadLedger(config["ledger_path"]).read_all()
    assert len(entries) == 1
    assert entries[0].task_name == "load_orders"
    assert entries[0].rows_loaded == 5


def test_cli_blocked_run_returns_one(tmp_path, database):
    config_path = tmp_path / "audit.yaml"
    config = base_config(tmp_path, database)
    # Narrowing VARCHAR(20) -> VARCHAR(5) is unsafe.
    config["tables"][0]["incoming"]["columns"][1]["type"] = "VARCHAR(5)"
    write_config(config_path, config)

    result = CliRunner().invoke(main, [str(config_path)], catch_exceptions=False)

    assert result.exit_code == 1
    assert "NOT loaded" in result.output
    assert not tmp_path.joinpath("ledger.jsonl").exists()


def test_cli_missing_config_file(tmp_path):
    result = CliRunner().invoke(main, [str(tmp_path / "nope.yaml")], catch_exceptions=False)
    assert result.exit_code == 2
    assert "does not exist" in result.output


def test_cli_unknown_policy_named(tmp_path, database):
    config_path = tmp_path / "audit.yaml"
    write_config(config_path, base_config(tmp_path, database, policy="bogus"))

    result = CliRunner().invoke(main, [str(config_path)], catch_exceptions=False)
    assert result.exit_code == 2
    assert "Unknown disposal policy" in result.output


def test_cli_missing_source_object_aborts(tmp_path, database):
    config_path = tmp_path / "audit.yaml"
    config = base_config(tmp_path, database)
    config["tables"][0]["target_table"] = "ghost"
    write_config(config_path, config)

    result = CliRunner().invoke(main, [str(config_path)], catch_exceptions=False)
    assert result.exit_code == 2
    assert "Precondition failed" in result.output
    assert "ghost" in result.output


def test_run_audit_missing_ledger_path_key(tmp_path, database):
    config_path = tmp_path / "audit.yaml"
    config = base_config(tmp_path, database)
    del config["ledger_path"]
    write_config(config_path, config)

    with pytest.raises(Exception) as exc:
        run_audit(str(config_path))
    assert "ledger_path" in str(exc.value)

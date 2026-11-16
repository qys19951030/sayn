"""Tests for read-only live schema introspection (sqlite)."""

import pytest
from sqlalchemy import create_engine

from sayn.database.creator import create as create_db
from sayn.schema_audit.errors import PreconditionError
from sayn.schema_audit.reader import SchemaReader


@pytest.fixture
def sqlite_db(tmp_path):
    settings = {"type": "sqlite", "database": str(tmp_path / "audit.db")}
    database = create_db("test", "test", settings)
    database._activate_connection()
    yield database
    database.engine.dispose()


def _create(database, ddl):
    with database.engine.begin() as connection:
        connection.exec_driver_sql(ddl)


def test_connection_check_passes_on_live_db(sqlite_db):
    SchemaReader().check_connection(sqlite_db)


def test_connection_check_fails_with_named_error():
    class DeadDb:
        name = "dead"
        engine = create_engine("sqlite:////nonexistent/dir/dead.db")

    with pytest.raises(PreconditionError) as exc:
        SchemaReader().check_connection(DeadDb())
    assert "dead" in str(exc.value)


def test_missing_object_raises_precondition(sqlite_db):
    reader = SchemaReader()
    assert reader.object_exists(sqlite_db, "nope") is False

    with pytest.raises(PreconditionError) as exc:
        reader.read_schema(sqlite_db, "nope")
    assert "nope" in str(exc.value)


def test_read_schema_reflects_columns_nullability_default_and_pk(sqlite_db):
    _create(
        sqlite_db,
        """
        CREATE TABLE orders (
            id INTEGER NOT NULL PRIMARY KEY,
            ref VARCHAR(20) NOT NULL DEFAULT 'x',
            note TEXT
        )
        """,
    )

    schema = SchemaReader().read_schema(sqlite_db, "orders")

    assert schema.table == "orders"
    assert schema.primary_key == ["id"]
    by_name = {c.name: c for c in schema.columns}
    assert list(by_name) == ["id", "ref", "note"]
    assert by_name["id"].nullable is False
    assert by_name["ref"].nullable is False
    assert by_name["ref"].default is not None
    assert by_name["note"].nullable is True
    assert [c.position for c in schema.columns] == [0, 1, 2]
    assert by_name["ref"].db_type.upper().startswith("VARCHAR(20)")


def test_read_schema_detects_views(sqlite_db):
    _create(sqlite_db, "CREATE TABLE base (id INTEGER)")
    _create(sqlite_db, "CREATE VIEW v_base AS SELECT id FROM base")

    reader = SchemaReader()
    assert reader.object_exists(sqlite_db, "v_base") is True
    schema = reader.read_schema(sqlite_db, "v_base")
    assert schema.column_names == ["id"]


def test_read_schema_qualified_name(sqlite_db):
    _create(sqlite_db, "CREATE TABLE t (a INTEGER)")
    schema = SchemaReader().read_schema(sqlite_db, "t")
    assert schema.table == "t"

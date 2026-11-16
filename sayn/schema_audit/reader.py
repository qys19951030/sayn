"""Read-only introspection of live tables through a SAYN Database object.

The reader only reflects metadata: it never executes DDL, DML or loads.
It uses the public ``engine``/``metadata`` surface of
:class:`sayn.database.Database`, so the database layer itself is untouched.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import MetaData
from sqlalchemy.exc import SQLAlchemyError

from .errors import PreconditionError
from .models import Column, TableSchema


class SchemaReader:
    """Reads live table structure without executing any write."""

    def check_connection(self, database) -> None:
        """Fail early with a named error when the target DB is unreachable."""
        engine = getattr(database, "engine", None)
        if engine is None:
            raise PreconditionError(
                f"Target database '{getattr(database, 'name', '?')}' has no "
                "active engine connection"
            )
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except SQLAlchemyError as exc:
            raise PreconditionError(
                f"Cannot connect to target database "
                f"'{getattr(database, 'name', '?')}': {exc}"
            ) from exc

    def object_exists(
        self,
        database,
        table: str,
        schema: Optional[str] = None,
    ) -> bool:
        """Whether the source table or view is visible to the engine."""
        inspector = self._inspector(database)
        try:
            return table in inspector.get_table_names(schema=schema) or table in (
                inspector.get_view_names(schema=schema)
            )
        except SQLAlchemyError as exc:
            raise PreconditionError(
                f"Cannot introspect object '{self._qualify(schema, table)}' on "
                f"database '{getattr(database, 'name', '?')}': {exc}"
            ) from exc

    def read_schema(
        self,
        database,
        table: str,
        schema: Optional[str] = None,
    ) -> TableSchema:
        """Reflect the live table into a :class:`TableSchema`."""
        if not self.object_exists(database, table, schema):
            raise PreconditionError(
                f"Source object '{self._qualify(schema, table)}' does not exist "
                f"on database '{getattr(database, 'name', '?')}'"
            )

        # Reflect into a private MetaData: reusing the engine's shared MetaData
        # can return a partially reflected table whose column ordering is not
        # the physical DDL order, and this package must not mutate the
        # database layer's own metadata.
        try:
            reflected_table = self._reflect_table(database, table, schema)
        except SQLAlchemyError as exc:
            raise PreconditionError(
                f"Failed to read structure of "
                f"'{self._qualify(schema, table)}': {exc}"
            ) from exc
        pk_columns = [column.name for column in reflected_table.primary_key.columns]

        columns = [
            Column(
                name=column.name,
                db_type=str(column.type),
                nullable=bool(column.nullable),
                default=self._render_default(
                    column.server_default.arg.text
                    if column.server_default is not None
                    else None
                ),
                position=position,
            )
            for position, column in enumerate(reflected_table.columns)
        ]
        return TableSchema(
            table=self._qualify(schema, table),
            columns=columns,
            primary_key=pk_columns,
        )

    @staticmethod
    def _reflect_table(database, table, schema):
        from sqlalchemy import Table

        metadata = MetaData()
        return Table(
            table,
            metadata,
            schema=schema,
            autoload=True,
            autoload_with=database.engine,
        )

    @staticmethod
    def _inspector(database):
        from sqlalchemy import inspect

        return inspect(database.engine)

    @staticmethod
    def _render_default(default) -> Optional[str]:
        if default is None:
            return None
        return str(default)

    @staticmethod
    def _qualify(schema: Optional[str], table: str) -> str:
        return f"{schema}.{table}" if schema else table

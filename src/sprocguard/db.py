"""Thin database layer over pyodbc or pymssql.

All SQL in sprocguard is written with ``?`` placeholders; the layer converts
them for drivers that use a different parameter style. The connection runs in
autocommit mode on the driver side and sprocguard manages transactions itself
(see ``capture.py``), so the same safety logic works with both drivers.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import os
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .config import Config, ConfigError


class SafetyError(Exception):
    """Raised when an operation is refused for safety reasons."""


@dataclass
class ResultSet:
    columns: list[str]
    rows: list[list[Any]]


class Database(Protocol):
    """What the rest of sprocguard needs from a database connection."""

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: Iterable[Any] = ()) -> list[ResultSet]: ...

    def close(self) -> None: ...


def normalize_value(value: Any) -> Any:
    """Convert a driver value into a JSON-friendly, comparable value."""
    if value is None or isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex().upper()
    if isinstance(value, uuid.UUID):
        return str(value).upper()
    return str(value)


class DriverDatabase:
    """A ``Database`` backed by a real DB-API connection."""

    def __init__(self, connection: Any, paramstyle: str, error_types: tuple[type[BaseException], ...]):
        self._conn = connection
        self._paramstyle = paramstyle
        self.error_types = error_types

    def _sql(self, sql: str) -> str:
        if self._paramstyle == "qmark":
            return sql
        # pymssql uses %s; escape literal percent signs first.
        return sql.replace("%", "%%").replace("?", "%s")

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        sets = self.execute(sql, params)
        if not sets:
            return []
        first = sets[0]
        return [dict(zip(first.columns, row)) for row in first.rows]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> list[ResultSet]:
        cursor = self._conn.cursor()
        try:
            params = tuple(params)
            if params:
                cursor.execute(self._sql(sql), params)
            else:
                cursor.execute(sql)
            results: list[ResultSet] = []
            while True:
                if cursor.description:
                    columns = [col[0] for col in cursor.description]
                    rows = [[normalize_value(v) for v in row] for row in cursor.fetchall()]
                    results.append(ResultSet(columns=columns, rows=rows))
                if not cursor.nextset():
                    break
            return results
        finally:
            cursor.close()

    def close(self) -> None:
        self._conn.close()


def connect(config: Config) -> DriverDatabase:
    """Open a connection and check that it points at an allowed database."""
    conn_cfg = config.connection
    if conn_cfg.driver == "pyodbc":
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - depends on the machine
            raise ConfigError(
                "pyodbc could not be loaded. Install the 'ODBC Driver 18 for SQL Server' "
                "or use pymssql (see README)."
            ) from exc
        raw = pyodbc.connect(conn_cfg.odbc, autocommit=True)
        db = DriverDatabase(raw, "qmark", (pyodbc.Error,))
    else:
        try:
            import pymssql
        except ImportError as exc:  # pragma: no cover
            raise ConfigError('pymssql is not installed: pip install "sprocguard[pymssql]"') from exc
        password = os.environ.get(conn_cfg.password_env)
        if password is None:
            raise ConfigError(f"Set the {conn_cfg.password_env} environment variable with the password.")
        raw = pymssql.connect(
            server=conn_cfg.server,
            port=conn_cfg.port,
            user=conn_cfg.user,
            password=password,
            database=conn_cfg.database,
            autocommit=True,
        )
        db = DriverDatabase(raw, "pyformat", (pymssql.Error,))

    check_allowed_database(db, config.safety.allowed_databases)
    return db


def check_allowed_database(db: Database, allowed: list[str]) -> str:
    current = db.query("SELECT DB_NAME() AS name")[0]["name"]
    if current.lower() not in {name.lower() for name in allowed}:
        db.close()
        raise SafetyError(
            f"Connected to database '{current}', which is not in [safety].allowed_databases "
            f"({', '.join(allowed)}). sprocguard only runs against development databases you list."
        )
    return current

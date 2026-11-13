"""Configuration loading for sprocguard.

Configuration lives in a ``sprocguard.toml`` file in the project folder (or the
path given in the ``SPROCGUARD_CONFIG`` environment variable). Secrets are never
stored in the file: passwords are read from an environment variable.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "sprocguard.toml"


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass
class ConnectionConfig:
    # Option A: an ODBC connection string (works with Windows authentication).
    odbc: str | None = None
    # Option B: pymssql parameters (SQL authentication).
    server: str | None = None
    port: int = 1433
    user: str | None = None
    password_env: str = "SPROCGUARD_PASSWORD"
    database: str | None = None

    @property
    def driver(self) -> str:
        return "pyodbc" if self.odbc else "pymssql"


@dataclass
class SafetyConfig:
    # sprocguard refuses to run against any database that is not listed here.
    allowed_databases: list[str] = field(default_factory=list)
    # Tables bigger than this are not snapshotted (their changes are not compared).
    max_rows_per_table: int = 5000
    # Procedures with external side effects (e-mail, xp_cmdshell, linked servers...)
    # are refused unless this is true, because a rollback cannot undo them.
    allow_external_side_effects: bool = False
    # Seconds to wait for a lock before giving up.
    lock_timeout_seconds: int = 10


@dataclass
class StorageConfig:
    baseline_dir: str = ".sprocguard/baselines"
    tsqlt_dir: str = ".sprocguard/tsqlt"


@dataclass
class Config:
    connection: ConnectionConfig
    safety: SafetyConfig
    storage: StorageConfig
    root: Path

    @property
    def baseline_path(self) -> Path:
        return (self.root / self.storage.baseline_dir).resolve()

    @property
    def tsqlt_path(self) -> Path:
        return (self.root / self.storage.tsqlt_dir).resolve()


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table in {DEFAULT_CONFIG_NAME}")
    return value


def _build(cls, values: dict, section: str):
    allowed = set(cls.__dataclass_fields__)
    unknown = set(values) - allowed
    if unknown:
        raise ConfigError(f"Unknown keys in [{section}]: {', '.join(sorted(unknown))}")
    return cls(**values)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load configuration from ``path``, ``$SPROCGUARD_CONFIG`` or ``./sprocguard.toml``."""
    candidate = path or os.environ.get("SPROCGUARD_CONFIG") or DEFAULT_CONFIG_NAME
    config_file = Path(candidate).expanduser()
    if not config_file.is_file():
        raise ConfigError(
            f"Configuration file not found: {config_file.resolve()}. "
            f"Run 'sprocguard init' to create one."
        )
    with config_file.open("rb") as fh:
        data = tomllib.load(fh)

    connection = _build(ConnectionConfig, _section(data, "connection"), "connection")
    # An environment variable can override the ODBC string (handy for CI).
    if os.environ.get("SPROCGUARD_ODBC"):
        connection.odbc = os.environ["SPROCGUARD_ODBC"]
    safety = _build(SafetyConfig, _section(data, "safety"), "safety")
    storage = _build(StorageConfig, _section(data, "storage"), "storage")

    if not connection.odbc and not connection.server:
        raise ConfigError("Set either [connection].odbc or [connection].server.")
    if not safety.allowed_databases:
        raise ConfigError(
            "[safety].allowed_databases is empty. List the development databases "
            "sprocguard may use; it refuses to run anywhere else."
        )
    return Config(connection=connection, safety=safety, storage=storage, root=config_file.parent.resolve())


SAMPLE_CONFIG = """\
# sprocguard configuration. Point it ONLY at a development database.

[connection]
# Option A (recommended on Windows): an ODBC connection string.
# Use "ODBC Driver 17 for SQL Server" if that is the driver installed on your machine.
odbc = "Driver={ODBC Driver 18 for SQL Server};Server=localhost;Database=DemoShop;Trusted_Connection=yes;TrustServerCertificate=yes"

# Option B: SQL authentication through pymssql (pip install "sprocguard[pymssql]").
# The password is read from the environment variable named in password_env.
# server = "localhost"
# port = 1433
# user = "sa"
# password_env = "SPROCGUARD_PASSWORD"
# database = "DemoShop"

[safety]
# sprocguard refuses to connect to any database not in this list.
allowed_databases = ["DemoShop"]
max_rows_per_table = 5000
allow_external_side_effects = false
lock_timeout_seconds = 10

[storage]
# Commit these folders to git: they are your safety net.
baseline_dir = ".sprocguard/baselines"
tsqlt_dir = ".sprocguard/tsqlt"
"""

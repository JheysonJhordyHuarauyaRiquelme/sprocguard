"""MCP server exposing sprocguard to AI coding agents."""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, service
from .analysis import InspectionError
from .capture import CaptureError, clean_error
from .config import ConfigError
from .db import SafetyError

F = TypeVar("F", bound=Callable[..., Any])

INSTRUCTIONS = """\
sprocguard is a safety net for SQL Server stored procedures.
Before you change a stored procedure: inspect_procedure -> suggest_cases -> capture_baseline.
After you deploy your change to the development database: verify_procedure.
Every execution runs inside a transaction that is rolled back, and only against the
development databases listed in sprocguard.toml. Never edit a baseline to make verify pass:
if a difference is intended, explain it to the user and recapture with overwrite=true only
after they agree.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITES_FILE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def explain_errors(func: F) -> F:
    """Pass the real reason for a failure to the agent (MCP hides unexpected exceptions)."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (ConfigError, SafetyError, CaptureError, InspectionError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            module = type(exc).__module__.split(".")[0]
            if module in ("pyodbc", "pymssql", "_mssql"):  # connection or SQL errors from the driver
                raise ToolError(f"SQL Server error: {clean_error(exc)}") from exc
            raise

    return wrapper  # type: ignore[return-value]


def build_server(config_path: str | None = None) -> MCPServer:
    mcp = MCPServer(name="sprocguard", version=__version__, instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    @explain_errors
    def inspect_procedure(procedure: str, include_definition: bool = False) -> dict[str, Any]:
        """Describe a stored procedure: parameters, tables it reads and writes, procedures and
        triggers involved, and risks (external side effects, dynamic SQL, nondeterminism).
        Call this first. `procedure` is a name like 'dbo.usp_PlaceOrder'."""
        return service.inspect(procedure, include_definition, config_path)

    @mcp.tool(annotations=READ_ONLY)
    @explain_errors
    def suggest_cases(procedure: str) -> dict[str, Any]:
        """Suggest parameter values for test cases: real values found in related tables plus
        edge values (0, -1, empty, NULL). Use them to design cases that cover every branch."""
        return service.suggest(procedure, config_path)

    @mcp.tool(annotations=WRITES_FILE)
    @explain_errors
    def capture_baseline(procedure: str, cases: list[dict[str, Any]], watch_tables: list[str] | None = None,
                         overwrite: bool = False) -> dict[str, Any]:
        """Record what the procedure does TODAY, before you change it.
        `cases` is a list like [{"name": "customer with orders", "params": {"@CustomerId": 1}}].
        Each case runs twice inside a rolled-back transaction; result sets, return value, OUTPUT
        parameters, errors and table changes are saved to a baseline file. Values that change by
        themselves (dates, new IDs) are detected and ignored later. `watch_tables` adds tables to
        compare (useful with dynamic SQL). Refuses to replace an existing baseline unless overwrite=true."""
        return service.capture_baseline(procedure, cases, watch_tables, overwrite, config_path)

    @mcp.tool(annotations=READ_ONLY)
    @explain_errors
    def verify_procedure(procedure: str) -> dict[str, Any]:
        """Re-run the baseline cases against the procedure as it is now in the database and list
        every difference in behavior. Run it after deploying your change to the development database.
        An empty difference list for every case means the behavior is unchanged."""
        return service.verify(procedure, config_path)

    @mcp.tool(annotations=WRITES_FILE)
    @explain_errors
    def generate_tsqlt_tests(procedure: str) -> dict[str, Any]:
        """Turn the baseline into tSQLt tests (a .sql file) so the safety net can run in CI.
        Version 0.1 supports read-only procedures."""
        return service.generate_tsqlt(procedure, config_path)

    @mcp.tool(annotations=READ_ONLY)
    @explain_errors
    def list_baselines() -> list[dict[str, Any]]:
        """List the procedures that already have a baseline."""
        return service.list_baselines(config_path)

    return mcp


def run(config_path: str | None = None) -> None:
    build_server(config_path).run("stdio")

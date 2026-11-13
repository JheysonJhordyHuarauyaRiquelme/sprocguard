"""Run procedures safely, capture baselines and verify them.

Safety model
------------
Every execution happens inside a transaction that is always rolled back:

1. ``SET IMPLICIT_TRANSACTIONS ON`` so that even if the procedure ends the
   transaction on its own (``ROLLBACK``), any later change opens a new one.
2. Two nested ``BEGIN TRANSACTION`` so a stray ``COMMIT`` inside the procedure
   only lowers the nesting level instead of committing.
3. ``IF @@TRANCOUNT > 0 ROLLBACK`` at the end, in a ``finally`` block.

Changes in tables are measured by snapshotting the tables the procedure writes
before and after the call, inside that same transaction.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import ProcedureInfo, inspect_procedure, quote_name
from .compare import compare_run, diff_table, learn_volatility
from .config import Config
from .db import Database, ResultSet, SafetyError

RETURN_COLUMN = "__sprocguard_return_value"
CLOCK_SQL = "SELECT SYSDATETIME() AS l, SYSUTCDATETIME() AS u"


class CaptureError(Exception):
    pass


@dataclass
class Case:
    name: str
    params: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> Case:
        if not isinstance(data, dict):
            raise CaptureError(f"Case #{index + 1} must be an object with 'name' and 'params'.")
        params = data.get("params", {}) or {}
        if not isinstance(params, dict):
            raise CaptureError(f"Case #{index + 1}: 'params' must be an object.")
        return cls(name=str(data.get("name") or f"case {index + 1}"), params=params)


# --------------------------------------------------------------------------------------
# Building the EXEC batch
# --------------------------------------------------------------------------------------


def _canonical_params(info: ProcedureInfo, params: dict[str, Any]) -> dict[str, Any]:
    by_lower = {p.name.lower(): p.name for p in info.parameters}
    result: dict[str, Any] = {}
    for raw, value in params.items():
        name = raw if raw.startswith("@") else "@" + raw
        canonical = by_lower.get(name.lower())
        if canonical is None:
            known = ", ".join(p.name for p in info.parameters) or "(none)"
            raise CaptureError(f"{info.full_name} has no parameter {name}. Parameters: {known}")
        result[canonical] = value
    return result


def build_exec_batch(info: ProcedureInfo, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build the batch that executes the procedure and returns its return value and OUTPUT parameters."""
    params = _canonical_params(info, params)
    lines = ["SET NOCOUNT ON;", "DECLARE @__sg_rc int;"]
    values: list[Any] = []
    args: list[str] = []
    outputs: list[tuple[str, str]] = []
    for i, p in enumerate(info.parameters):
        if p.is_output:
            var = f"@__sg_out{i}"
            if p.name in params:
                lines.append(f"DECLARE {var} {p.type} = ?;")
                values.append(params[p.name])
            else:
                lines.append(f"DECLARE {var} {p.type};")
            args.append(f"{p.name} = {var} OUTPUT")
            outputs.append((p.name, var))
        elif p.name in params:
            args.append(f"{p.name} = ?")
            values.append(params[p.name])
    call = f"EXEC @__sg_rc = {info.quoted_name}"
    if args:
        call += " " + ", ".join(args)
    lines.append(call + ";")
    select = [f"@__sg_rc AS {quote_name(RETURN_COLUMN)}"] + [
        f"{var} AS {quote_name(name)}" for name, var in outputs
    ]
    lines.append("SELECT " + ", ".join(select) + ";")
    return "\n".join(lines), values


# --------------------------------------------------------------------------------------
# Running one case
# --------------------------------------------------------------------------------------


def clean_error(exc: BaseException) -> str:
    """Turn a driver exception into the SQL Server message only."""
    args = tuple(getattr(exc, "args", ()))
    if args and isinstance(args[0], tuple):  # some drivers wrap (number, message) in a tuple
        args = args[0]
    if len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], (bytes, str)):
        # pymssql: (number, b'message ...DB-Lib error message ...')
        message = args[1].decode("utf-8", "replace") if isinstance(args[1], bytes) else args[1]
        text = message
    else:
        parts = [a for a in args if isinstance(a, str)]
        text = max(parts, key=len) if parts else str(exc)
    text = re.sub(r"(\[[^\]]*\])+", "", text)
    text = re.sub(r"\s*\(\d+\)\s*\(SQL\w+\)\s*$", "", text)
    text = re.sub(r"DB-Lib error message \d+, severity \d+:.*", "", text, flags=re.DOTALL)
    return " ".join(text.split())


def _primary_key(db: Database, table: str) -> list[str]:
    rows = db.query(
        "SELECT c.name FROM sys.indexes i "
        "JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
        "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
        "WHERE i.object_id = OBJECT_ID(?) AND i.is_primary_key = 1 ORDER BY ic.key_ordinal",
        (table,),
    )
    return [r["name"] for r in rows]


def _snapshot(db: Database, table: str) -> list[dict[str, Any]]:
    schema, name = table.split(".", 1)
    return db.query(f"SELECT * FROM {quote_name(schema, name)}")


def plan_tables(
    db: Database, tables: list[str], max_rows: int
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Decide which tables can be snapshotted. Returns (table -> key columns, table -> skip reason)."""
    usable: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}
    for table in tables:
        schema, name = table.split(".", 1)
        count = db.query(f"SELECT COUNT_BIG(*) AS n FROM {quote_name(schema, name)}")[0]["n"]
        if count > max_rows:
            skipped[table] = (
                f"{count} rows is more than [safety].max_rows_per_table ({max_rows}); "
                f"changes in this table are not compared."
            )
        else:
            usable[table] = _primary_key(db, table)
    return usable, skipped


def run_case(
    db: Database,
    info: ProcedureInfo,
    params: dict[str, Any],
    tables: dict[str, list[str]],
    skipped: dict[str, str],
    config: Config,
) -> dict[str, Any]:
    """Execute one case inside a transaction that is always rolled back."""
    batch, values = build_exec_batch(info, params)
    timeout_ms = int(config.safety.lock_timeout_seconds * 1000)
    db.execute(
        f"SET IMPLICIT_TRANSACTIONS ON; SET LOCK_TIMEOUT {timeout_ms}; "
        "BEGIN TRANSACTION; BEGIN TRANSACTION;"
    )
    try:
        level = db.query("SELECT @@TRANCOUNT AS n")[0]["n"]
        if level < 2:
            raise SafetyError("Could not open the protective transaction; nothing was executed.")
        before = {t: _snapshot(db, t) for t in tables}
        clock_start = db.query(CLOCK_SQL)[0]
        error = None
        sets: list[ResultSet] = []
        try:
            sets = db.execute(batch, values)
        except Exception as exc:  # the procedure's own error is part of its behavior
            if not _is_db_error(db, exc):
                raise
            error = clean_error(exc)
        clock_end = db.query(CLOCK_SQL)[0]
        after = {t: _snapshot(db, t) for t in tables}
    finally:
        db.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION; SET IMPLICIT_TRANSACTIONS OFF;")

    return_value = None
    outputs: dict[str, Any] = {}
    if sets and sets[-1].columns and sets[-1].columns[0] == RETURN_COLUMN:
        last = sets.pop()
        row = last.rows[0] if last.rows else [None] * len(last.columns)
        return_value = row[0]
        outputs = dict(zip(last.columns[1:], row[1:], strict=True))

    run: dict[str, Any] = {
        "error": error,
        "return_value": return_value,
        "outputs": outputs,
        "result_sets": [{"columns": s.columns, "rows": s.rows} for s in sets],
        "tables": {t: diff_table(before[t], after[t], key) for t, key in tables.items()},
        # Server time while the procedure ran, used to spot columns that hold "now".
        "clock": {
            "local": [clock_start["l"], clock_end["l"]],
            "utc": [clock_start["u"], clock_end["u"]],
        },
    }
    for table, reason in skipped.items():
        run["tables"][table] = {"skipped": reason}
    return run


def _is_db_error(db: Database, exc: BaseException) -> bool:
    error_types = getattr(db, "error_types", (Exception,))
    return isinstance(exc, error_types)


# --------------------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------------------


def baseline_file(config: Config, info: ProcedureInfo) -> Path:
    return config.baseline_path / f"{info.schema}.{info.name}.json"


def _check_runnable(info: ProcedureInfo, config: Config) -> None:
    blocked = [
        r
        for r in info.blocked
        if not (
            r.code in ("external_side_effect", "linked_server")
            and config.safety.allow_external_side_effects
        )
    ]
    if blocked:
        reasons = " ".join(
            f"[{r.code}] {r.message} Evidence: {', '.join(r.evidence)}." for r in blocked
        )
        raise SafetyError(f"Refusing to run {info.full_name}. {reasons}")


def _current_database(db: Database) -> str:
    return db.query("SELECT DB_NAME() AS name")[0]["name"]


def capture_baseline(
    db: Database,
    config: Config,
    procedure: str,
    cases: list[dict[str, Any]],
    watch_tables: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run each case twice, learn what is volatile, and save the expected behavior to a baseline file."""
    if not cases:
        raise CaptureError("Provide at least one case.")
    info = inspect_procedure(db, procedure)
    _check_runnable(info, config)
    path = baseline_file(config, info)
    if path.exists() and not overwrite:
        raise CaptureError(
            f"A baseline already exists at {path}. Pass overwrite=true only if the current "
            f"behavior is the one you want to keep."
        )
    parsed = [Case.from_dict(c, i) for i, c in enumerate(cases)]
    tables_wanted = sorted(set(info.writes) | {_normalize_table(t) for t in (watch_tables or [])})
    tables, skipped = plan_tables(db, tables_wanted, config.safety.max_rows_per_table)

    out_cases: list[dict[str, Any]] = []
    warnings: list[str] = []
    for case in parsed:
        run1 = run_case(db, info, case.params, tables, skipped, config)
        run2 = run_case(db, info, case.params, tables, skipped, config)
        learned = learn_volatility(run1, run2)
        for problem in learned["problems"]:
            warnings.append(f"Case '{case.name}': {problem}")
        out_cases.append(
            {
                "name": case.name,
                "params": _canonical_params(info, case.params),
                "expected": run1,
                "learned": learned,
            }
        )

    baseline = {
        "tool": "sprocguard",
        "tool_version": __version__,
        "format_version": 1,
        "procedure": info.full_name,
        "database": _current_database(db),
        "captured_at": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
        "definition_hash": info.definition_hash,
        "watched_tables": sorted(tables),
        "skipped_tables": skipped,
        "risks": [{"code": r.code, "severity": r.severity} for r in info.risks],
        "cases": out_cases,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    return {
        "procedure": info.full_name,
        "baseline_file": str(path),
        "cases": len(out_cases),
        "watched_tables": sorted(tables),
        "skipped_tables": skipped,
        "volatile": {
            c["name"]: _volatile_summary(c["learned"])
            for c in out_cases
            if _volatile_summary(c["learned"])
        },
        "warnings": warnings
        + [f"[{r.code}] {r.message}" for r in info.risks if r.severity == "warn"],
    }


def _normalize_table(name: str) -> str:
    parts = [p.strip("[]") for p in name.split(".")]
    return ".".join(parts if len(parts) == 2 else ["dbo", parts[-1]])


def _volatile_summary(learned: dict[str, Any]) -> list[str]:
    items = list(learned.get("volatile_scalars", []))
    for i, meta in enumerate(learned.get("result_sets", [])):
        items += [f"result set #{i + 1}.{c}" for c in meta.get("volatile_columns", [])]
    for table, cols in learned.get("volatile_table_columns", {}).items():
        items += [f"{table}.{c}" for c in cols]
    return items


def load_baseline(config: Config, info: ProcedureInfo) -> dict[str, Any]:
    path = baseline_file(config, info)
    if not path.exists():
        raise CaptureError(
            f"No baseline for {info.full_name}. Capture one first (before changing the procedure)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def verify(db: Database, config: Config, procedure: str) -> dict[str, Any]:
    """Re-run the baseline cases against the current procedure and report any change in behavior."""
    info = inspect_procedure(db, procedure)
    _check_runnable(info, config)
    baseline = load_baseline(config, info)
    tables_wanted = sorted(set(baseline.get("watched_tables", [])) | set(info.writes))
    tables, skipped = plan_tables(db, tables_wanted, config.safety.max_rows_per_table)
    new_tables = sorted(set(tables) - set(baseline.get("watched_tables", [])))

    results = []
    for case in baseline["cases"]:
        try:
            actual = run_case(db, info, case["params"], tables, skipped, config)
            differences = compare_run(case["expected"], case.get("learned", {}), actual)
        except CaptureError as exc:
            differences = [f"Could not run this case anymore: {exc}"]
        results.append(
            {
                "name": case["name"],
                "params": case["params"],
                "passed": not differences,
                "differences": differences,
            }
        )

    passed = sum(1 for r in results if r["passed"])
    return {
        "procedure": info.full_name,
        "passed": passed == len(results),
        "summary": f"{passed}/{len(results)} case(s) behave exactly as in the baseline.",
        "definition_changed": info.definition_hash != baseline.get("definition_hash"),
        "baseline_captured_at": baseline.get("captured_at"),
        "newly_written_tables": new_tables,
        "cases": results,
    }

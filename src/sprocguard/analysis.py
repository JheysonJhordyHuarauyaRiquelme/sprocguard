"""Inspect a stored procedure: parameters, tables it reads and writes, and risks.

The inspection combines SQL Server metadata (``sys.dm_sql_referenced_entities``)
with a light static analysis of the procedure text. Neither is perfect on its
own, so sprocguard uses both and tells the user where the picture may be
incomplete (dynamic SQL, for example).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .db import Database

# --------------------------------------------------------------------------------------
# Names and types
# --------------------------------------------------------------------------------------


def quote_name(*parts: str) -> str:
    """Return a safely bracketed, dot-separated SQL Server name."""
    return ".".join("[" + part.replace("]", "]]") + "]" for part in parts)


def type_decl(type_name: str, max_length: int, precision: int, scale: int) -> str:
    """Build a T-SQL type declaration (e.g. ``nvarchar(50)``) from catalog values."""
    t = type_name.lower()
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t}(max)" if max_length == -1 else f"{t}({max_length})"
    if t in ("nvarchar", "nchar"):
        return f"{t}(max)" if max_length == -1 else f"{t}({max_length // 2})"
    if t in ("decimal", "numeric"):
        return f"{t}({precision},{scale})"
    if t in ("datetime2", "time", "datetimeoffset"):
        return f"{t}({scale})"
    return t


# --------------------------------------------------------------------------------------
# Static analysis
# --------------------------------------------------------------------------------------


def strip_comments_and_strings(sql: str) -> str:
    """Remove comments and blank out string literals so patterns don't match inside them."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
        elif ch == "/" and nxt == "*":
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
        elif ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                elif sql[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
            out.append("''")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


@dataclass
class Risk:
    code: str
    severity: str  # "block" | "warn" | "info"
    message: str
    evidence: list[str] = field(default_factory=list)


_RISK_RULES: list[tuple[str, str, str, str]] = [
    # (code, severity, regex, message)
    (
        "external_side_effect",
        "block",
        r"\b(xp_cmdshell|sp_send_dbmail|sp_OA\w+|sp_start_job|OPENQUERY|OPENROWSET|OPENDATASOURCE|BULK\s+INSERT)\b"
        r"|\bEXEC(UTE)?\s*\(.*?\)\s*AT\s+\w+",
        "The procedure can affect things outside the database (e-mail, files, other servers, jobs). "
        "A rollback cannot undo that, so sprocguard will not run it unless "
        "[safety].allow_external_side_effects = true.",
    ),
    (
        "linked_server",
        "block",
        r"(?<![\w\].])(\[?\w+\]?\.){3}\[?\w+\]?(?![\w.])",
        "Four-part names point to a linked server. Changes there may not be rolled back.",
    ),
    (
        "dynamic_sql",
        "warn",
        r"\bsp_executesql\b|\bEXEC(UTE)?\s*\(\s*(@|N?'')",
        "Dynamic SQL: sprocguard cannot see which tables it writes. Data changes are still rolled back, "
        "but changes to tables it did not detect will not be compared. Use watch_tables to add them.",
    ),
    (
        "inner_rollback",
        "warn",
        r"\bROLLBACK(\s+(TRAN|TRANSACTION|WORK))?\b",
        "The procedure rolls back on its own. sprocguard still undoes all changes, but SQL Server may "
        "report error 266 (transaction count mismatch) during capture.",
    ),
    (
        "waitfor",
        "warn",
        r"\bWAITFOR\b",
        "WAITFOR makes captures slow and can hold locks.",
    ),
    (
        "ddl",
        "warn",
        r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|VIEW|PROC|PROCEDURE|FUNCTION|TRIGGER|SCHEMA)\s+(?!#)",
        "The procedure changes database structure (DDL). It is rolled back, but behavior may depend on it.",
    ),
    (
        "nondeterministic",
        "info",
        r"\b(GETDATE|GETUTCDATE|SYSDATETIME|SYSUTCDATETIME|SYSDATETIMEOFFSET|NEWID|NEWSEQUENTIALID|RAND|CRYPT_GEN_RANDOM)\s*\("
        r"|\bCURRENT_TIMESTAMP\b",
        "Uses values that change on every run (dates, random values). sprocguard runs each case twice and "
        "ignores the columns that change by themselves.",
    ),
    (
        "identity",
        "info",
        r"\bSCOPE_IDENTITY\s*\(|@@IDENTITY\b|\bIDENT_CURRENT\s*\(|\bOUTPUT\s+inserted\.",
        "Returns generated IDs. IDENTITY values are not reused after a rollback, so they are treated "
        "as volatile.",
    ),
    (
        "error_paths",
        "info",
        r"\b(THROW|RAISERROR)\b",
        "Has error paths. Errors are behavior too: add cases that trigger them.",
    ),
]


_OWN_HEADER = re.compile(r"^\s*(CREATE|ALTER)\s+(OR\s+ALTER\s+)?PROC(EDURE)?\b", re.IGNORECASE)


def analyze_risks(definition: str) -> list[Risk]:
    # The procedure's own "CREATE PROCEDURE" header is not DDL executed by the procedure.
    text = _OWN_HEADER.sub(" ", strip_comments_and_strings(definition), count=1)
    risks: list[Risk] = []
    for code, severity, pattern, message in _RISK_RULES:
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL))
        if matches:
            evidence = sorted({" ".join(m.group(0).split())[:60] for m in matches})[:5]
            risks.append(Risk(code=code, severity=severity, message=message, evidence=evidence))
    return risks


def count_branches(definition: str) -> int:
    """Rough number of decision points, used to suggest how many cases to capture."""
    text = strip_comments_and_strings(definition)
    return len(re.findall(r"\b(IF|CASE|WHILE|THROW|RAISERROR)\b", text, flags=re.IGNORECASE))


_WRITE_TARGET = re.compile(
    r"\b(?:INSERT\s+(?:INTO\s+)?|UPDATE\s+|DELETE\s+(?:FROM\s+)?|MERGE\s+(?:INTO\s+)?|TRUNCATE\s+TABLE\s+)"
    r"((?:\[[^\]]+\]|\w+)(?:\s*\.\s*(?:\[[^\]]+\]|\w+))?)",
    flags=re.IGNORECASE,
)


def write_targets_from_text(definition: str) -> list[str]:
    """Candidate table names written by INSERT/UPDATE/DELETE/MERGE/TRUNCATE."""
    text = strip_comments_and_strings(definition)
    found = []
    for match in _WRITE_TARGET.finditer(text):
        name = re.sub(r"\s+", "", match.group(1))
        if name.startswith(("#", "@")) or name.upper() in {"TOP", "STATISTICS"}:
            continue
        found.append(name)
    return sorted(set(found))


# --------------------------------------------------------------------------------------
# Catalog queries
# --------------------------------------------------------------------------------------


@dataclass
class Parameter:
    name: str
    type: str
    is_output: bool
    is_table_type: bool


@dataclass
class ResultColumn:
    name: str
    type: str
    nullable: bool


@dataclass
class ProcedureInfo:
    schema: str
    name: str
    object_id: int
    definition: str
    definition_hash: str
    parameters: list[Parameter]
    reads: list[str]
    writes: list[str]
    calls: list[str]
    triggers: list[str]
    first_result_set: list[ResultColumn] | None
    risks: list[Risk]
    suggested_case_count: int
    notes: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def quoted_name(self) -> str:
        return quote_name(self.schema, self.name)

    @property
    def blocked(self) -> list[Risk]:
        return [r for r in self.risks if r.severity == "block"]

    def to_dict(self, include_definition: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["full_name"] = self.full_name
        if not include_definition:
            data.pop("definition")
        return data


class InspectionError(Exception):
    pass


def resolve_procedure(db: Database, name: str) -> tuple[int, str, str]:
    rows = db.query(
        "SELECT o.object_id, s.name AS schema_name, o.name AS name, o.type AS type "
        "FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id "
        "WHERE o.object_id = OBJECT_ID(?)",
        (name,),
    )
    if not rows:
        raise InspectionError(f"Stored procedure '{name}' was not found in the current database.")
    row = rows[0]
    if row["type"].strip() != "P":
        raise InspectionError(
            f"'{name}' is not a T-SQL stored procedure (type {row['type'].strip()})."
        )
    return row["object_id"], row["schema_name"], row["name"]


def _parameters(db: Database, object_id: int) -> list[Parameter]:
    rows = db.query(
        "SELECT p.name, TYPE_NAME(p.system_type_id) AS system_type, TYPE_NAME(p.user_type_id) AS user_type, "
        "p.max_length, p.precision, p.scale, p.is_output, t.is_table_type "
        "FROM sys.parameters p JOIN sys.types t ON t.user_type_id = p.user_type_id "
        "WHERE p.object_id = ? AND p.parameter_id > 0 ORDER BY p.parameter_id",
        (object_id,),
    )
    params = []
    for r in rows:
        is_table = bool(r["is_table_type"])
        declared = (
            r["user_type"]
            if is_table
            else type_decl(r["system_type"], r["max_length"], r["precision"], r["scale"])
        )
        params.append(
            Parameter(
                name=r["name"],
                type=declared,
                is_output=bool(r["is_output"]),
                is_table_type=is_table,
            )
        )
    return params


def _references(db: Database, qualified: str) -> list[dict[str, Any]]:
    return db.query(
        "SELECT COALESCE(OBJECT_SCHEMA_NAME(r.referenced_id), r.referenced_schema_name, 'dbo') AS schema_name, "
        "r.referenced_entity_name AS name, o.type AS type, "
        "MAX(CAST(r.is_selected AS int)) AS is_selected, MAX(CAST(r.is_updated AS int)) AS is_updated "
        "FROM sys.dm_sql_referenced_entities(?, 'OBJECT') r "
        "LEFT JOIN sys.objects o ON o.object_id = r.referenced_id "
        "WHERE r.referenced_class = 1 "
        "GROUP BY COALESCE(OBJECT_SCHEMA_NAME(r.referenced_id), r.referenced_schema_name, 'dbo'), "
        "r.referenced_entity_name, o.type",
        (qualified,),
    )


def _resolve_table(db: Database, candidate: str) -> str | None:
    rows = db.query(
        "SELECT OBJECT_SCHEMA_NAME(OBJECT_ID(?, 'U')) AS schema_name, OBJECT_NAME(OBJECT_ID(?, 'U')) AS name",
        (candidate, candidate),
    )
    if rows and rows[0]["name"]:
        return f"{rows[0]['schema_name']}.{rows[0]['name']}"
    return None


def _triggers(db: Database, table: str) -> list[str]:
    rows = db.query(
        "SELECT OBJECT_SCHEMA_NAME(t.object_id) AS schema_name, t.name "
        "FROM sys.triggers t WHERE t.parent_id = OBJECT_ID(?) AND t.is_disabled = 0",
        (table,),
    )
    return [f"{r['schema_name']}.{r['name']}" for r in rows]


def _first_result_set(db: Database, object_id: int) -> list[ResultColumn] | None:
    rows = db.query(
        "SELECT name, system_type_name, is_nullable, error_message "
        "FROM sys.dm_exec_describe_first_result_set_for_object(?, 0) "
        "WHERE is_hidden = 0 OR is_hidden IS NULL ORDER BY column_ordinal",
        (object_id,),
    )
    if not rows or any(r.get("error_message") for r in rows):
        return None
    return [
        ResultColumn(name=r["name"], type=r["system_type_name"], nullable=bool(r["is_nullable"]))
        for r in rows
    ]


def _collect_effects(
    db: Database,
    qualified: str,
    definition: str,
    depth: int,
    seen: set[str],
    reads: set[str],
    writes: set[str],
    calls: set[str],
    notes: list[str],
) -> None:
    """Walk the procedure (and procedures/triggers it reaches) collecting table reads and writes."""
    if qualified.lower() in seen or depth > 5:
        return
    seen.add(qualified.lower())
    try:
        refs = _references(db, qualified)
    except Exception as exc:  # metadata can fail when the code references missing objects
        notes.append(
            f"Could not read dependency metadata for {qualified}: {exc}. Falling back to text analysis."
        )
        refs = []
    for ref in refs:
        full = f"{ref['schema_name']}.{ref['name']}"
        kind = (ref["type"] or "").strip()
        if kind == "U":
            if ref["is_updated"]:
                writes.add(full)
            else:
                reads.add(full)
        elif kind == "P":
            calls.add(full)
            inner = db.query("SELECT OBJECT_DEFINITION(OBJECT_ID(?)) AS definition", (full,))
            inner_def = inner[0]["definition"] if inner else None
            if inner_def:
                _collect_effects(db, full, inner_def, depth + 1, seen, reads, writes, calls, notes)
        elif kind in ("V", "FN", "IF", "TF"):
            reads.add(full)
    for candidate in write_targets_from_text(definition):
        resolved = _resolve_table(db, candidate)
        if resolved:
            writes.add(resolved)
    reads.difference_update(writes)


def inspect_procedure(db: Database, name: str) -> ProcedureInfo:
    object_id, schema, proc = resolve_procedure(db, name)
    qualified = f"{schema}.{proc}"
    definition = db.query("SELECT OBJECT_DEFINITION(?) AS definition", (object_id,))[0][
        "definition"
    ]
    if not definition:
        raise InspectionError(
            f"The definition of {qualified} is not readable (encrypted or no VIEW DEFINITION permission)."
        )

    reads: set[str] = set()
    writes: set[str] = set()
    calls: set[str] = set()
    notes: list[str] = []
    _collect_effects(db, qualified, definition, 0, set(), reads, writes, calls, notes)

    # Triggers on written tables can write more tables.
    triggers: list[str] = []
    pending = sorted(writes)
    seen_tables: set[str] = set()
    while pending:
        table = pending.pop()
        if table in seen_tables:
            continue
        seen_tables.add(table)
        for trigger in _triggers(db, table):
            triggers.append(trigger)
            trig_def = (
                db.query("SELECT OBJECT_DEFINITION(OBJECT_ID(?)) AS definition", (trigger,))[0][
                    "definition"
                ]
                or ""
            )
            before = set(writes)
            _collect_effects(db, trigger, trig_def, 1, set(), reads, writes, calls, notes)
            pending.extend(sorted(writes - before))

    risks = analyze_risks(definition)
    if triggers:
        risks.append(
            Risk(
                "triggers",
                "info",
                "Triggers run when this procedure writes; their changes are included.",
                sorted(triggers),
            )
        )
    parameters = _parameters(db, object_id)
    if any(p.is_table_type for p in parameters):
        risks.append(
            Risk(
                "table_valued_parameter",
                "block",
                "Table-valued parameters are not supported yet in sprocguard 0.1.",
                [p.name for p in parameters if p.is_table_type],
            )
        )
    try:
        first = _first_result_set(db, object_id)
    except Exception:
        first = None

    return ProcedureInfo(
        schema=schema,
        name=proc,
        object_id=object_id,
        definition=definition,
        definition_hash=hashlib.sha256(definition.encode("utf-8")).hexdigest(),
        parameters=parameters,
        reads=sorted(reads),
        writes=sorted(writes),
        calls=sorted(calls),
        triggers=sorted(set(triggers)),
        first_result_set=first,
        risks=risks,
        suggested_case_count=min(12, 2 + count_branches(definition)),
        notes=notes,
    )


# --------------------------------------------------------------------------------------
# Case suggestions
# --------------------------------------------------------------------------------------


_EDGE_VALUES: dict[str, list[Any]] = {
    "int": [0, -1],
    "bigint": [0, -1],
    "smallint": [0, -1],
    "tinyint": [0, 255],
    "bit": [0, 1],
    "decimal": ["0", "-1"],
    "numeric": ["0", "-1"],
    "money": ["0", "-1"],
    "float": [0.0, -1.0],
    "varchar": ["", "x" * 50],
    "nvarchar": ["", "ñandú"],
    "char": [""],
    "nchar": [""],
    "date": ["1900-01-01", "9999-12-31"],
    "datetime": ["1900-01-01T00:00:00"],
    "datetime2": ["0001-01-01T00:00:00"],
}


def suggest_cases(db: Database, info: ProcedureInfo, samples_per_param: int = 3) -> dict[str, Any]:
    """Suggest parameter values: real values from matching columns plus edge values."""
    tables = info.reads + info.writes
    suggestions: dict[str, Any] = {}
    for param in info.parameters:
        if param.is_table_type:
            continue
        column = param.name.lstrip("@")
        real: list[Any] = []
        source = None
        for table in tables:
            schema, tname = table.split(".", 1)
            exists = db.query(
                "SELECT 1 AS ok FROM sys.columns WHERE object_id = OBJECT_ID(?) AND name = ?",
                (table, column),
            )
            if exists:
                rows = db.query(
                    f"SELECT DISTINCT TOP ({int(samples_per_param)}) {quote_name(column)} AS v "
                    f"FROM {quote_name(schema, tname)} WHERE {quote_name(column)} IS NOT NULL",
                )
                real = [r["v"] for r in rows]
                source = f"{table}.{column}"
                break
        base_type = param.type.split("(")[0]
        suggestions[param.name] = {
            "type": param.type,
            "is_output": param.is_output,
            "real_values": real,
            "real_values_from": source,
            "edge_values": [*_EDGE_VALUES.get(base_type, []), None],
        }
    return {
        "procedure": info.full_name,
        "suggested_case_count": info.suggested_case_count,
        "parameters": suggestions,
        "advice": (
            "Pick cases that cover each branch (IF/CASE), each error path (THROW/RAISERROR), "
            "an empty result, and boundary values. Use real values so the procedure finds data."
        ),
    }

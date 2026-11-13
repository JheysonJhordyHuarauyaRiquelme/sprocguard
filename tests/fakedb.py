"""A tiny in-memory imitation of the SQL Server behavior sprocguard relies on.

It understands the catalog queries sprocguard sends, keeps tables in memory,
supports nested transactions with rollback, and runs "procedures" written as
Python functions. IDENTITY values are NOT reused after a rollback, like in
SQL Server, so volatility detection is exercised for real.
"""

from __future__ import annotations

import copy
import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sprocguard.db import ResultSet


class FakeDbError(Exception):
    pass


@dataclass
class FakeProc:
    schema: str
    name: str
    definition: str
    params: list[tuple[str, str, bool]]  # (name, type, is_output)
    reads: list[str]
    writes: list[str]
    body: Callable[[FakeDatabase, dict[str, Any]], tuple[list[ResultSet], int, dict[str, Any]]]
    first_result_set: list[tuple[str, str]] | None = None


@dataclass
class FakeDatabase:
    name: str = "DemoShop"
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    keys: dict[str, list[str]] = field(default_factory=dict)
    procs: dict[str, FakeProc] = field(default_factory=dict)
    trancount: int = 0
    implicit: bool = False
    saved: dict[str, list[dict[str, Any]]] | None = None
    clock: itertools.count = field(default_factory=lambda: itertools.count(1))
    identity: itertools.count = field(default_factory=lambda: itertools.count(1000))
    log: list[str] = field(default_factory=list)
    error_types: tuple = (FakeDbError,)

    # ---- helpers used by fake procedures -------------------------------------------
    frozen_clock: bool = False

    def now(self) -> str:
        if self.frozen_clock:  # e.g. datetime2(0): two runs in the same second look identical
            return "2026-11-12T10:00:00"
        return f"2026-11-12T10:00:{next(self.clock):02d}"

    def next_id(self) -> int:
        return next(self.identity)

    def write(self) -> None:
        if self.trancount == 0 and self.implicit:
            self._begin()

    def commit(self) -> None:
        """What a COMMIT inside a procedure does."""
        if self.trancount == 0:
            raise FakeDbError(
                "The COMMIT TRANSACTION request has no corresponding BEGIN TRANSACTION."
            )
        self.trancount -= 1
        if self.trancount == 0:
            self.saved = None  # committed for real

    # ---- transactions ---------------------------------------------------------------
    def _begin(self) -> None:
        if self.trancount == 0:
            self.saved = copy.deepcopy(self.tables)
        self.trancount += 1

    def _rollback(self) -> None:
        if self.trancount == 0:
            raise FakeDbError("ROLLBACK without transaction")
        self.tables = self.saved
        self.saved = None
        self.trancount = 0

    # ---- Database protocol ----------------------------------------------------------
    def query(self, sql, params=()):
        sets = self.execute(sql, params)
        if not sets:
            return []
        return [dict(zip(sets[0].columns, r, strict=True)) for r in sets[0].rows]

    def close(self):
        pass

    def _proc(self, name: str) -> FakeProc | None:
        key = name.replace("[", "").replace("]", "").lower()
        if "." not in key:
            key = "dbo." + key
        return self.procs.get(key)

    def _table(self, name: str) -> str | None:
        key = name.replace("[", "").replace("]", "")
        if "." not in key:
            key = "dbo." + key
        for t in self.tables:
            if t.lower() == key.lower():
                return t
        return None

    def execute(self, sql, params=()):
        params = list(params)
        s = " ".join(sql.split())
        self.log.append(s)

        if s.startswith("SET IMPLICIT_TRANSACTIONS ON"):
            self.implicit = True
            n = s.count("BEGIN TRANSACTION")
            for _ in range(n):
                self._begin()
            return []
        if s.startswith("IF @@TRANCOUNT > 0 ROLLBACK"):
            if self.trancount > 0:
                self._rollback()
            self.implicit = False
            return []
        if s == "SELECT @@TRANCOUNT AS n":
            return [ResultSet(["n"], [[self.trancount]])]
        if s == "SELECT SYSDATETIME() AS l, SYSUTCDATETIME() AS u":
            return [
                ResultSet(
                    ["l", "u"], [["2026-11-12T10:00:00.000000", "2026-11-12T15:00:00.000000"]]
                )
            ]
        if s == "SELECT DB_NAME() AS name":
            return [ResultSet(["name"], [[self.name]])]
        if "WHERE o.object_id = OBJECT_ID(?)" in s:
            p = self._proc(params[0])
            if not p:
                return [ResultSet(["object_id", "schema_name", "name", "type"], [])]
            return [
                ResultSet(
                    ["object_id", "schema_name", "name", "type"],
                    [[hash(p.name) % 10000, p.schema, p.name, "P "]],
                )
            ]
        if s.startswith("SELECT OBJECT_DEFINITION(?)"):
            p = next(p for p in self.procs.values() if hash(p.name) % 10000 == params[0])
            return [ResultSet(["definition"], [[p.definition]])]
        if s.startswith("SELECT OBJECT_DEFINITION(OBJECT_ID(?))"):
            p = self._proc(params[0])
            return [ResultSet(["definition"], [[p.definition if p else None]])]
        if "FROM sys.parameters p" in s:
            p = next(p for p in self.procs.values() if hash(p.name) % 10000 == params[0])
            rows = []
            for name, typ, is_out in p.params:
                base = typ.split("(")[0]
                rows.append([name, base, base, -1 if "max" in typ else 50, 18, 2, int(is_out), 0])
            return [
                ResultSet(
                    [
                        "name",
                        "system_type",
                        "user_type",
                        "max_length",
                        "precision",
                        "scale",
                        "is_output",
                        "is_table_type",
                    ],
                    rows,
                )
            ]
        if "sys.dm_sql_referenced_entities" in s:
            p = self._proc(params[0])
            rows = []
            if p:
                for t in p.reads:
                    sc, nm = t.split(".")
                    rows.append([sc, nm, "U ", 1, 0])
                for t in p.writes:
                    sc, nm = t.split(".")
                    rows.append([sc, nm, "U ", 1, 1])
            return [ResultSet(["schema_name", "name", "type", "is_selected", "is_updated"], rows)]
        if "OBJECT_ID(?, 'U')" in s:
            t = self._table(params[0])
            if t:
                sc, nm = t.split(".")
                return [ResultSet(["schema_name", "name"], [[sc, nm]])]
            return [ResultSet(["schema_name", "name"], [[None, None]])]
        if "FROM sys.triggers" in s:
            return [ResultSet(["schema_name", "name"], [])]
        if "dm_exec_describe_first_result_set_for_object" in s:
            p = next(p for p in self.procs.values() if hash(p.name) % 10000 == params[0])
            if not p.first_result_set:
                return [ResultSet(["name", "system_type_name", "is_nullable", "error_message"], [])]
            return [
                ResultSet(
                    ["name", "system_type_name", "is_nullable", "error_message"],
                    [[n, t, 1, None] for n, t in p.first_result_set],
                )
            ]
        if "i.is_primary_key = 1" in s:
            t = self._table(params[0])
            return [ResultSet(["name"], [[k] for k in self.keys.get(t, [])])]
        m = re.match(r"SELECT COUNT_BIG\(\*\) AS n FROM \[(\w+)\]\.\[(\w+)\]$", s)
        if m:
            return [ResultSet(["n"], [[len(self.tables[f"{m.group(1)}.{m.group(2)}"])]])]
        m = re.match(r"SELECT \* FROM \[(\w+)\]\.\[(\w+)\]$", s)
        if m:
            rows = self.tables[f"{m.group(1)}.{m.group(2)}"]
            cols = list(rows[0]) if rows else []
            return (
                [ResultSet(cols, [[r[c] for c in cols] for r in rows])]
                if rows
                else [ResultSet([], [])]
            )
        if "FROM sys.columns WHERE object_id = OBJECT_ID(?) AND name = ?" in s:
            t = self._table(params[0])
            has = t and self.tables[t] and params[1] in self.tables[t][0]
            return [ResultSet(["ok"], [[1]] if has else [])]
        m = re.match(r"SELECT DISTINCT TOP \((\d+)\) \[(\w+)\] AS v FROM \[(\w+)\]\.\[(\w+)\]", s)
        if m:
            n, col, sc, nm = int(m.group(1)), m.group(2), m.group(3), m.group(4)
            vals = []
            for r in self.tables[f"{sc}.{nm}"]:
                if r[col] is not None and r[col] not in vals:
                    vals.append(r[col])
            return [ResultSet(["v"], [[v] for v in vals[:n]])]
        if "EXEC @__sg_rc =" in s:
            return self._exec_batch(sql, params)
        raise AssertionError(f"FakeDatabase does not understand: {s}")

    def _exec_batch(self, sql: str, values: list[Any]):
        m = re.search(r"EXEC @__sg_rc = \[(\w+)\]\.\[(\w+)\]", sql)
        proc = self.procs[f"{m.group(1)}.{m.group(2)}".lower()]
        # Map placeholders to parameter names in order of appearance.
        args: dict[str, Any] = {}
        out_vars: dict[str, str] = {}
        var_values: dict[str, Any] = {}
        it = iter(values)
        for token in re.finditer(
            r"DECLARE (@__sg_out\d+) [^;=]+= \?|(@\w+) = \?|(@\w+) = (@__sg_out\d+) OUTPUT", sql
        ):
            if token.group(1):
                var_values[token.group(1)] = next(it)
            elif token.group(2):
                args[token.group(2)] = next(it)
            else:
                out_vars[token.group(3)] = token.group(4)
                args[token.group(3)] = var_values.get(token.group(4))
        sets, rc, outputs = proc.body(self, args)
        select_cols = ["__sprocguard_return_value", *out_vars]
        final = ResultSet(select_cols, [[rc, *(outputs.get(name) for name in out_vars)]])
        return [*sets, final]

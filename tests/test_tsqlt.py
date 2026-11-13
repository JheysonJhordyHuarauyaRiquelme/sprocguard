import re

import sqlglot

from conftest import PLACE_ORDER_CASES
from sprocguard.analysis import inspect_procedure
from sprocguard.capture import capture_baseline, load_baseline
from sprocguard.tsqlt import generate_tests, sql_literal

CASES = [
    {"name": "customer with orders", "params": {"@CustomerId": 1}},
    {"name": "customer without orders", "params": {"@CustomerId": 2}},
    {"name": "unknown customer -> error", "params": {"@CustomerId": 99}},
]


def test_sql_literal():
    assert sql_literal(None) == "NULL"
    assert sql_literal(5) == "5"
    assert sql_literal("12.50", "decimal(10,2)") == "12.50"
    assert sql_literal("O'Brien", "nvarchar(50)") == "N'O''Brien'"
    assert sql_literal("2026-01-05T09:00:00.123456", "datetime") == "N'2026-01-05T09:00:00.123'"
    assert sql_literal("2026-01-05T09:00:00.123456", "datetime2(7)") == "N'2026-01-05T09:00:00.123456'"
    assert sql_literal("0x0A0B", "varbinary(16)") == "CONVERT(varbinary(max), '0x0A0B', 1)"
    assert sql_literal("1; DROP TABLE x", "int") == "N'1; DROP TABLE x'"


def _batches(sql: str):
    return [b.strip() for b in re.split(r"^GO$", sql, flags=re.MULTILINE) if b.strip() and not b.strip().startswith("-- Run")]


def test_generates_valid_tsqlt_for_read_only_procedure(db, config):
    capture_baseline(db, config, "dbo.usp_GetCustomerOrders", CASES)
    info = inspect_procedure(db, "dbo.usp_GetCustomerOrders")
    result = generate_tests(info, load_baseline(config, info))
    assert result["supported"] and result["tests"] == 3, result
    sql = result["sql"]
    assert "EXEC tSQLt.NewTestClass N'sprocguard_dbo_usp_GetCustomerOrders';" in sql
    assert "INSERT INTO #Actual EXEC [dbo].[usp_GetCustomerOrders] @CustomerId = 1;" in sql
    assert "(1, N'2026-01-05T09:00:00', 25.00)" in sql
    assert "@ExpectedMessagePattern = N'%Customer not found%'" in sql
    assert "EXEC tSQLt.AssertEqualsTable '#Expected', '#ActualCompared'" in sql

    # Every statement inside the generated procedures must be valid T-SQL.
    for batch in _batches(sql):
        body = re.search(r"\bBEGIN\n(.*)\nEND;", batch, flags=re.DOTALL)
        statements = body.group(1) if body else batch
        # sqlglot does not know T-SQL's INSERT ... EXEC; validate the EXEC part on its own.
        statements = statements.replace("INSERT INTO #Actual EXEC", "EXEC")
        for statement in sqlglot.parse(statements, read="tsql"):
            assert statement is not None


def test_write_procedures_are_not_supported_yet(db, config):
    capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)
    info = inspect_procedure(db, "dbo.usp_PlaceOrder")
    result = generate_tests(info, load_baseline(config, info))
    assert not result["supported"]
    assert "modifies tables" in result["reason"]

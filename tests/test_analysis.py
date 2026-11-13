from conftest import PLACE_ORDER_SQL

from sprocguard.analysis import (
    analyze_risks,
    count_branches,
    inspect_procedure,
    strip_comments_and_strings,
    suggest_cases,
    type_decl,
    write_targets_from_text,
)


def codes(sql):
    return {r.code: r.severity for r in analyze_risks(sql)}


def test_strip_removes_comments_and_strings():
    sql = "SELECT 1 -- xp_cmdshell\n/* outer /* nested */ GETDATE() */ SELECT 'ROLLBACK', N'it''s'"
    out = strip_comments_and_strings(sql)
    assert "xp_cmdshell" not in out and "GETDATE" not in out and "ROLLBACK" not in out
    assert out.count("''") == 2


def test_risks_ignore_comments_but_find_real_code():
    risks = codes(PLACE_ORDER_SQL)
    assert "external_side_effect" not in risks  # only mentioned in a comment
    assert "ddl" not in risks  # its own CREATE PROCEDURE header is not DDL it runs
    assert risks["nondeterministic"] == "info"
    assert risks["identity"] == "info"
    assert risks["error_paths"] == "info"


def test_blocking_and_warning_rules():
    assert (
        codes("EXEC msdb.dbo.sp_send_dbmail @recipients = 'a@b.c'")["external_side_effect"]
        == "block"
    )
    assert codes("SELECT * FROM SRV.Sales.dbo.Orders")["linked_server"] == "block"
    assert codes("EXEC sp_executesql @sql")["dynamic_sql"] == "warn"
    assert codes("EXEC (@sql)")["dynamic_sql"] == "warn"
    assert codes("EXEC (N'DELETE FROM dbo.T')")["dynamic_sql"] == "warn"
    assert codes("IF @@ERROR <> 0 ROLLBACK TRANSACTION")["inner_rollback"] == "warn"
    assert "linked_server" not in codes("SELECT o.Total, 1.5 FROM dbo.Orders o")
    assert "ddl" not in codes("CREATE TABLE #tmp (id int)")
    assert codes("CREATE TABLE dbo.Tmp (id int)")["ddl"] == "warn"


def test_write_targets_and_branches():
    targets = write_targets_from_text(
        PLACE_ORDER_SQL + "\nDELETE FROM #t; UPDATE o SET Total = 0 FROM dbo.Orders o;"
    )
    assert "dbo.Orders" in targets and "dbo.Products" in targets and "dbo.AuditLog" in targets
    assert "#t" not in targets
    assert "o" in targets  # alias; resolved (and discarded) against the catalog later
    assert count_branches(PLACE_ORDER_SQL) == 3  # IF, IF, THROW


def test_type_decl():
    assert type_decl("nvarchar", 100, 0, 0) == "nvarchar(50)"
    assert type_decl("varchar", -1, 0, 0) == "varchar(max)"
    assert type_decl("decimal", 9, 10, 2) == "decimal(10,2)"
    assert type_decl("datetime2", 8, 27, 7) == "datetime2(7)"
    assert type_decl("int", 4, 10, 0) == "int"


def test_inspect_procedure(db):
    info = inspect_procedure(db, "dbo.usp_PlaceOrder")
    assert info.writes == ["dbo.AuditLog", "dbo.Orders", "dbo.Products"]
    assert info.reads == ["dbo.Customers"]
    assert [p.name for p in info.parameters] == [
        "@CustomerId",
        "@ProductId",
        "@Quantity",
        "@OrderId",
    ]
    assert info.parameters[-1].is_output
    assert not info.blocked
    assert info.suggested_case_count == 5


def test_suggest_cases_uses_real_values(db):
    info = inspect_procedure(db, "dbo.usp_PlaceOrder")
    suggestion = suggest_cases(db, info)
    customer = suggestion["parameters"]["@CustomerId"]
    assert customer["real_values"] == [1, 2]
    assert customer["real_values_from"] == "dbo.Customers.CustomerId"
    assert None in customer["edge_values"]

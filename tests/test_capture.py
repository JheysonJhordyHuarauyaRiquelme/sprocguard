import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import PLACE_ORDER_CASES, make_place_order_body

from sprocguard.analysis import inspect_procedure
from sprocguard.capture import CaptureError, build_exec_batch, capture_baseline, clean_error, verify
from sprocguard.db import SafetyError, check_allowed_database


def test_exec_batch_uses_placeholders_and_output_variables(db):
    info = inspect_procedure(db, "dbo.usp_PlaceOrder")
    sql, values = build_exec_batch(info, {"CustomerId": 1, "@productid": 10, "@Quantity": 2})
    assert (
        "EXEC @__sg_rc = [dbo].[usp_PlaceOrder] @CustomerId = ?, @ProductId = ?, @Quantity = ?, "
        "@OrderId = @__sg_out3 OUTPUT;" in sql
    )
    assert values == [1, 10, 2]
    assert "Robert'); DROP" not in sql
    with pytest.raises(CaptureError, match="no parameter @Nope"):
        build_exec_batch(info, {"@Nope": 1})


def test_capture_leaves_data_untouched_and_learns_volatility(db, config):
    original = copy.deepcopy(db.tables)
    result = capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)
    assert db.tables == original, "capture must roll back every change"
    assert db.trancount == 0
    assert result["cases"] == 3
    assert result["watched_tables"] == ["dbo.AuditLog", "dbo.Orders", "dbo.Products"]
    volatile = result["volatile"]["regular customer buys 2"]
    assert "output:@OrderId" in volatile
    assert "dbo.Orders.OrderId" in volatile and "dbo.Orders.OrderDate" in volatile
    assert "dbo.AuditLog.At" in volatile

    baseline = json.loads(Path(result["baseline_file"]).read_text(encoding="utf-8"))
    error_case = baseline["cases"][2]
    assert error_case["expected"]["error"] == "Insufficient stock"
    stock_change = baseline["cases"][0]["expected"]["tables"]["dbo.Products"]["updated"]
    assert stock_change == [{"key": {"ProductId": 10}, "changes": {"Stock": [100, 98]}}]


def test_verify_passes_when_nothing_changed(db, config):
    capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)
    report = verify(db, config, "dbo.usp_PlaceOrder")
    assert report["passed"], report
    assert report["summary"] == "3/3 case(s) behave exactly as in the baseline."


def test_verify_catches_a_regression(db, config):
    capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)
    # Someone "refactors" the procedure and the VIP discount becomes 5%.
    db.procs["dbo.usp_placeorder"].body = make_place_order_body(vip_discount=Decimal("0.95"))
    db.procs["dbo.usp_placeorder"].definition += "\n-- refactored"
    report = verify(db, config, "dbo.usp_PlaceOrder")
    assert not report["passed"]
    assert report["definition_changed"]
    by_name = {c["name"]: c for c in report["cases"]}
    assert by_name["regular customer buys 2"]["passed"]
    assert by_name["insufficient stock raises error"]["passed"]
    vip = by_name["VIP customer gets 10% off"]
    assert not vip["passed"]
    text = "\n".join(vip["differences"])
    assert "33.75" in text and "35.62" in text  # 37.50 * 0.90 vs 37.50 * 0.95


def test_overwrite_protection(db, config):
    capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)
    with pytest.raises(CaptureError, match="already exists"):
        capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)
    capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES, overwrite=True)


def test_stray_commit_inside_procedure_cannot_persist_changes(db, config):
    body = db.procs["dbo.usp_placeorder"].body

    def committing_body(fake, args):
        out = body(fake, args)
        fake.commit()  # an unbalanced COMMIT inside the procedure
        return out

    db.procs["dbo.usp_placeorder"].body = committing_body
    original = copy.deepcopy(db.tables)
    capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES[:1])
    assert db.tables == original


def test_blocked_procedure_is_refused(db, config):
    db.procs[
        "dbo.usp_placeorder"
    ].definition += "\nEXEC msdb.dbo.sp_send_dbmail @recipients = 'boss@x.com';"
    with pytest.raises(SafetyError, match="external_side_effect"):
        capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES)


def test_only_allowed_databases(db):
    assert check_allowed_database(db, ["demoshop"]) == "DemoShop"
    with pytest.raises(SafetyError, match="not in"):
        check_allowed_database(db, ["OtherDb"])


def test_clean_error_messages():
    class PyodbcLike(Exception):
        pass

    exc = PyodbcLike(
        "42000",
        "[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Insufficient stock "
        "(50002) (SQLExecDirectW)",
    )
    assert clean_error(exc) == "Insufficient stock"
    pymssql_like = Exception(
        50002,
        b"Insufficient stockDB-Lib error message 20018, severity 16:\nGeneral SQL Server error",
    )
    assert clean_error(pymssql_like) == "Insufficient stock"


def test_dates_equal_in_both_runs_are_still_volatile(db, config):
    """A datetime2(0) written twice within the same second must not cause a false alarm later."""
    db.frozen_clock = True
    result = capture_baseline(db, config, "dbo.usp_PlaceOrder", PLACE_ORDER_CASES[:1])
    volatile = result["volatile"]["regular customer buys 2"]
    assert "dbo.Orders.OrderDate" in volatile and "dbo.AuditLog.At" in volatile
    db.frozen_clock = False  # "the next day": dates are different now
    assert verify(db, config, "dbo.usp_PlaceOrder")["passed"]

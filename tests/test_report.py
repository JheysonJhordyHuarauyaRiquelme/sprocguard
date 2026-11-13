from sprocguard.report import to_markdown


def _result(name, cases, definition_changed=False, new_tables=()):
    return {
        "procedure": name,
        "passed": all(c["passed"] for c in cases),
        "summary": "",
        "definition_changed": definition_changed,
        "baseline_captured_at": "2026-11-13T10:00:00+00:00",
        "newly_written_tables": list(new_tables),
        "cases": cases,
    }


def test_report_when_everything_passes():
    report = to_markdown([_result("dbo.a", [{"name": "x", "params": {}, "passed": True}])])
    assert "All 1 procedure(s) behave exactly as in their baselines." in report
    assert "| `dbo.a` | Same behavior | 1/1 as in the baseline |" in report
    assert "###" not in report


def test_report_explains_each_failed_case():
    failing = {
        "name": "VIP gets 10% off",
        "params": {"@CustomerId": 1, "@Code": "A|B"},
        "passed": False,
        "differences": ["result set #1 row 1 column Total: expected 33.75, got 33.8"],
    }
    report = to_markdown(
        [
            _result("dbo.ok", [{"name": "fine", "params": {}, "passed": True}]),
            _result(
                "dbo.usp_PlaceOrder",
                [failing, {"name": "fine", "params": {}, "passed": True}],
                definition_changed=True,
                new_tables=["dbo.Audit"],
            ),
        ]
    )
    assert "**1 of 2 procedure(s) changed behavior.**" in report
    assert "| `dbo.usp_PlaceOrder` | **Changed** | 1/2 as in the baseline |" in report
    assert "### dbo.usp\\_PlaceOrder" in report
    assert (
        "definition changed since the baseline was captured (2026-11-13T10:00:00+00:00)" in report
    )
    assert "It now writes `dbo.Audit`" in report
    assert '- **VIP gets 10% off** `@CustomerId=1, @Code="A|B"`' in report
    assert "  - result set \\#1 row 1 column Total: expected 33.75, got 33.8" in report
    assert "fine" not in report.split("### dbo.usp\\_PlaceOrder")[1]

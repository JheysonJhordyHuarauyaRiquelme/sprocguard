"""End-to-end tests of the command-line interface against the fake SQL Server."""

import json
from decimal import Decimal

import pytest
from conftest import PLACE_ORDER_CASES, make_place_order_body

from sprocguard import service
from sprocguard.cli import main


@pytest.fixture
def cli(db, config, monkeypatch, tmp_path):
    """Point the CLI at the fake database and a temporary project folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "load_config", lambda _path=None: config)
    monkeypatch.setattr(service, "connect", lambda _config: db)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(PLACE_ORDER_CASES), encoding="utf-8")
    return cases


def test_inspect_and_suggest(cli, capsys):
    assert main(["inspect", "dbo.usp_PlaceOrder"]) == 0
    out = capsys.readouterr().out
    assert "dbo.usp_PlaceOrder" in out
    assert "dbo.Orders" in out

    assert main(["--json", "suggest", "dbo.usp_PlaceOrder"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "@Quantity" in data["parameters"]


def test_capture_list_and_verify_pass(cli, capsys):
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 0
    assert "baseline_file" in capsys.readouterr().out

    assert main(["--json", "list"]) == 0
    baselines = json.loads(capsys.readouterr().out)
    assert [b["procedure"] for b in baselines] == ["dbo.usp_PlaceOrder"]

    assert main(["verify"]) == 0
    assert capsys.readouterr().out.startswith("PASS")


def test_verify_fails_with_exit_code_2_on_a_regression(cli, db, capsys):
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 0
    capsys.readouterr()
    db.procs["dbo.usp_placeorder"].body = make_place_order_body(vip_discount=Decimal("0.95"))

    assert main(["verify", "dbo.usp_PlaceOrder"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("FAIL")
    assert "XX  VIP customer gets 10% off" in out


def test_capture_refuses_to_overwrite_without_flag(cli, capsys):
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 0
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 1
    assert "sprocguard:" in capsys.readouterr().err
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli), "--overwrite"]) == 0


def test_verify_and_list_without_baselines(cli, capsys):
    assert main(["verify"]) == 1
    assert "No baselines found" in capsys.readouterr().out
    assert main(["list"]) == 0
    assert "No baselines yet" in capsys.readouterr().out


def test_tsqlt_generation_from_the_cli(cli, capsys):
    cases = cli.parent / "read_cases.json"
    cases.write_text(
        json.dumps([{"name": "VIP orders", "params": {"@CustomerId": 1}}]), encoding="utf-8"
    )
    assert main(["capture", "dbo.usp_GetCustomerOrders", "--cases", str(cases)]) == 0
    capsys.readouterr()
    assert main(["--json", "tsqlt", "dbo.usp_GetCustomerOrders"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["file"].endswith(".sql")


def test_verify_writes_a_markdown_report(cli, db, tmp_path, capsys):
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 0
    db.procs["dbo.usp_placeorder"].body = make_place_order_body(vip_discount=Decimal("0.95"))
    report = tmp_path / "report.md"

    assert main(["verify", "--markdown-report", str(report)]) == 2
    text = report.read_text(encoding="utf-8")
    assert text.startswith("## sprocguard")
    assert "| `dbo.usp_PlaceOrder` | **Changed** |" in text
    assert "VIP customer gets 10% off" in text


def test_verify_help_lists_only_failures(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["verify", "--help"])
    assert exc.value.code == 0
    assert "--only-failures" in capsys.readouterr().out


@pytest.mark.parametrize("regression", [False, True])
def test_verify_only_failures_filters_text(cli, db, capsys, regression):
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 0
    capsys.readouterr()
    if regression:
        db.procs["dbo.usp_placeorder"].body = make_place_order_body(vip_discount=Decimal("0.95"))
    expected_code = 2 if regression else 0
    assert main(["--json", "verify"]) == expected_code
    result = json.loads(capsys.readouterr().out)[0]
    assert main(["verify"]) == expected_code
    full = capsys.readouterr().out
    assert "  ok " in full

    assert main(["verify", "--only-failures"]) == expected_code
    out = capsys.readouterr().out
    assert out.splitlines()[0] == full.splitlines()[0]
    assert "  ok " not in out
    if not regression:
        assert len(out.splitlines()) == 1
    for case in result["cases"]:
        if case["passed"]:
            assert case["name"] not in out
        else:
            assert f"XX  {case['name']}" in out
            for diff in case["differences"]:
                assert diff in out


@pytest.mark.parametrize("regression", [False, True])
def test_only_failures_preserves_json_and_markdown(cli, db, tmp_path, capsys, regression):
    assert main(["capture", "dbo.usp_PlaceOrder", "--cases", str(cli)]) == 0
    capsys.readouterr()
    if regression:
        db.procs["dbo.usp_placeorder"].body = make_place_order_body(vip_discount=Decimal("0.95"))
    expected_code = 2 if regression else 0
    assert main(["--json", "verify"]) == expected_code
    original_json = json.loads(capsys.readouterr().out)
    assert main(["--json", "verify", "--only-failures"]) == expected_code
    assert json.loads(capsys.readouterr().out) == original_json

    original_report = tmp_path / "original.md"
    filtered_report = tmp_path / "filtered.md"
    assert main(["verify", "--markdown-report", str(original_report)]) == expected_code
    assert (
        main(["verify", "--only-failures", "--markdown-report", str(filtered_report)])
        == expected_code
    )
    assert filtered_report.read_bytes() == original_report.read_bytes()

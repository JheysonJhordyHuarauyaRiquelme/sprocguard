import asyncio

import pytest

from sprocguard.cli import main
from sprocguard.config import ConfigError, load_config
from sprocguard.server import build_server


def test_mcp_server_exposes_tools_with_annotations():
    server = build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name: t for t in tools}
    assert set(names) == {"inspect_procedure", "suggest_cases", "capture_baseline", "verify_procedure",
                          "generate_tsqlt_tests", "list_baselines"}
    assert names["verify_procedure"].annotations.read_only_hint is True
    assert names["capture_baseline"].annotations.read_only_hint is False
    schema = names["capture_baseline"].input_schema
    assert set(schema["required"]) == {"procedure", "cases"}


def test_init_and_load_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    config = load_config()
    assert config.safety.allowed_databases == ["DemoShop"]
    assert config.connection.driver == "pyodbc"
    assert config.baseline_path == (tmp_path / ".sprocguard" / "baselines").resolve()
    assert main(["init"]) == 1  # never overwrites


def test_config_requires_allowed_databases(tmp_path):
    path = tmp_path / "sprocguard.toml"
    path.write_text('[connection]\nodbc = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="allowed_databases"):
        load_config(path)
    path.write_text('[connection]\nodbc = "x"\n[safety]\nallowed_databases=["A"]\ntypo = 1\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown keys"):
        load_config(path)


def test_cli_reports_missing_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["list"]) == 1
    assert "Configuration file not found" in capsys.readouterr().err


def test_tool_errors_reach_the_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no sprocguard.toml here
    server = build_server()
    with pytest.raises(Exception) as info:
        asyncio.run(server.call_tool("inspect_procedure", {"procedure": "dbo.x"}))
    assert "Configuration file not found" in str(info.value)

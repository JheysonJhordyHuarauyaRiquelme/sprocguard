import pytest

from sprocguard.capture import CaptureError
from sprocguard.cases import collect_case_files, procedure_for


@pytest.mark.parametrize(
    ("file_name", "procedure"),
    [
        ("usp_PlaceOrder.json", "dbo.usp_PlaceOrder"),
        ("sales.usp_PlaceOrder.json", "sales.usp_PlaceOrder"),
        ("dbo.usp_GetCustomerOrders.json", "dbo.usp_GetCustomerOrders"),
    ],
)
def test_procedure_name_comes_from_the_file_name(file_name, procedure):
    assert procedure_for(f"cases/{file_name}") == procedure


def test_collect_expands_folders_and_removes_duplicates(tmp_path):
    folder = tmp_path / "cases"
    folder.mkdir()
    for name in ("b.json", "a.json", "notes.txt"):
        (folder / name).write_text("[]", encoding="utf-8")

    files = collect_case_files([str(folder), str(folder / "a.json")])

    assert [f.name for f in files] == ["a.json", "b.json"]


def test_collect_reports_missing_paths_and_empty_folders(tmp_path):
    with pytest.raises(CaptureError, match="not found"):
        collect_case_files([str(tmp_path / "nope")])
    with pytest.raises(CaptureError, match=r"no \.json cases files"):
        collect_case_files([str(tmp_path)])


def test_baseline_dir_can_come_from_the_environment(tmp_path, monkeypatch):
    from sprocguard.config import load_config

    config_file = tmp_path / "sprocguard.toml"
    config_file.write_text(
        '[connection]\nserver = "x"\nuser = "sa"\n[safety]\nallowed_databases = ["D"]\n',
        encoding="utf-8",
    )
    target = tmp_path / "temp-baselines"
    monkeypatch.setenv("SPROCGUARD_BASELINE_DIR", str(target))

    assert load_config(config_file).baseline_path == target.resolve()

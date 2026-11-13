"""High-level operations shared by the CLI and the MCP server."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from . import analysis, capture, tsqlt
from .config import Config, load_config
from .db import Database, connect


@contextmanager
def session(config_path: str | None = None) -> Iterator[tuple[Config, Database]]:
    config = load_config(config_path)
    db = connect(config)
    try:
        yield config, db
    finally:
        db.close()


def inspect(procedure: str, include_definition: bool = False, config_path: str | None = None) -> dict[str, Any]:
    with session(config_path) as (config, db):
        info = analysis.inspect_procedure(db, procedure)
        data = info.to_dict(include_definition=include_definition)
        data["has_baseline"] = capture.baseline_file(config, info).exists()
        data["can_run"] = not info.blocked or all(
            r.code in ("external_side_effect", "linked_server") for r in info.blocked
        ) and config.safety.allow_external_side_effects
        return data


def suggest(procedure: str, config_path: str | None = None) -> dict[str, Any]:
    with session(config_path) as (_config, db):
        info = analysis.inspect_procedure(db, procedure)
        return analysis.suggest_cases(db, info)


def capture_baseline(procedure: str, cases: list[dict[str, Any]], watch_tables: list[str] | None = None,
                     overwrite: bool = False, config_path: str | None = None) -> dict[str, Any]:
    with session(config_path) as (config, db):
        return capture.capture_baseline(db, config, procedure, cases, watch_tables, overwrite)


def verify(procedure: str, config_path: str | None = None) -> dict[str, Any]:
    with session(config_path) as (config, db):
        return capture.verify(db, config, procedure)


def generate_tsqlt(procedure: str, config_path: str | None = None) -> dict[str, Any]:
    with session(config_path) as (config, db):
        info = analysis.inspect_procedure(db, procedure)
        baseline = capture.load_baseline(config, info)
        result = tsqlt.write_tests(config, info, baseline)
        if result.get("file"):
            result.pop("sql", None)  # the script is in the file; keep the response short
        return result


def list_baselines(config_path: str | None = None) -> list[dict[str, Any]]:
    config = load_config(config_path)
    items = []
    if config.baseline_path.exists():
        for path in sorted(config.baseline_path.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append({
                "procedure": data.get("procedure"),
                "cases": len(data.get("cases", [])),
                "captured_at": data.get("captured_at"),
                "file": str(path),
            })
    return items

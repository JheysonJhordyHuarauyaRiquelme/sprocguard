"""Case files: JSON lists of ``{"name": ..., "params": {...}}`` objects, one file per procedure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capture import CaptureError


def load_cases(path: str | Path) -> Any:
    """Read and parse a cases file, turning file and JSON errors into CaptureError."""
    cases_path = Path(path)
    try:
        text = cases_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CaptureError(f"cases file not found: {path}") from None
    except OSError as exc:
        raise CaptureError(f"cannot read cases file {path}: {exc}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptureError(
            f"invalid JSON in cases file {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from None


def procedure_for(path: str | Path) -> str:
    """Name of the procedure a cases file belongs to: ``usp_X.json`` -> ``dbo.usp_X``."""
    stem = Path(path).stem
    return stem if "." in stem else f"dbo.{stem}"


def collect_case_files(paths: list[str]) -> list[Path]:
    """Expand files and folders (their ``*.json`` files) into a sorted, de-duplicated list."""
    found: list[Path] = []
    for item in paths:
        path = Path(item)
        if path.is_dir():
            found.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            found.append(path)
        else:
            raise CaptureError(f"cases file or folder not found: {item}")
    unique = list(dict.fromkeys(p.resolve() for p in found))
    if not unique:
        raise CaptureError(f"no .json cases files found in: {', '.join(paths)}")
    return unique

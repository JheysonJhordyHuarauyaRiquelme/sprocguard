"""Command-line interface: ``sprocguard <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, service
from .analysis import InspectionError
from .capture import CaptureError
from .config import DEFAULT_CONFIG_NAME, SAMPLE_CONFIG, ConfigError
from .db import SafetyError
from .report import to_markdown


def _print(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        _print_human(data)


def _print_human(data: Any, indent: int = 0) -> None:
    pad = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                print(f"{pad}{key}:")
                _print_human(value, indent + 1)
            else:
                print(f"{pad}{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                print(f"{pad}-")
                _print_human(item, indent + 1)
            else:
                print(f"{pad}- {item}")
    else:
        print(f"{pad}{data}")


def _print_verify(result: dict[str, Any]) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    print(f"{status}  {result['procedure']}: {result['summary']}")
    if result.get("definition_changed"):
        print("      (the procedure definition changed since the baseline was captured)")
    for table in result.get("newly_written_tables", []):
        print(f"      note: the procedure now writes {table}, which the baseline did not watch")
    for case in result["cases"]:
        mark = "ok " if case["passed"] else "XX "
        print(f"  {mark} {case['name']}")
        for diff in case["differences"]:
            print(f"        - {diff}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sprocguard", description="A safety net for SQL Server stored procedures."
    )
    parser.add_argument("--version", action="version", version=f"sprocguard {__version__}")
    parser.add_argument(
        "--config", help=f"Path to the configuration file (default: ./{DEFAULT_CONFIG_NAME})"
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help=f"Create a sample {DEFAULT_CONFIG_NAME}")
    p = sub.add_parser("inspect", help="Describe a procedure and its risks")
    p.add_argument("procedure")
    p.add_argument("--definition", action="store_true", help="Include the procedure text")
    p = sub.add_parser("suggest", help="Suggest parameter values for cases")
    p.add_argument("procedure")
    p = sub.add_parser("capture", help="Capture the current behavior as a baseline")
    p.add_argument("procedure")
    p.add_argument(
        "--cases", required=True, help='JSON file: [{"name": ..., "params": {...}}, ...]'
    )
    p.add_argument("--watch", nargs="*", default=[], help="Extra tables to compare (schema.table)")
    p.add_argument("--overwrite", action="store_true", help="Replace an existing baseline")
    p = sub.add_parser("verify", help="Compare the procedure against its baseline")
    p.add_argument("procedures", nargs="*", help="Procedures to verify (default: every baseline)")
    p.add_argument(
        "--markdown-report",
        metavar="PATH",
        help="Also write a Markdown report (for pull requests and CI job summaries)",
    )
    p = sub.add_parser("tsqlt", help="Generate tSQLt tests from a baseline")
    p.add_argument("procedure")
    sub.add_parser("list", help="List baselines")
    sub.add_parser("serve", help="Run the MCP server over stdio")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            target = Path(args.config or DEFAULT_CONFIG_NAME)
            if target.exists():
                print(f"{target} already exists; not touching it.")
                return 1
            target.write_text(SAMPLE_CONFIG, encoding="utf-8")
            print(
                f"Created {target}. Edit the connection and allowed_databases, then run 'sprocguard inspect <proc>'."
            )
            return 0
        if args.command == "serve":
            from .server import run

            run(args.config)
            return 0
        if args.command == "inspect":
            _print(service.inspect(args.procedure, args.definition, args.config), args.json)
        elif args.command == "suggest":
            _print(service.suggest(args.procedure, args.config), args.json)
        elif args.command == "capture":
            cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
            _print(
                service.capture_baseline(
                    args.procedure, cases, args.watch, args.overwrite, args.config
                ),
                args.json,
            )
        elif args.command == "verify":
            names = args.procedures or [b["procedure"] for b in service.list_baselines(args.config)]
            if not names:
                print("No baselines found. Capture one first.")
                return 1
            results = [service.verify(name, args.config) for name in names]
            if args.markdown_report:
                Path(args.markdown_report).write_text(to_markdown(results), encoding="utf-8")
            if args.json:
                _print(results, True)
            else:
                for result in results:
                    _print_verify(result)
            return 0 if all(r["passed"] for r in results) else 2
        elif args.command == "tsqlt":
            _print(service.generate_tsqlt(args.procedure, args.config), args.json)
        elif args.command == "list":
            baselines = service.list_baselines(args.config)
            if baselines or args.json:
                _print(baselines, args.json)
            else:
                print(
                    "No baselines yet. Capture one with 'sprocguard capture <proc> --cases cases.json'."
                )
        return 0
    except (ConfigError, SafetyError, CaptureError, InspectionError) as exc:
        print(f"sprocguard: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

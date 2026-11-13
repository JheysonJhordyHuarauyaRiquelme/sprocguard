"""Markdown report of ``verify`` results, for pull requests and CI job summaries."""

from __future__ import annotations

import json
import re
from typing import Any

_SPECIAL = re.compile(r"([\\`*_\[\]<>|#])")


def _escape(text: str) -> str:
    """Escape characters that Markdown would otherwise turn into formatting."""
    return _SPECIAL.sub(r"\\\1", str(text))


def _params(params: dict[str, Any]) -> str:
    if not params:
        return ""
    text = ", ".join(
        f"{name}={json.dumps(value, ensure_ascii=False, default=str)}"
        for name, value in params.items()
    )
    return " `" + text.replace("`", "'") + "`"


def to_markdown(results: list[dict[str, Any]]) -> str:
    """Render the results of one or more ``verify`` calls as Markdown."""
    total = len(results)
    failed = [r for r in results if not r["passed"]]
    lines = ["## sprocguard", ""]
    if failed:
        lines.append(
            f"**{len(failed)} of {total} procedure(s) changed behavior.** "
            "Review the differences below before merging."
        )
    else:
        lines.append(f"All {total} procedure(s) behave exactly as in their baselines.")
    lines += ["", "| Procedure | Result | Cases |", "| --- | --- | --- |"]
    for result in results:
        cases = result["cases"]
        ok = sum(1 for c in cases if c["passed"])
        status = "Same behavior" if result["passed"] else "**Changed**"
        lines.append(
            f"| `{result['procedure']}` | {status} | {ok}/{len(cases)} as in the baseline |"
        )

    for result in failed:
        lines += ["", f"### {_escape(result['procedure'])}", ""]
        if result.get("definition_changed"):
            captured = result.get("baseline_captured_at") or "an unknown date"
            lines.append(
                f"The procedure definition changed since the baseline was captured ({captured})."
            )
        for table in result.get("newly_written_tables", []):
            lines.append(f"It now writes `{table}`, which the baseline did not watch.")
        if lines[-1]:
            lines.append("")
        for case in result["cases"]:
            if case["passed"]:
                continue
            lines.append(f"- **{_escape(case['name'])}**{_params(case.get('params', {}))}")
            lines += [f"  - {_escape(diff)}" for diff in case["differences"]]

    return "\n".join(lines) + "\n"

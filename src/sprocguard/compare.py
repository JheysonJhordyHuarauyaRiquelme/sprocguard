"""Pure functions to diff table snapshots, learn volatility and compare runs.

A *run* is what one execution of a procedure produced::

    {
      "error": str | None,
      "return_value": Any,
      "outputs": {"@Param": value},
      "result_sets": [{"columns": [...], "rows": [[...], ...]}],
      "tables": {"dbo.Orders": {"key": [...], "inserted": [{...}], "deleted": [{...}],
                                "updated": [{"key": {...}, "changes": {"col": [old, new]}}]}}
    }

Capturing runs the same case twice. Anything that differs between the two runs
(dates, new IDs, random values) is marked *volatile* and ignored when verifying.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import Counter
from typing import Any

MAX_REPORTED_DIFFERENCES = 20


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


# --------------------------------------------------------------------------------------
# Table snapshots
# --------------------------------------------------------------------------------------


def diff_table(
    before: list[dict[str, Any]], after: list[dict[str, Any]], key_columns: list[str]
) -> dict[str, Any]:
    """Describe how a table changed between two snapshots."""
    if key_columns:

        def k(row: dict[str, Any]) -> str:
            return _key([row.get(c) for c in key_columns])

        old = {k(r): r for r in before}
        new = {k(r): r for r in after}
        inserted = [new[x] for x in new if x not in old]
        deleted = [old[x] for x in old if x not in new]
        updated = []
        for x in old:
            if x in new and old[x] != new[x]:
                changes = {
                    c: [old[x].get(c), new[x].get(c)]
                    for c in sorted(set(old[x]) | set(new[x]))
                    if old[x].get(c) != new[x].get(c)
                }
                updated.append({"key": {c: old[x].get(c) for c in key_columns}, "changes": changes})
    else:
        old_count = Counter(_key(r) for r in before)
        new_count = Counter(_key(r) for r in after)
        inserted = [json.loads(x) for x in (new_count - old_count).elements()]
        deleted = [json.loads(x) for x in (old_count - new_count).elements()]
        updated = []
    sort = lambda rows: sorted(rows, key=_key)  # noqa: E731
    return {
        "key": key_columns,
        "inserted": sort(inserted),
        "deleted": sort(deleted),
        "updated": sorted(updated, key=lambda u: _key(u["key"])),
    }


def table_changed(diff: dict[str, Any]) -> bool:
    return bool(diff.get("inserted") or diff.get("deleted") or diff.get("updated"))


# --------------------------------------------------------------------------------------
# Learning volatility from two runs
# --------------------------------------------------------------------------------------


def _strip(rows: list[list[Any]], drop: set[int]) -> list[list[Any]]:
    return [[v for j, v in enumerate(r) if j not in drop] for r in rows]


def _sorted(rows: list[list[Any]]) -> list[str]:
    return sorted(_key(r) for r in rows)


def learn_rows(a: list[list[Any]], b: list[list[Any]]) -> tuple[set[int], bool] | None:
    """Compare the same rows from two runs.

    Returns ``(volatile column indexes, order is stable)`` or ``None`` when the
    rows cannot be matched (different counts, or differences that are not just
    a few changing columns).
    """
    if len(a) != len(b):
        return None
    if a == b:
        return set(), True
    if _sorted(a) == _sorted(b):
        return set(), False
    width = max((len(r) for r in a), default=0)
    volatile = {
        j
        for i in range(len(a))
        for j in range(width)
        if j >= len(a[i]) or j >= len(b[i]) or a[i][j] != b[i][j]
    }
    sa, sb = _strip(a, volatile), _strip(b, volatile)
    if sa == sb:
        return volatile, True
    if _sorted(sa) == _sorted(sb):
        return volatile, False
    return None


def _dict_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[list[Any]]:
    return [[r.get(c) for c in columns] for r in rows]


def _columns_of(*row_lists: list[dict[str, Any]]) -> list[str]:
    cols: set[str] = set()
    for rows in row_lists:
        for r in rows:
            cols.update(r)
    return sorted(cols)


_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
CLOCK_MARGIN = _dt.timedelta(seconds=61)  # smalldatetime rounds to the minute


def _parse_time(value: Any) -> _dt.datetime | _dt.date | None:
    if not isinstance(value, str) or not _DATE_PREFIX.match(value):
        return None
    try:
        if len(value) == 10:
            return _dt.date.fromisoformat(value)
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_dt.UTC).replace(tzinfo=None)
    return parsed


def _clock_windows(run: dict[str, Any]) -> list[tuple[_dt.datetime, _dt.datetime]]:
    windows = []
    for start, end in (run.get("clock") or {}).values():
        s, e = _parse_time(start), _parse_time(end)
        if isinstance(s, _dt.datetime) and isinstance(e, _dt.datetime):
            windows.append((s - CLOCK_MARGIN, e + CLOCK_MARGIN))
    return windows


def _is_now(value: Any, windows: list[tuple[_dt.datetime, _dt.datetime]]) -> bool:
    """True when ``value`` looks like a timestamp taken while the procedure was running."""
    parsed = _parse_time(value)
    if parsed is None or not windows:
        return False
    if isinstance(parsed, _dt.datetime):
        return any(s <= parsed <= e for s, e in windows)
    return any(s.date() <= parsed <= e.date() for s, e in windows)


def clock_columns(run: dict[str, Any]) -> dict[str, Any]:
    """Columns holding the current date/time, which differ on every capture even if two runs agree.

    Two runs a few milliseconds apart produce the same value for a ``datetime2(0)`` or ``date``
    column, so comparing runs alone would miss them.
    """
    windows = _clock_windows(run)
    found: dict[str, Any] = {"scalars": set(), "result_sets": {}, "tables": {}}
    if not windows:
        return found
    if _is_now(run.get("return_value"), windows):
        found["scalars"].add("return_value")
    for name, value in (run.get("outputs") or {}).items():
        if _is_now(value, windows):
            found["scalars"].add(f"output:{name}")
    for i, rs in enumerate(run.get("result_sets", [])):
        cols = {
            rs["columns"][j] for row in rs["rows"] for j, v in enumerate(row) if _is_now(v, windows)
        }
        if cols:
            found["result_sets"][i] = cols
    for table, diff in (run.get("tables") or {}).items():
        if diff.get("skipped"):
            continue
        cols = {c for row in diff["inserted"] for c, v in row.items() if _is_now(v, windows)}
        cols |= {
            c
            for u in diff["updated"]
            for c, (_old, new) in u["changes"].items()
            if _is_now(new, windows)
        }
        if cols:
            found["tables"][table] = cols
    return found


def learn_volatility(run1: dict[str, Any], run2: dict[str, Any]) -> dict[str, Any]:
    """Build the expectation for a case from two runs of it."""
    learned = _learn_from_two_runs(run1, run2)
    for run in (run1, run2):
        clock = clock_columns(run)
        learned["volatile_scalars"] = sorted(set(learned["volatile_scalars"]) | clock["scalars"])
        for i, cols in clock["result_sets"].items():
            if i < len(learned["result_sets"]):
                meta = learned["result_sets"][i]
                meta["volatile_columns"] = sorted(set(meta["volatile_columns"]) | cols)
        for table, cols in clock["tables"].items():
            current = set(learned["volatile_table_columns"].get(table, []))
            learned["volatile_table_columns"][table] = sorted(current | cols)
    return learned


def _learn_from_two_runs(run1: dict[str, Any], run2: dict[str, Any]) -> dict[str, Any]:
    volatile_scalars: list[str] = []
    result_meta: list[dict[str, Any]] = []
    table_volatile: dict[str, list[str]] = {}
    problems: list[str] = []

    if run1.get("error") != run2.get("error"):
        if bool(run1.get("error")) != bool(run2.get("error")):
            problems.append("The procedure failed in one run and succeeded in the other.")
        volatile_scalars.append("error")
    if run1.get("return_value") != run2.get("return_value"):
        volatile_scalars.append("return_value")
    for name in sorted(set(run1.get("outputs", {})) | set(run2.get("outputs", {}))):
        if run1.get("outputs", {}).get(name) != run2.get("outputs", {}).get(name):
            volatile_scalars.append(f"output:{name}")

    sets1, sets2 = run1.get("result_sets", []), run2.get("result_sets", [])
    if len(sets1) != len(sets2):
        problems.append(
            f"The number of result sets changed between runs ({len(sets1)} vs {len(sets2)})."
        )
    for i, (s1, s2) in enumerate(zip(sets1, sets2, strict=False)):
        if s1["columns"] != s2["columns"]:
            problems.append(f"Result set #{i + 1} has different columns between runs.")
            result_meta.append({"volatile_columns": [], "ordered": False})
            continue
        learned = learn_rows(s1["rows"], s2["rows"])
        if learned is None:
            problems.append(
                f"Result set #{i + 1} returned different rows between runs "
                f"({len(s1['rows'])} vs {len(s2['rows'])}); it may depend on time, randomness "
                f"or TOP without ORDER BY."
            )
            result_meta.append({"volatile_columns": [], "ordered": False, "unstable": True})
            continue
        vol, ordered = learned
        result_meta.append(
            {"volatile_columns": [s1["columns"][j] for j in sorted(vol)], "ordered": ordered}
        )

    tables1, tables2 = run1.get("tables", {}), run2.get("tables", {})
    for table in sorted(set(tables1) | set(tables2)):
        t1, t2 = tables1.get(table), tables2.get(table)
        if t1 is None or t2 is None or t1.get("skipped") or t2.get("skipped"):
            continue
        vol_cols: set[str] = set()
        for part in ("inserted", "deleted"):
            cols = _columns_of(t1[part], t2[part])
            learned = learn_rows(_dict_rows(t1[part], cols), _dict_rows(t2[part], cols))
            if learned is None:
                problems.append(
                    f"{table}: {part} rows differ between runs "
                    f"({len(t1[part])} vs {len(t2[part])})."
                )
                continue
            vol_cols.update(cols[j] for j in learned[0])
        upd1 = {_key(u["key"]): u["changes"] for u in t1["updated"]}
        upd2 = {_key(u["key"]): u["changes"] for u in t2["updated"]}
        if set(upd1) != set(upd2):
            problems.append(f"{table}: different rows were updated between runs.")
        for k in set(upd1) & set(upd2):
            for col in set(upd1[k]) | set(upd2[k]):
                if upd1[k].get(col) != upd2[k].get(col):
                    vol_cols.add(col)
        if vol_cols:
            table_volatile[table] = sorted(vol_cols)

    return {
        "volatile_scalars": volatile_scalars,
        "result_sets": result_meta,
        "volatile_table_columns": table_volatile,
        "problems": problems,
    }


# --------------------------------------------------------------------------------------
# Verifying a new run against the expectation
# --------------------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + "..."


def _project(rows: list[dict[str, Any]], drop: set[str]) -> list[dict[str, Any]]:
    return [{c: v for c, v in r.items() if c not in drop} for r in rows]


def _multiset_diff(expected: list[Any], actual: list[Any]) -> tuple[list[Any], list[Any]]:
    e = Counter(_key(x) for x in expected)
    a = Counter(_key(x) for x in actual)
    missing = [json.loads(x) for x in (e - a).elements()]
    unexpected = [json.loads(x) for x in (a - e).elements()]
    return missing, unexpected


def compare_run(
    expected: dict[str, Any], learned: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    """Return human-readable differences between the expected run and a new one (empty = same behavior)."""
    diffs: list[str] = []
    vs = set(learned.get("volatile_scalars", []))

    exp_err, act_err = expected.get("error"), actual.get("error")
    if "error" in vs:
        if bool(exp_err) != bool(act_err):
            diffs.append(
                f"Error behavior changed: expected {'an error' if exp_err else 'success'}, "
                f"got {_fmt(act_err) if act_err else 'success'}."
            )
    elif exp_err != act_err:
        if exp_err and act_err:
            diffs.append(f"Error message changed: {_fmt(exp_err)} -> {_fmt(act_err)}")
        elif exp_err:
            diffs.append(f"Expected the error {_fmt(exp_err)}, but the procedure succeeded.")
        else:
            diffs.append(f"The procedure now fails: {_fmt(act_err)}")

    if "return_value" not in vs and expected.get("return_value") != actual.get("return_value"):
        diffs.append(
            f"Return value changed: {_fmt(expected.get('return_value'))} -> {_fmt(actual.get('return_value'))}"
        )
    for name, value in expected.get("outputs", {}).items():
        if f"output:{name}" not in vs and actual.get("outputs", {}).get(name) != value:
            diffs.append(
                f"Output parameter {name} changed: {_fmt(value)} -> {_fmt(actual.get('outputs', {}).get(name))}"
            )

    exp_sets, act_sets = expected.get("result_sets", []), actual.get("result_sets", [])
    if len(exp_sets) != len(act_sets):
        diffs.append(f"Number of result sets changed: {len(exp_sets)} -> {len(act_sets)}")
    metas = learned.get("result_sets", [])
    for i, (es, as_) in enumerate(zip(exp_sets, act_sets, strict=False)):
        label = f"Result set #{i + 1}"
        meta: dict[str, Any] = (
            metas[i] if i < len(metas) else {"volatile_columns": [], "ordered": False}
        )
        if meta.get("unstable"):
            continue
        if es["columns"] != as_["columns"]:
            diffs.append(
                f"{label}: columns changed {_fmt(es['columns'])} -> {_fmt(as_['columns'])}"
            )
            continue
        drop_indexes = {
            j for j, c in enumerate(es["columns"]) if c in set(meta.get("volatile_columns", []))
        }
        cols = [c for j, c in enumerate(es["columns"]) if j not in drop_indexes]
        er, ar = _strip(es["rows"], drop_indexes), _strip(as_["rows"], drop_indexes)
        if len(er) != len(ar):
            diffs.append(f"{label}: row count changed {len(er)} -> {len(ar)}")
        if meta.get("ordered"):
            for r, (x, y) in enumerate(zip(er, ar, strict=False)):
                if x != y:
                    changed = {
                        cols[j]: [x[j], y[j]] for j in range(min(len(x), len(y))) if x[j] != y[j]
                    }
                    diffs.append(
                        f"{label}, row {r + 1}: "
                        + ", ".join(f"{c} {_fmt(o)} -> {_fmt(n)}" for c, (o, n) in changed.items())
                    )
            if len(er) == len(ar) and er != ar and _sorted(er) == _sorted(ar):
                diffs.append(f"{label}: same rows, different order")
        else:
            missing, unexpected = _multiset_diff(er, ar)
            for row in missing:
                diffs.append(f"{label}: missing row {_fmt(dict(zip(cols, row, strict=True)))}")
            for row in unexpected:
                diffs.append(f"{label}: unexpected row {_fmt(dict(zip(cols, row, strict=True)))}")

    exp_tables, act_tables = expected.get("tables", {}), actual.get("tables", {})
    for table in sorted(set(exp_tables) | set(act_tables)):
        et = exp_tables.get(table) or {"inserted": [], "deleted": [], "updated": []}
        at = act_tables.get(table) or {"inserted": [], "deleted": [], "updated": []}
        if et.get("skipped") or at.get("skipped"):
            continue
        drop_columns: set[str] = set(learned.get("volatile_table_columns", {}).get(table, []))
        for part, verb in (("inserted", "inserts"), ("deleted", "deletes")):
            missing, unexpected = _multiset_diff(
                _project(et[part], drop_columns), _project(at[part], drop_columns)
            )
            if missing or unexpected:
                if len(et[part]) != len(at[part]):
                    diffs.append(
                        f"{table}: expected {len(et[part])} {part} row(s), got {len(at[part])}"
                    )
                for row in missing[:5]:
                    diffs.append(f"{table}: no longer {verb} {_fmt(row)}")
                for row in unexpected[:5]:
                    diffs.append(f"{table}: now {verb} {_fmt(row)}")
        eu = {
            _key(u["key"]): {c: v for c, v in u["changes"].items() if c not in drop_columns}
            for u in et["updated"]
        }
        au = {
            _key(u["key"]): {c: v for c, v in u["changes"].items() if c not in drop_columns}
            for u in at["updated"]
        }
        eu = {k: v for k, v in eu.items() if v}
        au = {k: v for k, v in au.items() if v}
        for k in sorted(set(eu) | set(au)):
            if eu.get(k) != au.get(k):
                if k not in au:
                    diffs.append(f"{table}: row {k} is no longer updated (expected {_fmt(eu[k])})")
                elif k not in eu:
                    diffs.append(f"{table}: row {k} is now updated: {_fmt(au[k])}")
                else:
                    for col in sorted(set(eu[k]) | set(au[k])):
                        if eu[k].get(col) != au[k].get(col):
                            exp_new = eu[k].get(col, [None, "(unchanged)"])[1]
                            act_new = au[k].get(col, [None, "(unchanged)"])[1]
                            diffs.append(
                                f"{table}: row {k}, column {col}: expected new value "
                                f"{_fmt(exp_new)}, got {_fmt(act_new)}"
                            )

    if len(diffs) > MAX_REPORTED_DIFFERENCES:
        extra = len(diffs) - MAX_REPORTED_DIFFERENCES
        diffs = [*diffs[:MAX_REPORTED_DIFFERENCES], f"... and {extra} more difference(s)"]
    return diffs

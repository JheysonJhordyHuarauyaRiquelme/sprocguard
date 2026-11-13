---
name: sprocguard
description: Use before changing, refactoring, optimizing or fixing a SQL Server stored procedure (T-SQL, CREATE/ALTER PROCEDURE, usp_*, sp_* files). Captures what the procedure does today and verifies the change did not alter its behavior by accident. Requires the sprocguard MCP server.
---

# sprocguard: change stored procedures without breaking them

Stored procedures often hold business rules that nobody has tests for. Before you touch one,
record what it does today; after the change, prove that only the intended behavior changed.

## When to use

- The user asks you to modify, refactor, optimize, clean up or fix a stored procedure.
- You are about to run `ALTER PROCEDURE` / `CREATE OR ALTER PROCEDURE` on a development database.
- You changed a table, view or function that a procedure uses (verify the procedures that use it).

Do **not** use it against production. sprocguard refuses databases that are not listed in
`sprocguard.toml` under `allowed_databases`.

## Workflow

1. **Inspect** – call `inspect_procedure`.
   - If `risks` contains a `block` item, stop and tell the user why (e-mail, files, linked
     servers or table-valued parameters cannot be checked safely).
   - Read `warn` items aloud to the user in one line each (dynamic SQL means some table changes
     may not be compared: pass those tables in `watch_tables`).
   - If `has_baseline` is true, skip to step 4 unless the user wants new cases.

2. **Design cases** – call `suggest_cases`, then read the procedure definition
   (`inspect_procedure` with `include_definition=true`, or the .sql file in the repo).
   Build roughly `suggested_case_count` cases so that together they:
   - go through every `IF`/`CASE` branch,
   - trigger every `THROW`/`RAISERROR` (errors are behavior too),
   - include an "empty" case (no matching rows) and boundary values,
   - use real values from `real_values` so the procedure finds data.
   Give each case a short descriptive name, e.g. `"insufficient stock raises error"`.

3. **Capture BEFORE editing** – call `capture_baseline` with the cases.
   Report the `volatile` columns it detected (dates, generated IDs) and any `warnings`.
   The baseline file belongs in the repository: suggest committing `.sprocguard/`.

4. **Make the change** – edit the procedure and deploy it to the development database
   (`CREATE OR ALTER PROCEDURE ...`).

5. **Verify** – call `verify_procedure`.
   - All cases passed: tell the user the behavior is unchanged (say how many cases).
   - Some cases differ: show the differences. Decide with the user whether each one is
     **intended** (the point of the change) or a **regression**. Fix regressions and verify again.
   - Never edit the baseline file by hand to make verify pass.

6. **Accept intended changes** – only after the user agrees that the new behavior is correct,
   call `capture_baseline` again with `overwrite=true`, and mention it in the commit message.

7. **Optional: CI** – for read-only procedures, call `generate_tsqlt_tests` to produce tSQLt
   tests. For any procedure, `sprocguard verify` in a pipeline exits with code 2 when behavior changed.

## How to report results

Keep it short and concrete, for example:

> Baseline: 6 cases for dbo.usp_PlaceOrder (OrderDate and OrderId vary per run and are ignored).
> After the change: 5/6 cases unchanged. "10% discount" now returns Total 135.00 instead of 135.50:
> the new ROUND() changed the rounding. Is that intended?

## Safety notes

- Every execution runs inside a transaction that sprocguard always rolls back.
- Things a rollback cannot undo (e-mails, files, jobs, linked servers) are blocked by default.
- Captures hold locks on the tables involved for a few seconds: use a development database.

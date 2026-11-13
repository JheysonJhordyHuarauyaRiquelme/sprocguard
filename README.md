# sprocguard

**A safety net for SQL Server stored procedures.**
Capture what a procedure does *today*, let a human or an AI agent change it, and get a clear
report of every behavior that changed — before it reaches production.

[Leer en español](README.es.md)

```text
$ sprocguard verify dbo.usp_PlaceOrder
FAIL  dbo.usp_PlaceOrder: 3/5 case(s) behave exactly as in the baseline.
      (the procedure definition changed since the baseline was captured)
  ok  regular customer buys 2 coffees
  XX  VIP customer gets 10% off
        - Result set #1, row 1: Total "33.75" -> "33.80"
        - dbo.Orders: no longer inserts {"CustomerId": 1, "Total": "33.75"}
        - dbo.Orders: now inserts {"CustomerId": 1, "Total": "33.80"}
  XX  VIP discount with odd cents
        - Result set #1, row 1: Total "78.75" -> "78.80"
        - dbo.Orders: no longer inserts {"CustomerId": 1, "Total": "78.75"}
        - dbo.Orders: now inserts {"CustomerId": 1, "Total": "78.80"}
  ok  buys exactly the last units
  ok  insufficient stock raises error
```

## Why

Many companies keep their business rules inside stored procedures, usually without tests.
AI coding agents are now asked to "clean up" or "optimize" them, and a refactor that looks
equivalent can quietly change a rounding rule, an error path or a table it updates.

sprocguard gives you **characterization tests** (also called *golden master* tests) in minutes:
it records what the procedure returns and what it changes in the database, then compares any
new version against that record.

## What it captures

For every case (a set of parameter values):

- every **result set**, in order when the order is stable;
- the **return value** and **OUTPUT parameters**;
- the **error** raised, if any (errors are behavior too);
- the **rows inserted, updated and deleted** in each table the procedure writes,
  including tables written by procedures it calls and by triggers.

Each case runs **twice**. Values that change by themselves (new IDENTITY values, `NEWID()`, and any
date or time taken while the procedure was running, even at second or day precision) are detected
automatically and ignored when verifying.

## Safety model

sprocguard executes your procedures, so safety comes first:

| Protection | How |
|---|---|
| Nothing is ever committed | Each execution runs inside a transaction that is always rolled back, with `IMPLICIT_TRANSACTIONS ON` and two nesting levels, so an inner `ROLLBACK` or a stray `COMMIT` in the procedure cannot leak changes. |
| Only development databases | It refuses to connect to any database not listed in `allowed_databases`. |
| No irreversible side effects | Procedures that send e-mail, run `xp_cmdshell`, start jobs, use `OPENROWSET`/`OPENQUERY` or linked servers are refused by default: a rollback cannot undo them. |
| Big tables are not dumped | Tables above `max_rows_per_table` are not snapshotted (reported as skipped). |
| SQL injection | Parameter values are always sent as query parameters, never concatenated. |

Captures hold locks on the tables involved for a moment: use a development database.

## Install

Requires Python 3.11+ and the [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
(usually already installed with SQL Server or SSMS).

```bash
pip install git+https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard
# SQL authentication without ODBC:  pip install "sprocguard[pymssql] @ git+https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard"
```

## Quick start (5 minutes, with the demo database)

```bash
sqlcmd -S localhost -E -i examples/demo.sql        # creates DemoShop with two procedures
sprocguard init                                     # writes sprocguard.toml; edit the connection
sprocguard inspect dbo.usp_PlaceOrder               # parameters, tables, risks
sprocguard capture dbo.usp_PlaceOrder --cases examples/cases/usp_PlaceOrder.json
sqlcmd -S localhost -E -i examples/regression.sql  # a "harmless" refactor
sprocguard verify dbo.usp_PlaceOrder                # catches the rounding change (exit code 2)
```

`sprocguard.toml`:

```toml
[connection]
odbc = "Driver={ODBC Driver 18 for SQL Server};Server=localhost;Database=DemoShop;Trusted_Connection=yes;TrustServerCertificate=yes"

[safety]
allowed_databases = ["DemoShop"]
max_rows_per_table = 5000
allow_external_side_effects = false

[storage]
baseline_dir = ".sprocguard/baselines"   # commit this folder
tsqlt_dir = ".sprocguard/tsqlt"
```

## Use it with AI agents (MCP)

sprocguard ships an MCP server and an [Agent Skill](skills/sprocguard/SKILL.md) that teaches
agents the workflow: *inspect → design cases → capture → change → verify*.

**Claude Code** (plugin with the MCP server and the skill):

```text
/plugin marketplace add JheysonJhordyHuarauyaRiquelme/sprocguard
/plugin install sprocguard@sprocguard
```

**Any MCP client** (VS Code, Cursor, Claude Desktop...):

```json
{ "mcpServers": { "sprocguard": { "command": "sprocguard", "args": ["serve"] } } }
```

| Tool | What it does | Read-only |
|---|---|---|
| `inspect_procedure` | Parameters, tables read/written, procedures and triggers involved, risks | yes |
| `suggest_cases` | Real parameter values from related tables plus edge values | yes |
| `capture_baseline` | Records the current behavior (refuses to overwrite unless asked) | writes a file |
| `verify_procedure` | Re-runs the cases and lists every difference | yes |
| `generate_tsqlt_tests` | Turns a baseline into tSQLt tests | writes a file |
| `list_baselines` | Lists captured baselines | yes |

## CLI

```text
sprocguard init                                   create sprocguard.toml
sprocguard inspect <proc> [--definition]          describe a procedure and its risks
sprocguard suggest <proc>                         suggest parameter values
sprocguard capture <proc> --cases cases.json      capture a baseline  [--watch schema.table ...] [--overwrite]
sprocguard verify [<proc> ...]                    verify one, several or all baselines (exit 2 if behavior changed)
sprocguard tsqlt <proc>                           generate tSQLt tests (read-only procedures)
sprocguard list                                   list baselines
sprocguard serve                                  run the MCP server (stdio)
```

Add `--json` for machine-readable output. In CI, run `sprocguard verify` against a test
database restored from a known backup: the job fails when any procedure changed behavior.

## tSQLt export

For read-only procedures, `sprocguard tsqlt <proc>` writes a `.sql` file with one
[tSQLt](https://tsqlt.org) test per case, using `tSQLt.AssertEqualsTable` and
`tSQLt.ExpectException`. Volatile columns are left out of the comparison.

## Limitations (0.1)

- Table-valued parameters are not supported yet.
- Tables changed only through dynamic SQL are not detected automatically; add them with `--watch`.
- If the procedure fails, result sets returned before the error are not recorded (the error is).
- tSQLt export covers read-only procedures; procedures that modify data use `verify`.
- Comparisons ignore the order of rows when the order was not stable between the two capture runs.

## Roadmap

- [ ] tSQLt export with `FakeTable` for procedures that modify data
- [ ] Table-valued parameters
- [ ] `sprocguard impact <table>`: verify every procedure that uses a table you are changing
- [ ] GitHub Action
- [ ] Publish to PyPI and the MCP Registry

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite uses an in-memory imitation of SQL Server (`tests/fakedb.py`), so it runs
anywhere; real-database testing uses `examples/demo.sql`.

## License

MIT

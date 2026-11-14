# sprocguard

<!-- mcp-name: io.github.JheysonJhordyHuarauyaRiquelme/sprocguard -->

[![PyPI](https://img.shields.io/pypi/v/sprocguard.svg)](https://pypi.org/project/sprocguard/)
[![CI](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/ci.yml/badge.svg)](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/ci.yml)
[![Integration](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/integration.yml/badge.svg)](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/integration.yml)
[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-sprocguard%20verify-blue?logo=github)](https://github.com/marketplace/actions/sprocguard-verify)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/LICENSE)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/sprocguard.svg)](https://pypi.org/project/sprocguard/)

**A safety net for SQL Server stored procedures.**
Capture what a procedure does *today*, let a human or an AI agent change it, and get a clear
report of every behavior that changed — before it reaches production.

[Leer en español](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/README.es.md)

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

## Three ways to use it

| Where | How | For |
| --- | --- | --- |
| Terminal | `pip install sprocguard` ([CLI](#cli)) | Checking a change on your machine |
| AI agents | MCP server and Claude Code plugin ([MCP](#use-it-with-ai-agents-mcp)) | Letting an agent verify its own SQL edits |
| Pull requests | [GitHub Action](#verify-on-every-pull-request-github-action), also on the [GitHub Marketplace](https://github.com/marketplace/actions/sprocguard-verify) | Blocking a merge that changes behavior |

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

Requires Python 3.11+ and the [Microsoft ODBC Driver 17 or 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
(usually already installed with SQL Server or SSMS). Check which one you have with
`Get-OdbcDriver | Where-Object Name -like "*SQL Server*"` in PowerShell.

```bash
pip install sprocguard
# SQL authentication without ODBC:  pip install "sprocguard[pymssql]"
```

## Quick start (5 minutes, with the demo database)

The demo scripts live in the [`examples`](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/tree/main/examples) folder of the repository:

```bash
git clone https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard.git
cd sprocguard
```

```bash
sqlcmd -S localhost -E -i examples/demo.sql        # creates DemoShop with two procedures
sprocguard init                                     # writes sprocguard.toml; edit the connection
sprocguard inspect dbo.usp_PlaceOrder               # parameters, tables, risks
sprocguard capture dbo.usp_PlaceOrder --cases examples/cases/usp_PlaceOrder.json
sqlcmd -S localhost -E -i examples/regression.sql  # a "harmless" refactor
sprocguard verify dbo.usp_PlaceOrder                # catches the rounding change (exit code 2)
```

`sprocguard.toml` (use `ODBC Driver 17 for SQL Server` if that is the one you have):

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

sprocguard ships an MCP server and an [Agent Skill](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/skills/sprocguard/SKILL.md) that teaches
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

With [uv](https://docs.astral.sh/uv/) you can skip the install step: use `"command": "uvx", "args": ["sprocguard", "serve"]`.
sprocguard is also listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as
`io.github.JheysonJhordyHuarauyaRiquelme/sprocguard`.

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
                                                  [--markdown-report report.md]
                                                  [--only-failures]
sprocguard tsqlt <proc>                           generate tSQLt tests (read-only procedures)
sprocguard list                                   list baselines
sprocguard serve                                  run the MCP server (stdio)
```

Add `--json` for machine-readable output.

Use `sprocguard verify --only-failures` to keep passing procedures to one `PASS` line
and show only failing cases with their differences for changed procedures. JSON output
and Markdown reports are unaffected.

## Verify on every pull request (GitHub Action)

This repository is also a GitHub Action, listed on the
[GitHub Marketplace](https://github.com/marketplace/actions/sprocguard-verify). It starts a throwaway SQL Server in Docker, builds
your database from the scripts in your repository, and runs `sprocguard verify` against the
baselines you committed. The pull request fails when a procedure changed behavior, and the
job summary lists every difference.

```yaml
name: Stored procedures
on: pull_request
permissions:
  contents: read
jobs:
  sprocguard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: JheysonJhordyHuarauyaRiquelme/sprocguard@v0.2.0
        with:
          setup-scripts: |
            db/schema.sql
            db/seed.sql
            db/procedures/*.sql
```

To set it up:

1. Keep scripts that build a development copy of your database (tables, test data and
   procedures) in the repository. The action runs them in order with `sqlcmd`.
2. Build that database on your machine from the same scripts and capture the baselines
   there, so they describe the same data the action will build.
3. Commit the baselines (`.sprocguard/baselines`) and a sprocguard configuration file. The
   action replaces its `[connection]` with the throwaway server, so it does not need your
   real connection string; point `config` at a separate file such as `sprocguard.ci.toml`
   if you prefer. The `[safety]` rules still apply.

| Input | Default | What it does |
| --- | --- | --- |
| `config` | `sprocguard.toml` | Configuration file in your repository |
| `setup-scripts` | | Scripts that build the database, one per line; globs allowed |
| `procedures` | every baseline | Procedures to verify, separated by spaces or new lines |
| `database` | first of `allowed_databases` | Database to create and verify |
| `verify` | `true` | Set to `false` to only prepare the server for your own `sprocguard` steps |
| `start-sql-server` | `true` | Set to `false` to reuse the server started by an earlier step |
| `sql-server-image` | `mcr.microsoft.com/mssql/server:2022-latest` | SQL Server image |

Outputs: `passed` (`true` or `false`) and `report` (path to the Markdown report). The action
needs a Linux runner with Docker, such as `ubuntu-latest`. It connects with pymssql; if a
baseline captured with ODBC differs only in how a value is formatted, please open an issue.
The [integration workflow](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/.github/workflows/integration.yml)
of this repository uses the action on the demo database and is a complete example.

To run sprocguard against another server in CI, set environment variables instead of
editing the configuration: `SPROCGUARD_ODBC` (an ODBC connection string), or
`SPROCGUARD_SERVER`, `SPROCGUARD_PORT`, `SPROCGUARD_USER`, `SPROCGUARD_DATABASE` and
`SPROCGUARD_PASSWORD` (a pymssql connection).

## tSQLt export

For read-only procedures, `sprocguard tsqlt <proc>` writes a `.sql` file with one
[tSQLt](https://tsqlt.org) test per case, using `tSQLt.AssertEqualsTable` and
`tSQLt.ExpectException`. Volatile columns are left out of the comparison.

## Limitations

- Table-valued parameters are not supported yet.
- Tables changed only through dynamic SQL are not detected automatically; add them with `--watch`.
- If the procedure fails, result sets returned before the error are not recorded (the error is).
- tSQLt export covers read-only procedures; procedures that modify data use `verify`.
- Comparisons ignore the order of rows when the order was not stable between the two capture runs.

## Roadmap

- [ ] tSQLt export with `FakeTable` for procedures that modify data
- [ ] Table-valued parameters
- [ ] `sprocguard impact <table>`: verify every procedure that uses a table you are changing
- [ ] Verify a pull request without committed baselines: capture on the base branch, verify on the head branch
- [ ] PostgreSQL support
- [x] Publish to PyPI and the MCP Registry
- [x] GitHub Action that verifies procedures on every pull request
- [x] List the GitHub Action on the GitHub Marketplace

## Contributing

Contributions are welcome, in English or Spanish. See [CONTRIBUTING.md](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/CONTRIBUTING.md)
to set up the project, run the checks and open a pull request, and the
[changelog](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/CHANGELOG.md) for what changed in each version.
Security problems: please report them privately as described in [SECURITY.md](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/blob/main/SECURITY.md).

The unit tests use an in-memory imitation of SQL Server (`tests/fakedb.py`), so they run
anywhere. The integration workflow runs the demo in `examples/` against a real SQL Server
on every change.

## License

MIT

# Contributing to sprocguard

Thanks for helping. Bug reports, ideas and pull requests are all welcome.
Issues and discussions can be in English or Spanish.

## Set up

```bash
git clone https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard.git
cd sprocguard
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

You do not need SQL Server to run the test suite: the tests use an in-memory fake
(`tests/fakedb.py`). To try sprocguard against a real server, follow the
quick start in the README with `examples/demo.sql`.

## Before opening a pull request

Run the same checks as CI:

```bash
ruff check src tests
ruff format src tests
mypy
pytest --cov
```

- Add or update tests for any change in behavior. CI fails if coverage drops below the floor set in `pyproject.toml`.
- Keep pull requests small and focused on one change.
- Add a line under `[Unreleased]` in `CHANGELOG.md`.
- Use clear commit messages, for example `feat: support views`, `fix: ...`, `docs: ...`, `test: ...`.

## Safety rules for new features

sprocguard runs code against people's databases, so these rules are not negotiable:

1. Everything sprocguard executes must run inside the protective transaction and be rolled back.
2. Only databases listed in `allowed_databases` may be touched.
3. Anything that can reach outside the database (mail, `xp_cmdshell`, linked servers) stays blocked by default.

## Reporting bugs

Open an issue with the bug template. Include the sprocguard version (`sprocguard --version`),
your SQL Server and ODBC driver versions, and the smallest procedure that reproduces the problem.
Never paste real customer data or connection strings with passwords.

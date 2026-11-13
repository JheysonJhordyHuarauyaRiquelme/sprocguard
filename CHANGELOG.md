# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Linting and formatting with ruff, type checking with mypy, and a coverage floor in CI.
- pre-commit hooks that run the same checks before each commit.
- Dependabot updates for Python dependencies and GitHub Actions.
- Contributing guide, security policy, issue templates and pull request template.
- End-to-end tests for the command-line interface.

### Changed
- Every `zip()` call now states whether both sides must have the same length.

## [0.1.1] - 2026-11-12

### Added
- Published to PyPI (`pip install sprocguard`) and listed in the official MCP Registry.
- Release workflow with PyPI Trusted Publishing.

## [0.1.0] - 2026-11-12

### Added
- First release: `inspect`, `suggest`, `capture`, `verify`, `tsqlt`, `list` and `serve` commands.
- MCP server with six tools, an Agent Skill and a Claude Code plugin.
- Every run happens inside a transaction that is always rolled back.

[Unreleased]: https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/releases/tag/v0.1.0

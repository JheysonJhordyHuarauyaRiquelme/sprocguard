# Security policy

## Supported versions

Security fixes go into the latest release on PyPI.

## Reporting a vulnerability

Please do not open a public issue for security problems.
Use GitHub's private reporting instead: open the **Security** tab of this repository and choose
**Report a vulnerability**.

Examples of what counts as a security issue here:

- a way for a procedure run by sprocguard to commit changes instead of being rolled back;
- a way to reach a database that is not listed in `allowed_databases`;
- a way to run blocked operations (mail, `xp_cmdshell`, linked servers) without opting in;
- credentials leaking into baselines, logs or MCP responses.

You will get an answer within 7 days. Once a fix is released, the report is credited
in the changelog unless you prefer to stay anonymous.

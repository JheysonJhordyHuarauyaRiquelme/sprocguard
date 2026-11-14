#!/usr/bin/env bash
# Runs T-SQL scripts with the sqlcmd inside the sprocguard-sql container started by action.yml.
#
# Usage: run-scripts.sh <folder> <patterns>
#   <folder>    folder the patterns are relative to (the repository, or the base-branch copy)
#   <patterns>  one file or glob pattern per line, run in order (db/procedures/*.sql)
#
# Needs SPROCGUARD_SQLCMD and SPROCGUARD_PASSWORD (set by the "Start SQL Server" step)
# and DATABASE (the database the scripts run in).
set -euo pipefail

root="$1"
patterns="$2"

if [ -z "${SPROCGUARD_SQLCMD:-}" ]; then
  echo "::error title=sprocguard::Running scripts needs the SQL Server started by this action (start-sql-server: true in this or an earlier step)."
  exit 1
fi

cd "$root"
shopt -s nullglob globstar
while IFS= read -r pattern; do
  pattern="$(echo "$pattern" | xargs)"
  [ -z "$pattern" ] && continue
  # Intentionally unquoted so glob patterns expand; nullglob drops non-matches.
  # shellcheck disable=SC2206
  files=( $pattern )
  if [ ${#files[@]} -eq 0 ]; then
    echo "::error title=sprocguard::No file matches '$pattern' in $root."
    exit 1
  fi
  for file in "${files[@]}"; do
    echo "::group::$file"
    docker cp "$file" sprocguard-sql:/tmp/sprocguard-script.sql
    # -I: QUOTED_IDENTIFIER ON, like SSMS. -f 65001: read the script as UTF-8.
    docker exec sprocguard-sql "$SPROCGUARD_SQLCMD" -S localhost -U sa \
      -P "$SPROCGUARD_PASSWORD" -C -b -I -f 65001 -d "$DATABASE" \
      -i /tmp/sprocguard-script.sql
    echo "::endgroup::"
  done
done <<< "$patterns"

#!/usr/bin/env bash
# Start the box-local portable Postgres (if not running) and the MCP server on $PORT.
set -euo pipefail
cd "$(dirname "$0")/.."
PG=.local/pg/bin
"$PG/pg_ctl" -D .local/data status >/dev/null 2>&1 || \
  "$PG/pg_ctl" -D .local/data -l .local/pg.log -o "-p 5432 -k /tmp -c listen_addresses=127.0.0.1" -w start
set -a; . ./.env; set +a
exec .venv/bin/python -m app.server

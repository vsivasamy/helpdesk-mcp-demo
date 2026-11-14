# IT Helpdesk MCP server (demo)

A small [MCP](https://modelcontextprotocol.io) server over **Streamable HTTP** that exposes a fake IT helpdesk
(Postgres on Neon) to AI agents. It is read-only by default. Every person, email (`@example.com`) and ticket is synthetic.

**Live URL (no auth, read-only):**
[https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp](https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp)

`GET /health` is liveness and never touches the database. `GET /health/db` does a database round-trip. The first request after idle takes about 30 seconds (the app scales to zero); after that, about 2 seconds. Hit `/health` first to warm it. How to connect common clients: [docs/CONNECT.md](docs/CONNECT.md).

- Python, official `mcp` SDK v2 (`MCPServer`, the successor to v1's `FastMCP`), stateless JSON mode at `POST /mcp`
- **Auth is optional:** no `MCP_API_KEY` means no auth (the live demo; a warning is logged). If set, `Authorization: Bearer <key>` is required (constant-time compare, `401` otherwise)
- **DNS-rebinding protection:** Host/Origin allow-list with wildcards (`ALLOWED_HOSTS`, default `localhost,127.0.0.1,[::1],*.azurecontainerapps.io`)
- Reads use the read-only role `helpdesk_ro`. Every query runs in a `READ ONLY` transaction with `SET LOCAL statement_timeout`. That is pooler-safe (Neon PgBouncer) and works with `sslmode=require`
- Parameterised SQL; validated inputs (enums, lengths, `limit` 1-50, date ranges)
- Seeded with 400 tickets (30 open) in Neon project `helpdesk-demo`. Writes stay off (`ENABLE_WRITES=false`)

## Tools
| Tool | Purpose |
|---|---|
| `search_tickets` | Free-text search with optional `status`, `priority`, `category`, `assignee`; newest first, max 50 |
| `get_ticket` | One ticket by id or `INC000123` ref, with SLA flags and comment thread |
| `ticket_stats` | Counts by status/priority/category, avg first response and resolution time, SLA breach rate; optional date range |
| `list_open_by_priority` | Unresolved tickets grouped by priority, oldest first, with counts |
| `sla_breaches` | Response and/or resolution SLA breaches, most overdue first |
| `create_ticket` / `update_status` | Registered **only** when `ENABLE_WRITES=true` (use the `helpdesk_rw` role). Not exposed on the live demo |

## Layout
```
app/server.py              MCP server (tools, auth, host guard, pooler-safe DB layer)
db/schema.sql, roles.sql   schema (idempotent), helpdesk_ro / helpdesk_rw roles
db/seed.py                 Faker data, fixed seed, SEED_TICKETS (default 400)
scripts/seed_neon.py       seed/check Neon from .neon.env (auto WebSocket bridge if 5432 is blocked)
scripts/neon_ws_bridge.py  local-only TCP to wss bridge for HTTPS-only networks
scripts/with_env.py        run a command with dotenv files (safe with '&' in URLs)
scripts/dev_up.sh          box-local Postgres + server
tests/smoke_client.py      MCP-client end-to-end test (both auth modes, host guard, every tool)
Dockerfile                 python:3.13-slim, non-root, HOST=0.0.0.0 PORT=8000
.github/workflows/build-and-push.yml   build linux/amd64 and push to GHCR (+ optional ACA deploy)
docs/                      CONNECT, DEPLOY, GHCR-EXPLAINED, ARCHITECTURE, DEMO-SCRIPT, TEARDOWN
```

## Run locally
```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env          # fill in
.venv/bin/python scripts/with_env.py .env -- .venv/bin/python db/seed.py
.venv/bin/python scripts/with_env.py .env -- .venv/bin/python -m app.server
.venv/bin/python scripts/with_env.py .env -- .venv/bin/python tests/smoke_client.py
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp --transport http --method tools/list
```

Deploying is covered in [docs/DEPLOY.md](docs/DEPLOY.md), connecting clients in [docs/CONNECT.md](docs/CONNECT.md), the talk track in [docs/DEMO-SCRIPT.md](docs/DEMO-SCRIPT.md), and removal in [docs/TEARDOWN.md](docs/TEARDOWN.md).

# Architecture

Live endpoint: `https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp` (Streamable HTTP, no auth, read-only).

```mermaid
flowchart LR
  subgraph Clients
    I[MCP Inspector<br/>CLI / web UI]
    A[AI agent / IDE<br/>Claude, Cursor, ChatGPT, VS Code]
  end
  subgraph GitHub
    R[repo helpdesk-mcp-demo] -- push to main --> GA[GitHub Actions<br/>build-and-push.yml]
    GA -- docker push --> G[(GHCR<br/>ghcr.io/vsivasamy/helpdesk-mcp-demo<br/>public)]
  end
  subgraph Azure["Azure Pay-As-You-Go - rg-mcp-demo, southeastasia (budget ₹100/month)"]
    ENV[Container Apps env cae-mcp-demo<br/>Consumption, logs: none] --> CA["Container App helpdesk-mcp<br/>0.25 vCPU / 0.5 GiB<br/>min 0 / max 1 replica<br/>ingress :8000 HTTPS"]
  end
  subgraph Neon["Neon free - project helpdesk-demo (aws-ap-southeast-1)"]
    PB[PgBouncer pooler] --> PG[(Postgres 17<br/>400 tickets, 30 open<br/>scale-to-zero after 5 min)]
  end
  I -- "HTTPS POST /mcp<br/>Streamable HTTP (stateless JSON)" --> CA
  A -- same --> CA
  G -- anonymous image pull --> CA
  CA -- "TLS 5432, role helpdesk_ro<br/>READ ONLY txn, 5 s timeout" --> PB
```

## Request path
1. Client sends JSON-RPC (`initialize`, `tools/list`, `tools/call`) to `https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp`.
2. If no replica is running, Container Apps holds the request while one starts (cold start, about 30 seconds). Warm requests take about 2 seconds. Hit `/health` first to wake the app; `/health/db` also wakes Neon.
3. `HostGuard` checks Host/Origin against `ALLOWED_HOSTS` (DNS-rebinding protection); optional `BearerAuth` checks `MCP_API_KEY` (unset on the live demo).
4. The MCP SDK (`MCPServer`, stateless Streamable HTTP) validates arguments against each tool's schema.
5. Each SQL query runs in its own transaction: `SET TRANSACTION READ ONLY`, `SET LOCAL statement_timeout`, `SET LOCAL search_path` and then parameterised SQL. That works through Neon's PgBouncer transaction pooling.
6. Neon resumes the compute if it was suspended (a few hundred ms) and answers.
7. After 300 s without requests, Container Apps scales to zero. After 5 min idle, Neon suspends. The idle cost is then storage only.

## Security layers (demo-grade)
| Layer | Control |
|---|---|
| Data | Entirely synthetic (`@example.com`), so a leak exposes nothing real |
| DB | `helpdesk_ro` has SELECT only, `default_transaction_read_only=on` and a 5 s timeout. The server also forces read-only transactions |
| App | Input validation (enums, lengths, limit ≤ 50, date checks); no dynamic SQL from input |
| Transport | HTTPS from Container Apps ingress; Host/Origin allow-list |
| Auth | None on the live demo. Set `MCP_API_KEY` for a shared bearer token. For production, use OAuth 2.1 per the MCP authorisation spec (e.g. Entra ID) |
| Writes | `create_ticket` and `update_status` are not registered (`ENABLE_WRITES=false`) |
| Secrets | `DATABASE_URL` is the Neon read-only pooled URL, stored as the Container Apps secret `database-url` |

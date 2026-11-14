# Connect a client to the live demo

**URL:** `https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp`

No authentication. Tools are read-only (`ENABLE_WRITES=false`). The data is fake.

**Cold start:** the app scales to zero, so the first request after idle takes about 30 seconds; later ones about 2 seconds. Warm it first:

```bash
curl -s https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/health
# optional: also wake Neon
curl -s https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/health/db
```

## MCP Inspector

UI: `npx @modelcontextprotocol/inspector`, then Transport = Streamable HTTP and the URL above. Connect → Tools → List.

CLI:

```bash
URL=https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp
npx @modelcontextprotocol/inspector --cli "$URL" --transport http --method tools/list
npx @modelcontextprotocol/inspector --cli "$URL" --transport http --method tools/call --tool-name ticket_stats
```

`tools/list` should show `search_tickets`, `get_ticket`, `ticket_stats`, `list_open_by_priority` and `sla_breaches` only.

## Claude

**Claude Desktop / claude.ai:** Settings → Connectors → add a custom connector → paste the URL (no auth).

**Claude Code:**

```bash
claude mcp add --transport http helpdesk https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp
```

## ChatGPT

In developer mode, add a connector and paste the URL. Leave authentication off.

## Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "helpdesk": {
      "url": "https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp"
    }
  }
}
```

## VS Code

In `.vscode/mcp.json`:

```json
{
  "servers": {
    "helpdesk": {
      "type": "http",
      "url": "https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp"
    }
  }
}
```

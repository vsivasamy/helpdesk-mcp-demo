# 10-minute demo: how products plug into AI agents with MCP

**Audience:** IAM / IT colleagues. **Setup before the call:** run `curl https://<FQDN>/health/db` about 1 minute before, so both the container and Neon are warm. Open a terminal and the MCP Inspector (`npx @modelcontextprotocol/inspector`, then choose Transport = Streamable HTTP and URL = `https://<FQDN>/mcp`).

| Time | Segment | What to show / say |
|---|---|---|
| 0:00-1:30 | **The problem** | "Every AI assistant wants to reach our systems (helpdesk, IdP, HR). Without a standard, each pairing is a custom integration. **MCP (Model Context Protocol)** is the USB-C for this: a product exposes *tools* once, and any MCP-capable agent can use them." Show `docs/ARCHITECTURE.md`. |
| 1:30-3:00 | **What a server exposes** | Inspector → *Connect* → *Tools* → *List*. Five tools, each with a JSON schema (enums for status/priority, `limit` ≤ 50). "The model reads these descriptions to decide what to call. Good descriptions are the product's API docs for AI." |
| 3:00-5:30 | **Calling tools by hand** | `ticket_stats` (no args) for the volume, the 19 % SLA breach rate and average resolution time. `list_open_by_priority` for the backlog. `search_tickets` with `query_text="MFA"` and `status="pending"`. `get_ticket` on one of the results (ref like `INC000123`) shows the comment thread and SLA flags. |
| 5:30-7:30 | **How an agent uses it** | Paste the same URL into an MCP-capable client (Claude Desktop / Cursor / VS Code Copilot: "add MCP server, HTTP URL"). Ask the prompts below and point out the agent choosing tools, chaining `search_tickets` → `get_ticket`, and summarising. |
| 7:30-9:00 | **Security talking points (IAM angle)** | Fake data, read-only DB role, read-only transactions, parameterised SQL, input validation, write tools absent unless enabled. Today it runs with **no auth**, which is fine for a demo but not for real data. Production would use OAuth 2.1 per the MCP authorisation spec, with Entra ID as the authorisation server, per-user scopes (e.g. `tickets.read`), consent, audit and Conditional Access. Bonus: DNS-rebinding protection via the Host/Origin allow-list. |
| 9:00-10:00 | **Cost and ops** | Scale-to-zero on both Container Apps and Neon, so idle cost is $0. GHCR builds on every push. A $1 budget alert. Teardown is one `az group delete`. Q&A. |

## Sample prompts (for the agent segment)
1. "What's our current SLA breach rate, and which priority is worst?"
2. "Show me the open P2 tickets, oldest first, and who they're assigned to."
3. "Find pending tickets about MFA and summarise what users are waiting for."
4. "Open INC000042 and draft a polite update to the requester based on the thread."
5. "Which categories generated the most tickets in October 2026?" (exercises the `date_from`/`date_to` validation)
6. "List resolution SLA breaches for P1 and P2 and suggest one process fix."
7. Guard-rail demo: "Close ticket INC000042." The agent should say it has no write tool (writes are disabled).

## CLI fallback (if the GUI misbehaves)
```bash
U=https://<FQDN>/mcp
npx @modelcontextprotocol/inspector --cli $U --transport http --method tools/list
npx @modelcontextprotocol/inspector --cli $U --transport http --method tools/call --tool-name ticket_stats
npx @modelcontextprotocol/inspector --cli $U --transport http --method tools/call --tool-name search_tickets --tool-arg query_text=VPN --tool-arg limit=3
```

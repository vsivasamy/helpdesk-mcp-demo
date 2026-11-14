"""End-to-end smoke test over Streamable HTTP using the official MCP Python client.

Usage:  MCP_URL=http://127.0.0.1:8000/mcp [MCP_API_KEY=...] python tests/smoke_client.py
If MCP_API_KEY is set the server is expected to require it (bearer mode); if unset, no-auth mode is
expected. Set SKIP_HOST_CHECKS=true when testing a remote URL. Exits non-zero on the first failure.
Never prints the API key.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
KEY = os.environ.get("MCP_API_KEY", "")
SKIP_HOST_CHECKS = os.environ.get("SKIP_HOST_CHECKS", "").lower() == "true"
EXPECT_WRITES = os.environ.get("ENABLE_WRITES", "").lower() == "true"
READ_TOOLS = {"search_tickets", "get_ticket", "ticket_stats", "list_open_by_priority", "sla_breaches"}
WRITE_TOOLS = {"create_ticket", "update_status"}
passed = 0


def check(cond: bool, msg: str) -> None:
    global passed
    if not cond:
        print(f"FAIL  {msg}")
        sys.exit(1)
    passed += 1
    print(f"PASS  {msg}")


def raw_status(headers: dict[str, str], url: str = URL) -> int:
    """POST a tools/list JSON-RPC request with the given headers and return the HTTP status."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def payload(res) -> dict:
    sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def is_error(res) -> bool:
    return bool(getattr(res, "is_error", None) or getattr(res, "isError", None))


async def mcp_session(token: str):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client(URL, http_client=http))


async def main() -> None:
    base = URL.rsplit("/mcp", 1)[0]
    health = json.load(urllib.request.urlopen(base + "/health", timeout=30))
    check(health["status"] == "ok", "/health is public and returns ok (no DB call)")
    check(health["auth"] == ("bearer" if KEY else "none"), f"/health reports auth={health['auth']}")
    dbh = json.load(urllib.request.urlopen(base + "/health/db", timeout=60))
    check(dbh["db"] == "ok", "/health/db reaches the database")

    if KEY:  # --- bearer mode
        check(raw_status({}) == 401, "bearer mode: no token -> 401")
        check(raw_status({"Authorization": "Bearer wrong-token-value"}) == 401, "bearer mode: wrong token -> 401")
        check(raw_status({"Authorization": f"Basic {KEY}"}) == 401, "bearer mode: wrong scheme -> 401")
        check(raw_status({"Authorization": f"Bearer {KEY}"}) == 200, "bearer mode: correct token -> 200")
        try:
            async with await mcp_session("wrong-token-value") as c:
                await c.list_tools()
            check(False, "bearer mode: MCP client with wrong token is rejected")
        except Exception:
            check(True, "bearer mode: MCP client with wrong token is rejected")
    else:  # --- no-auth mode
        check(raw_status({}) == 200, "no-auth mode: request without token -> 200")
        check(raw_status({"Authorization": "Bearer anything"}) == 200, "no-auth mode: stray token ignored -> 200")

    if not SKIP_HOST_CHECKS:  # --- DNS-rebinding protection (local only: we forge Host headers)
        auth = {"Authorization": f"Bearer {KEY}"} if KEY else {}
        check(raw_status({**auth, "Host": "evil.example.net"}) == 421, "unknown Host header -> 421")
        check(raw_status({**auth, "Host": "helpdesk.kindsea-1a2b3c.centralindia.azurecontainerapps.io"}) == 200,
              "*.azurecontainerapps.io Host accepted")
        check(raw_status({**auth, "Origin": "https://evil.example.net"}) == 403, "foreign Origin -> 403")

    async with await mcp_session(KEY or "unused") as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        print("tools:", ", ".join(sorted(tools)))
        check(READ_TOOLS <= set(tools), "all read tools listed")
        check((WRITE_TOOLS <= set(tools)) if EXPECT_WRITES else not (WRITE_TOOLS & set(tools)),
              f"write tools {'present' if EXPECT_WRITES else 'absent'} (ENABLE_WRITES={EXPECT_WRITES})")

        r = payload(await c.call_tool("search_tickets", {"query_text": "MFA", "limit": 5}))
        check(1 <= r["count"] <= 5, f"search_tickets text query returned {r['count']}")
        r = payload(await c.call_tool("search_tickets", {"status": "closed", "priority": "P1", "limit": 50}))
        check(all(t["status"] == "closed" and t["priority"] == "P1" for t in r["tickets"]), "search_tickets filters applied")
        r = payload(await c.call_tool("search_tickets", {"query_text": "'; DROP TABLE tickets; --"}))
        check(r["count"] == 0, "search_tickets treats SQL-ish input as plain text")
        res = await c.call_tool("search_tickets", {"limit": 500})
        check(is_error(res), "search_tickets rejects limit > 50")
        res = await c.call_tool("search_tickets", {"status": "bogus"})
        check(is_error(res), "search_tickets rejects unknown status")

        r = payload(await c.call_tool("get_ticket", {"ticket": "INC000001"}))
        check(r["ticket"]["ref"] == "INC000001" and isinstance(r["comments"], list), f"get_ticket INC000001 with {len(r['comments'])} comments")
        res = await c.call_tool("get_ticket", {"ticket": "999999"})
        check(is_error(res), "get_ticket unknown id -> error")

        r = payload(await c.call_tool("ticket_stats", {}))
        total = r["total_tickets"]
        check(total >= 10 and "by_priority" in r, f"ticket_stats total={r['total_tickets']} breach_rate={r['sla_breach_rate_pct']}%")
        r = payload(await c.call_tool("ticket_stats", {"date_from": "2026-10-01", "date_to": "2026-10-31"}))
        check(0 < r["total_tickets"] < total, f"ticket_stats October 2026 total={r['total_tickets']}")
        res = await c.call_tool("ticket_stats", {"date_from": "2026-11-01", "date_to": "2026-10-01"})
        check(is_error(res), "ticket_stats rejects inverted date range")

        r = payload(await c.call_tool("list_open_by_priority", {"limit": 3}))
        check(sum(r["open_counts"].values()) > 0, f"list_open_by_priority counts={r['open_counts']}")

        r = payload(await c.call_tool("sla_breaches", {"kind": "resolve", "limit": 5}))
        check(r["total_matching"] > 0 and all(t["resolve_breached"] for t in r["tickets"]), f"sla_breaches resolve total={r['total_matching']}")
        r = payload(await c.call_tool("sla_breaches", {"open_only": True}))
        check(all(t["status"] in ("new", "open", "in_progress", "pending") for t in r["tickets"]), f"sla_breaches open_only total={r['total_matching']}")

        if EXPECT_WRITES:
            r = payload(await c.call_tool("create_ticket", {"subject": "Smoke test ticket", "description": "Created by smoke test",
                                                            "requester_email": (await _any_requester(c)), "category": "mfa", "priority": "P4"}))
            ref = r["created"]["ref"]
            check(ref.startswith("INC"), f"create_ticket -> {ref}")
            r = payload(await c.call_tool("update_status", {"ticket": ref, "status": "resolved", "comment": "Smoke test resolve"}))
            check(r["status"] == "resolved", "update_status -> resolved")

    print(f"\nAll {passed} checks passed.")


async def _any_requester(c) -> str:
    r = payload(await c.call_tool("get_ticket", {"ticket": "1"}))
    return r["ticket"]["requester_email"]


if __name__ == "__main__":
    asyncio.run(main())

"""IT Helpdesk demo MCP server (Streamable HTTP, bearer-token auth, read-only by default).

Env:
  DATABASE_URL         required; READ-ONLY role (helpdesk_ro). Neon pooled URLs (-pooler, sslmode=require) are fine.
  MCP_API_KEY          optional; if set, clients must send `Authorization: Bearer <MCP_API_KEY>`.
                       If unset the server runs WITHOUT auth (fine for the fake-data demo; a warning is logged).
  PORT / HOST          listen address (defaults 8000 / 0.0.0.0)
  ENABLE_WRITES        "true" to register create_ticket / update_status (default false)
  DATABASE_WRITE_URL   write role connection; required if ENABLE_WRITES=true
  STATEMENT_TIMEOUT_MS per-statement timeout (default 5000)
  ALLOWED_HOSTS        comma list of allowed Host headers, wildcards ok (DNS-rebinding protection).
                       Default: localhost,127.0.0.1,*.azurecontainerapps.io.  "*" disables the check.

Pooler-safe: no session state is used. Every query runs in its own transaction that starts with
SET TRANSACTION READ ONLY + SET LOCAL statement_timeout/search_path, which works through PgBouncer
transaction pooling (Neon) as well as direct connections.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hmac
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal

import psycopg
import uvicorn
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

log = logging.getLogger("helpdesk-mcp")

Status = Literal["new", "open", "in_progress", "pending", "resolved", "closed"]
Priority = Literal["P1", "P2", "P3", "P4"]
Category = Literal["password_reset", "mfa", "sso", "access_request", "vpn", "network",
                   "hardware", "software", "printer", "email", "teams"]
OPEN_STATUSES = ("new", "open", "in_progress", "pending")
MAX_LIMIT = 50
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


API_KEY = os.environ.get("MCP_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ENABLE_WRITES = _env_bool("ENABLE_WRITES")
WRITE_URL = os.environ.get("DATABASE_WRITE_URL", "")
TIMEOUT_MS = max(100, min(int(os.environ.get("STATEMENT_TIMEOUT_MS", "5000")), 60000))
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,[::1],*.azurecontainerapps.io"


# ---------------------------------------------------------------- database
def _pool(url: str) -> AsyncConnectionPool:
    # min_size=0 + short max_idle: no idle connection is held open, so a serverless DB (Neon) can
    # scale to zero. prepare_threshold=None: no server-side prepared statements (pooler-friendly).
    # Generous timeouts absorb cold starts (container waking + Neon compute resuming).
    return AsyncConnectionPool(url, min_size=0, max_size=5, open=False, timeout=30, max_idle=60,
                               kwargs={"row_factory": dict_row, "autocommit": True,
                                       "prepare_threshold": None, "connect_timeout": 15,
                                       "application_name": "helpdesk-mcp"})


_pools: dict[str, AsyncConnectionPool] = {}
_pool_lock = asyncio.Lock()


async def _get_pool(kind: Literal["ro", "rw"]) -> AsyncConnectionPool:
    async with _pool_lock:
        if kind not in _pools:
            p = _pool(DATABASE_URL if kind == "ro" else WRITE_URL)
            await p.open(wait=False)
            _pools[kind] = p
        return _pools[kind]


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(v, (date,)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, timedelta):
        return round(v.total_seconds() / 3600, 2)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


async def query(sql: str, params: dict | None = None, *, kind: Literal["ro", "rw"] = "ro") -> list[dict]:
    pool = await _get_pool(kind)
    for attempt in (1, 2):  # one retry for a connection dropped while the DB was suspended
        try:
            async with pool.connection() as conn:
                async with conn.transaction(), conn.cursor() as cur:
                    await cur.execute("SET TRANSACTION READ ONLY" if kind == "ro" else "SET TRANSACTION READ WRITE")
                    await cur.execute(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")  # int, validated above
                    await cur.execute("SET LOCAL search_path = helpdesk")
                    await cur.execute(sql, params or {})
                    rows = await cur.fetchall() if cur.description else []
                return [_jsonable(r) for r in rows]
        except psycopg.OperationalError as e:
            if attempt == 1:
                log.warning("DB connection problem (%s); retrying once", type(e).__name__)
                await asyncio.sleep(1)
                continue
            log.exception("query failed")
            raise ToolError("Database unavailable, please retry in a few seconds") from e
        except psycopg.errors.QueryCanceled as e:
            raise ToolError(f"Query exceeded the {TIMEOUT_MS} ms statement timeout") from e
        except Exception as e:  # surface a safe message, keep detail in logs
            log.exception("query failed")
            raise ToolError(f"Database error: {type(e).__name__}") from e
    raise AssertionError("unreachable")


def _like(s: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", s) + "%"


def _ticket_id(ticket: str) -> int:
    m = re.fullmatch(r"\s*(?:INC)?0*(\d{1,9})\s*", ticket, flags=re.I)
    if not m:
        raise ToolError("ticket must be a numeric id (e.g. 42) or a reference like INC000042")
    return int(m.group(1))


def _date_range(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    if date_from and date_to and date_from > date_to:
        raise ToolError("date_from must be on or before date_to")
    f = datetime.combine(date_from, datetime.min.time(), timezone.utc) if date_from else None
    t = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), timezone.utc) if date_to else None
    return f, t


LIST_COLS = ("id, ref, subject, status, priority, category, requester, department, assignee, created_at, "
             "first_response_at, resolved_at, sla_response_due, sla_resolve_due, sla_breached")

# ---------------------------------------------------------------- server
mcp = MCPServer(
    name="helpdesk-demo",
    title="IT Helpdesk (demo data)",
    version="0.1.0",
    instructions=("Read-only access to a demo IT helpdesk with fake tickets. Ticket refs look like INC000123. "
                  "Priorities P1 (critical) to P4 (low); statuses new, open, in_progress, pending, resolved, closed. "
                  "All times are UTC."),
)


@mcp.tool(annotations=READ_ONLY)
async def search_tickets(
    query_text: Annotated[str, Field(description="Free-text search over subject and description, e.g. 'MFA new phone'. Empty matches all.", max_length=200)] = "",
    status: Annotated[Status | None, Field(description="Filter by status")] = None,
    priority: Annotated[Priority | None, Field(description="Filter by priority P1-P4")] = None,
    category: Annotated[Category | None, Field(description="Filter by category code")] = None,
    assignee: Annotated[str | None, Field(description="Filter by assignee name or email (partial match)", max_length=100)] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Max results (1-50)")] = 20,
) -> dict:
    """Search helpdesk tickets by text with optional status, priority, category and assignee filters. Newest first, max 50."""
    q = query_text.strip()
    rows = await query(
        f"""SELECT {LIST_COLS} FROM ticket_overview
            WHERE (%(q)s = '' OR to_tsvector('english', subject || ' ' || description) @@ websearch_to_tsquery('english', %(q)s)
                   OR subject ILIKE %(qlike)s OR ref ILIKE %(qlike)s)
              AND (%(status)s::text IS NULL OR status = %(status)s)
              AND (%(priority)s::text IS NULL OR priority = %(priority)s)
              AND (%(category)s::text IS NULL OR category = %(category)s)
              AND (%(assignee)s::text IS NULL OR assignee ILIKE %(alike)s OR assignee_email ILIKE %(alike)s)
            ORDER BY created_at DESC LIMIT %(limit)s""",
        {"q": q, "qlike": _like(q), "status": status, "priority": priority, "category": category,
         "assignee": assignee, "alike": _like(assignee or ""), "limit": min(limit, MAX_LIMIT)},
    )
    return {"count": len(rows), "tickets": rows}


@mcp.tool(annotations=READ_ONLY)
async def get_ticket(
    ticket: Annotated[str, Field(description="Ticket id (42) or reference (INC000042)", max_length=20)],
    include_internal: Annotated[bool, Field(description="Include internal agent notes in comments")] = True,
) -> dict:
    """Get one ticket's full details, SLA status and its comment thread (oldest first)."""
    tid = _ticket_id(ticket)
    rows = await query("SELECT * FROM ticket_overview WHERE id = %(id)s", {"id": tid})
    if not rows:
        raise ToolError(f"Ticket {ticket} not found")
    comments = await query(
        """SELECT c.created_at, c.author_type, COALESCE(a.full_name, r.full_name, 'System') AS author,
                  c.is_internal, c.body
           FROM ticket_comments c
           LEFT JOIN agents a ON a.id = c.author_agent_id
           LEFT JOIN requesters r ON r.id = c.author_requester_id
           WHERE c.ticket_id = %(id)s AND (%(internal)s OR NOT c.is_internal)
           ORDER BY c.created_at, c.id""",
        {"id": tid, "internal": include_internal},
    )
    return {"ticket": rows[0], "comments": comments}


@mcp.tool(annotations=READ_ONLY)
async def ticket_stats(
    date_from: Annotated[date | None, Field(description="Only tickets created on/after this date (YYYY-MM-DD, UTC)")] = None,
    date_to: Annotated[date | None, Field(description="Only tickets created on/before this date (YYYY-MM-DD, UTC)")] = None,
) -> dict:
    """Aggregate stats: counts by status, priority and category, average first-response and resolution time, SLA breach rate."""
    f, t = _date_range(date_from, date_to)
    p = {"f": f, "t": t}
    where = "WHERE (%(f)s::timestamptz IS NULL OR created_at >= %(f)s) AND (%(t)s::timestamptz IS NULL OR created_at < %(t)s)"
    summary = (await query(
        f"""SELECT count(*) AS total_tickets,
                   count(*) FILTER (WHERE status IN ('new','open','in_progress','pending')) AS open_tickets,
                   round(avg(extract(epoch FROM first_response_at - created_at)/60)::numeric, 1) AS avg_first_response_minutes,
                   round(avg(extract(epoch FROM resolved_at - created_at)/3600)::numeric, 2) AS avg_resolution_hours,
                   round(100.0 * avg(sla_breached::int), 1) AS sla_breach_rate_pct,
                   count(*) FILTER (WHERE response_breached) AS response_breaches,
                   count(*) FILTER (WHERE resolve_breached) AS resolve_breaches
            FROM ticket_overview {where}""", p))[0]

    async def by(col: str) -> dict:
        rows = await query(f"SELECT {col} AS k, count(*) AS n FROM ticket_overview {where} GROUP BY 1 ORDER BY 2 DESC", p)
        return {r["k"]: r["n"] for r in rows}

    return {"date_from": date_from and date_from.isoformat(), "date_to": date_to and date_to.isoformat(),
            **summary, "by_status": await by("status"), "by_priority": await by("priority"),
            "by_category": await by("category")}


@mcp.tool(annotations=READ_ONLY)
async def list_open_by_priority(
    priority: Annotated[Priority | None, Field(description="Only this priority; omit for all")] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Max tickets per priority (1-50)")] = 10,
) -> dict:
    """List unresolved tickets (new/open/in_progress/pending) grouped by priority, oldest first, with counts."""
    rows = await query(
        f"""SELECT * FROM (
              SELECT {LIST_COLS}, row_number() OVER (PARTITION BY priority ORDER BY created_at) AS rn
              FROM ticket_overview
              WHERE status IN ('new','open','in_progress','pending')
                AND (%(p)s::text IS NULL OR priority = %(p)s)) x
            WHERE rn <= %(limit)s ORDER BY priority, created_at""",
        {"p": priority, "limit": min(limit, MAX_LIMIT)})
    counts = await query(
        """SELECT priority, count(*) AS n FROM ticket_overview
           WHERE status IN ('new','open','in_progress','pending') AND (%(p)s::text IS NULL OR priority = %(p)s)
           GROUP BY 1 ORDER BY 1""", {"p": priority})
    grouped: dict[str, list] = {}
    for r in rows:
        r.pop("rn", None)
        grouped.setdefault(r["priority"], []).append(r)
    return {"open_counts": {c["priority"]: c["n"] for c in counts}, "tickets_by_priority": grouped}


@mcp.tool(annotations=READ_ONLY)
async def sla_breaches(
    kind: Annotated[Literal["any", "response", "resolve"], Field(description="Which SLA was breached")] = "any",
    open_only: Annotated[bool, Field(description="Only unresolved tickets")] = False,
    priority: Annotated[Priority | None, Field(description="Filter by priority")] = None,
    date_from: Annotated[date | None, Field(description="Created on/after (YYYY-MM-DD)")] = None,
    date_to: Annotated[date | None, Field(description="Created on/before (YYYY-MM-DD)")] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Max results (1-50)")] = 20,
) -> dict:
    """List tickets that breached their response and/or resolution SLA, most overdue first."""
    f, t = _date_range(date_from, date_to)
    col = {"any": "sla_breached", "response": "response_breached", "resolve": "resolve_breached"}[kind]
    p = {"open": open_only, "p": priority, "f": f, "t": t, "limit": min(limit, MAX_LIMIT)}
    where = f"""WHERE {col}
              AND (NOT %(open)s OR status IN ('new','open','in_progress','pending'))
              AND (%(p)s::text IS NULL OR priority = %(p)s)
              AND (%(f)s::timestamptz IS NULL OR created_at >= %(f)s)
              AND (%(t)s::timestamptz IS NULL OR created_at < %(t)s)"""
    rows = await query(
        f"""SELECT {LIST_COLS}, response_breached, resolve_breached,
                   round((extract(epoch FROM COALESCE(resolved_at, now()) - sla_resolve_due)/3600)::numeric, 1) AS resolve_overdue_hours
            FROM ticket_overview {where}
            ORDER BY resolve_overdue_hours DESC NULLS LAST LIMIT %(limit)s""", p)
    total = (await query(f"SELECT count(*) AS n FROM ticket_overview {where}", p))[0]["n"]
    return {"kind": kind, "total_matching": total, "returned": len(rows), "tickets": rows}


# ---------------------------------------------------------------- optional write tools
if ENABLE_WRITES:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    async def create_ticket(
        subject: Annotated[str, Field(min_length=3, max_length=200, description="Short summary")],
        description: Annotated[str, Field(min_length=3, max_length=4000, description="Full description")],
        requester_email: Annotated[str, Field(max_length=200, pattern=r"^[^@\s]+@example\.com$", description="Existing requester email (@example.com)")],
        category: Annotated[Category, Field(description="Category code")],
        priority: Annotated[Priority, Field(description="Priority P1-P4")] = "P3",
    ) -> dict:
        """[WRITE] Create a new ticket with status 'new'; SLA due times are derived from the priority."""
        rows = await query(
            """INSERT INTO tickets (subject, description, requester_id, category_id, priority_id, status_id,
                                    sla_response_due, sla_resolve_due)
               SELECT %(s)s, %(d)s, r.id, c.id, p.id, st.id,
                      now() + make_interval(mins => p.sla_response_minutes), now() + make_interval(mins => p.sla_resolve_minutes)
               FROM requesters r, categories c, priorities p, statuses st
               WHERE r.email = %(e)s AND c.code = %(c)s AND p.code = %(p)s AND st.code = 'new'
               RETURNING id, ref""",
            {"s": subject, "d": description, "e": requester_email.lower(), "c": category, "p": priority}, kind="rw")
        if not rows:
            raise ToolError(f"Unknown requester {requester_email}")
        return {"created": rows[0]}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def update_status(
        ticket: Annotated[str, Field(description="Ticket id or INC reference", max_length=20)],
        status: Annotated[Status, Field(description="New status")],
        comment: Annotated[str | None, Field(max_length=2000, description="Optional system comment to add")] = None,
    ) -> dict:
        """[WRITE] Change a ticket's status; stamps first_response_at / resolved_at / closed_at as appropriate."""
        tid = _ticket_id(ticket)
        rows = await query(
            """UPDATE tickets t SET status_id = st.id, updated_at = now(),
                   first_response_at = CASE WHEN %(s)s <> 'new' THEN COALESCE(t.first_response_at, now()) ELSE t.first_response_at END,
                   resolved_at = CASE WHEN %(s)s IN ('resolved','closed') THEN COALESCE(t.resolved_at, now()) ELSE NULL END,
                   closed_at   = CASE WHEN %(s)s = 'closed' THEN COALESCE(t.closed_at, now()) ELSE NULL END
               FROM statuses st WHERE st.code = %(s)s AND t.id = %(id)s RETURNING t.id, t.ref""",
            {"s": status, "id": tid}, kind="rw")
        if not rows:
            raise ToolError(f"Ticket {ticket} not found")
        if comment:
            await query("INSERT INTO ticket_comments (ticket_id, author_type, body, created_at) VALUES (%(id)s, 'system', %(b)s, now())",
                        {"id": tid, "b": comment}, kind="rw")
        return {"updated": rows[0], "status": status}


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> JSONResponse:
    """Liveness: no DB call, so platform probes never wake a scaled-to-zero database."""
    return JSONResponse({"status": "ok", "auth": "bearer" if API_KEY else "none", "writes_enabled": ENABLE_WRITES})


@mcp.custom_route("/health/db", methods=["GET"], include_in_schema=False)
async def health_db(_: Request) -> JSONResponse:
    """Readiness incl. DB round-trip (wakes Neon if suspended). Use manually, not as a frequent probe."""
    try:
        await query("SELECT 1 AS ok")
        return JSONResponse({"status": "ok", "db": "ok"})
    except Exception:
        return JSONResponse({"status": "degraded", "db": "unreachable"}, status_code=503)


# ---------------------------------------------------------------- auth + host guard
OPEN_PATHS = ("/health", "/health/db")


async def _deny(send, status: int, error: str, extra: list | None = None) -> None:
    body = json.dumps({"error": error}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *(extra or [])]})
    await send({"type": "http.response.body", "body": body})


class BearerAuth:
    """Pure-ASGI middleware: every HTTP path except the health checks needs `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS:
            return await self.app(scope, receive, send)
        header = dict(scope.get("headers") or []).get(b"authorization", b"")
        scheme, _, presented = header.partition(b" ")
        if not (scheme.lower() == b"bearer" and hmac.compare_digest(presented.strip(), self.token)):
            return await _deny(send, 401, "unauthorized", [(b"www-authenticate", b'Bearer realm="helpdesk-mcp"')])
        return await self.app(scope, receive, send)


class HostGuard:
    """DNS-rebinding protection with wildcard support (the SDK's built-in check only matches exact hosts).

    Checks the Host header (port stripped) and, when present, the Origin header's host against ALLOWED_HOSTS
    patterns such as `*.azurecontainerapps.io`. Health checks are exempt so platform probes always work.
    """

    def __init__(self, app, patterns: list[str]):
        self.app, self.patterns = app, [p.lower() for p in patterns]

    def _ok(self, host: str) -> bool:
        host = host.lower().strip()
        if host.startswith("["):
            host = host.split("]")[0] + "]"
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return any(fnmatch.fnmatchcase(host, p) for p in self.patterns)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin", b"").decode("latin-1")
        origin_host = origin.split("://", 1)[-1].split("/", 1)[0] if origin and origin != "null" else ""
        if not self._ok(host) or (origin_host and not self._ok(origin_host)):
            log.warning("rejected request: host=%r origin=%r not in ALLOWED_HOSTS", host, origin)
            return await _deny(send, 421 if not self._ok(host) else 403, "host or origin not allowed")
        return await self.app(scope, receive, send)


def build_app():
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL must be set (read-only role)")
    if ENABLE_WRITES and not WRITE_URL:
        raise SystemExit("ENABLE_WRITES=true requires DATABASE_WRITE_URL")
    if API_KEY and len(API_KEY) < 24:
        raise SystemExit("MCP_API_KEY is set but too short (use 24+ random characters), or unset it for no-auth mode")
    host = os.environ.get("HOST", "0.0.0.0")
    patterns = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",") if h.strip()]
    # The SDK's exact-match host check is replaced by HostGuard (wildcards); pass host=0.0.0.0 so the SDK
    # doesn't auto-enable its localhost-only list.
    app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0")
    if patterns != ["*"]:
        app = HostGuard(app, patterns)
    else:
        log.warning("ALLOWED_HOSTS='*': Host/Origin checks disabled")
    if API_KEY:
        app = BearerAuth(app, API_KEY)
    else:
        log.warning("SECURITY WARNING: MCP_API_KEY not set: running WITHOUT authentication. Only do this with fake/demo data.")
    log.info("helpdesk-mcp: auth=%s writes=%s allowed_hosts=%s bind=%s", "bearer" if API_KEY else "none",
             ENABLE_WRITES, patterns, host)
    return app


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(build_app(), timeout_keep_alive=75, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()

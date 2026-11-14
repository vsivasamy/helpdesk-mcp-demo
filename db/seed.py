"""Seed the helpdesk demo database with obviously fake, reproducible data.

Idempotent: drops and recreates the `helpdesk` schema every run.
Env:
  DATABASE_ADMIN_URL     owner connection (local superuser, or Neon's project owner e.g. helpdesk_owner).
                         A Neon "-pooler" host is automatically swapped for the direct host for DDL.
  HELPDESK_RO_PASSWORD   password to set on the read-only role helpdesk_ro (needed unless SEED_SKIP_ROLES=true)
  HELPDESK_RW_PASSWORD   optional password for helpdesk_rw (write tools)
  SEED_TICKETS           number of tickets to generate (default 400; e.g. 100 for a smaller, faster set)
  SEED_SKIP_ROLES        "true" to skip role creation entirely (then run the server with the owner URL;
                         it still forces SET TRANSACTION READ ONLY on every read).

If CREATE ROLE is not permitted (some managed Postgres), the data is still committed and a warning
explains the fallback.
"""
from __future__ import annotations

import os
import random
import sys
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from faker import Faker
from psycopg import sql

SEED = 20261113
REF_NOW = datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)  # data window ends here
WINDOW_DAYS = 90
# SEED_TICKETS (default 400, max 5000) scales the data set; comments scale with tickets (~2.7 each).
N_TICKETS = max(10, min(int(os.environ.get("SEED_TICKETS", "400")), 5000))
N_REQUESTERS = min(60, max(20, N_TICKETS * 3 // 20))  # 100 -> 20, 400+ -> 60
N_AGENTS = 10
HERE = Path(__file__).parent

DEPARTMENTS = [("Finance", "London"), ("Human Resources", "Manchester"), ("Sales", "London"),
               ("Marketing", "Bristol"), ("Engineering", "Edinburgh"), ("Operations", "Leeds"),
               ("Legal", "London"), ("Customer Success", "Birmingham")]
AGENT_TEAMS = ["Service Desk", "Service Desk", "Service Desk", "Identity & Access", "Identity & Access",
               "Endpoint", "Endpoint", "Network", "Messaging", "Service Desk"]
PRIORITY_WEIGHTS = {"P1": 4, "P2": 16, "P3": 50, "P4": 30}

SUBJECTS = {
    "password_reset": ["Password reset required - locked out after holiday", "Account locked after too many sign-in attempts",
                       "Password expired, cannot sign in to laptop", "Self-service password reset not sending code",
                       "Forgot password for {app}"],
    "mfa": ["MFA prompts not arriving on new phone", "Lost phone - need MFA method reset",
            "Authenticator app showing wrong number match", "Repeated MFA prompts in Outlook",
            "Cannot register FIDO2 security key", "MFA fatigue - unexpected push notifications"],
    "sso": ["SSO to {app} fails with AADSTS50105", "SAML error signing in to {app}",
            "Redirect loop when signing in to {app} via SSO", "{app} says user not assigned to application",
            "OIDC login to {app} returns invalid_client"],
    "access_request": ["Access request: {group} security group", "Need Entra ID role for {app} administration",
                       "Please add me to {group}", "Access to shared mailbox {mailbox}",
                       "Conditional Access blocking sign-in from managed device", "Guest access for external auditor"],
    "vpn": ["VPN disconnects every 10 minutes", "Cannot connect to VPN from hotel Wi-Fi",
            "GlobalProtect stuck on 'connecting'", "VPN connected but internal sites not loading"],
    "network": ["Office Wi-Fi very slow on 3rd floor", "Cannot connect to guest Wi-Fi", "Ethernet port at desk not working"],
    "hardware": ["Laptop will not power on", "Laptop battery swelling - urgent replacement", "Docking station not detecting monitors",
                 "New starter laptop request", "Keyboard keys sticking", "Laptop running extremely slowly"],
    "software": ["Install request: {sw}", "{sw} licence expired", "{sw} crashes on start-up", "Need admin rights to install {sw}"],
    "printer": ["Printer on 2nd floor showing paper jam", "Cannot add follow-me printer", "Print jobs stuck in queue",
                "Scanner to email not working"],
    "email": ["Outlook keeps asking for password", "Outlook not syncing new emails", "Cannot send email - mailbox full",
              "Calendar invites not showing in Outlook", "Suspicious phishing email received", "Outlook search not returning results"],
    "teams": ["Teams camera not working in meetings", "Cannot join external Teams meeting", "Teams status stuck on Away",
              "Teams chat history missing"],
}
CATEGORY_WEIGHTS = {"password_reset": 16, "mfa": 12, "sso": 8, "access_request": 14, "vpn": 9, "network": 4,
                    "hardware": 10, "software": 8, "printer": 5, "email": 10, "teams": 4}
APPS = ["Salesforce", "Workday", "ServiceNow", "Jira", "Confluence", "Slack", "Zoom", "Box", "Tableau", "SAP Concur"]
GROUPS = ["SG-Finance-Reports", "SG-HR-Confidential", "SG-Engineering-Prod-ReadOnly", "SG-Sales-EMEA", "SG-Legal-Contracts"]
MAILBOXES = ["payroll@example.com", "invoices@example.com", "recruitment@example.com", "support@example.com"]
SOFTWARE = ["Visual Studio Code", "Adobe Acrobat Pro", "Power BI Desktop", "Tableau Desktop", "Python 3.13", "Zoom client"]

AGENT_COMMENTS = ["Thanks for raising this. I'm looking into it now.", "Could you confirm when this started and whether anything changed?",
                  "I've reset the method; please try signing in again and let me know.", "Escalated to {team} for further investigation.",
                  "Remote session completed. Issue appears resolved on my side.", "Checked the sign-in logs - seeing the failure; investigating.",
                  "Awaiting approval from the group owner before granting access.", "Replacement device has been ordered.",
                  "Please restart your device and reconnect, then confirm.", "Applied the fix. Marking as resolved; reply if it recurs."]
REQUESTER_COMMENTS = ["Thanks, trying now.", "Still not working I'm afraid.", "It started this morning after the update.",
                      "That's fixed it, many thanks!", "Any update on this please? It's blocking my work.",
                      "I'm in meetings until 2pm, can we do it after?", "Screenshot attached of the error."]
INTERNAL_NOTES = ["Internal: user is VIP - prioritise.", "Internal: related to change CHG004512.",
                  "Internal: checked Conditional Access policy assignment.", "Internal: known issue with vendor, ticket raised."]


def weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def business_time(rng: random.Random) -> datetime:
    """Random created_at in the window, biased to UK weekday working hours."""
    while True:
        d = REF_NOW - timedelta(days=rng.uniform(0, WINDOW_DAYS))
        if d.weekday() >= 5 and rng.random() < 0.85:
            continue
        hour = rng.choices(range(24), weights=[1]*7 + [6, 10, 10, 9, 8, 7, 9, 9, 8, 6, 3] + [1]*6)[0]
        return d.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60), microsecond=0)


def pick_status(rng: random.Random, age_days: float) -> str:
    if age_days > 14:
        w = {"closed": 88, "resolved": 9, "pending": 2, "in_progress": 1}
    elif age_days > 3:
        w = {"resolved": 45, "closed": 20, "in_progress": 14, "pending": 12, "open": 9}
    elif age_days > 1:
        w = {"open": 25, "in_progress": 30, "pending": 12, "resolved": 28, "new": 5}
    else:
        w = {"new": 35, "open": 25, "in_progress": 20, "resolved": 20}
    return weighted(rng, w)


def fill(rng: random.Random, template: str) -> str:
    return template.format(app=rng.choice(APPS), group=rng.choice(GROUPS), mailbox=rng.choice(MAILBOXES),
                           sw=rng.choice(SOFTWARE), team="the next team")


def direct_url(url: str) -> str:
    """Neon: DDL and role management should use the direct endpoint, not the PgBouncer '-pooler' one."""
    parts = urlsplit(url)
    if parts.hostname and "-pooler." in parts.hostname:
        print("note: using Neon direct endpoint (stripped '-pooler') for schema/role changes")
        return urlunsplit(parts._replace(netloc=parts.netloc.replace("-pooler.", ".", 1)))
    return url


def setup_roles(conn, ro_pw: str | None, rw_pw: str | None) -> bool:
    """Create/refresh the least-privilege roles. Returns False (with guidance) if not permitted."""
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute((HERE / "roles.sql").read_text())
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO helpdesk_ro, helpdesk_rw").format(
                sql.Identifier(conn.info.dbname)))
            if ro_pw:
                cur.execute(sql.SQL("ALTER ROLE helpdesk_ro PASSWORD {}").format(sql.Literal(ro_pw)))
            if rw_pw:
                cur.execute(sql.SQL("ALTER ROLE helpdesk_rw PASSWORD {}").format(sql.Literal(rw_pw)))
        return True
    except psycopg.Error as e:
        print(f"WARNING: could not create/alter roles ({type(e).__name__}: {e.diag.message_primary if e.diag else e}).\n"
              "  Data was seeded. Options: (a) create helpdesk_ro in the provider console/CLI "
              "(Neon: `neon roles create --name helpdesk_ro --access read_only`) and re-run with SEED_SKIP_ROLES=true;\n"
              "  (b) point DATABASE_URL at the owner role - the server still runs every read in a READ ONLY transaction.",
              file=sys.stderr)
        return False


def main() -> int:
    admin_url = os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")
    ro_pw = os.environ.get("HELPDESK_RO_PASSWORD")
    rw_pw = os.environ.get("HELPDESK_RW_PASSWORD")
    skip_roles = os.environ.get("SEED_SKIP_ROLES", "").lower() in ("1", "true", "yes")
    if not admin_url or not (ro_pw or skip_roles):
        print("DATABASE_ADMIN_URL (or DATABASE_URL) and HELPDESK_RO_PASSWORD (or SEED_SKIP_ROLES=true) are required",
              file=sys.stderr)
        return 2
    admin_url = direct_url(admin_url)

    rng = random.Random(SEED)
    fake = Faker("en_GB")
    fake.seed_instance(SEED)

    with psycopg.connect(admin_url, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute((HERE / "schema.sql").read_text())

        cur.executemany("INSERT INTO departments (name, location) VALUES (%s, %s)", DEPARTMENTS)
        cur.execute("SELECT code, id, sla_response_minutes, sla_resolve_minutes FROM priorities")
        prios = {c: (i, r, s) for c, i, r, s in cur.fetchall()}
        cur.execute("SELECT code, id FROM statuses"); statuses = dict(cur.fetchall())
        cur.execute("SELECT code, id, team FROM categories"); cats = {c: (i, t) for c, i, t in cur.fetchall()}

        used: set[str] = set()

        def email_for(name: str) -> str:
            base = ".".join(name.lower().replace("'", "").split()[-2:])
            e, n = f"{base}@example.com", 2
            while e in used:
                e, n = f"{base}{n}@example.com", n + 1
            used.add(e)
            return e

        requesters = []
        for _ in range(N_REQUESTERS):
            name = f"{fake.first_name()} {fake.last_name()}"
            requesters.append((name, email_for(name), rng.randint(1, len(DEPARTMENTS)), fake.job(), rng.random() < 0.08,
                               REF_NOW - timedelta(days=rng.randint(120, 1500))))
        cur.executemany("INSERT INTO requesters (full_name, email, department_id, job_title, is_vip, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", requesters)

        agents = []
        for i in range(N_AGENTS):
            name = f"{fake.first_name()} {fake.last_name()}"
            agents.append((name, email_for(name), AGENT_TEAMS[i], 1 if AGENT_TEAMS[i] == "Service Desk" else rng.choice([2, 3])))
        cur.executemany("INSERT INTO agents (full_name, email, team, tier) VALUES (%s,%s,%s,%s)", agents)
        agents_by_team: dict[str, list[int]] = {}
        for idx, a in enumerate(agents, start=1):
            agents_by_team.setdefault(a[2], []).append(idx)

        tickets, comment_plans = [], []
        for _ in range(N_TICKETS):
            cat = weighted(rng, CATEGORY_WEIGHTS)
            pr = weighted(rng, PRIORITY_WEIGHTS)
            pid, resp_min, res_min = prios[pr]
            created = business_time(rng)
            age_days = (REF_NOW - created).total_seconds() / 86400
            status = pick_status(rng, age_days)
            resp_due = created + timedelta(minutes=resp_min)
            res_due = created + timedelta(minutes=res_min)

            first_resp = resolved = closed = None
            if status != "new":
                f = rng.uniform(0.05, 0.95) if rng.random() < 0.93 else rng.uniform(1.1, 4.0)  # ~7% response breaches
                first_resp = min(created + timedelta(minutes=resp_min * f), REF_NOW)
            if status in ("resolved", "closed"):
                f = rng.uniform(0.1, 0.95) if rng.random() < 0.91 else rng.uniform(1.1, 3.0)   # ~9% resolve breaches
                resolved = max(created + timedelta(minutes=res_min * f), first_resp + timedelta(minutes=5))
                if resolved > REF_NOW:
                    status, resolved = "in_progress", None
            if status == "closed":
                closed = resolved + timedelta(days=rng.uniform(1, 5))
                if closed > REF_NOW:
                    status, closed = "resolved", None

            team = cats[cat][1]
            pool = agents_by_team.get(team, []) + agents_by_team["Service Desk"]
            assignee = None if status == "new" and rng.random() < 0.8 else rng.choice(pool)
            requester_id = rng.randint(1, N_REQUESTERS)
            subject = fill(rng, rng.choice(SUBJECTS[cat]))
            desc = (f"{subject}. " + fake.sentence(nb_words=12) +
                    f" Device: {rng.choice(['Windows 11 laptop', 'MacBook Pro', 'iPhone', 'Android phone', 'Surface Pro'])}.")
            updated = max(x for x in (created, first_resp, resolved, closed) if x)
            tickets.append((subject, desc, requester_id, assignee, cats[cat][0], pid, statuses[status],
                            rng.choice(["portal"] * 5 + ["email"] * 3 + ["phone", "chat", "walk_up"]),
                            created, updated, first_resp, resolved, closed, resp_due, res_due))
            comment_plans.append((requester_id, assignee, team, created, first_resp, resolved or REF_NOW, status))

        cur.executemany(
            "INSERT INTO tickets (subject, description, requester_id, assignee_agent_id, category_id, priority_id, status_id,"
            " channel, created_at, updated_at, first_response_at, resolved_at, closed_at, sla_response_due, sla_resolve_due)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", tickets)

        comments = []
        for tid, (req_id, agent_id, team, created, first_resp, end, status) in enumerate(comment_plans, start=1):
            if first_resp is None:
                if rng.random() < 0.3:
                    comments.append((tid, "requester", None, req_id, rng.choice(REQUESTER_COMMENTS), False,
                                     created + timedelta(minutes=rng.randint(5, 120))))
                continue
            comments.append((tid, "agent", agent_id, None, AGENT_COMMENTS[0], False, first_resp))
            n_extra = rng.choices([0, 1, 2, 3, 4, 5], weights=[30, 35, 20, 10, 5, 0])[0]
            span = max((end - first_resp).total_seconds(), 60)
            times = sorted(first_resp + timedelta(seconds=rng.uniform(30, span)) for _ in range(n_extra))
            for t in times:
                kind = rng.random()
                if kind < 0.45:
                    comments.append((tid, "agent", agent_id, None, rng.choice(AGENT_COMMENTS[1:]).format(team=team), False, t))
                elif kind < 0.85:
                    comments.append((tid, "requester", None, req_id, rng.choice(REQUESTER_COMMENTS), False, t))
                else:
                    comments.append((tid, "agent", agent_id, None, rng.choice(INTERNAL_NOTES), True, t))
            if status in ("resolved", "closed") and rng.random() < 0.5:
                comments.append((tid, "system", None, None, "Ticket marked as resolved.", False, end))
        cur.executemany("INSERT INTO ticket_comments (ticket_id, author_type, author_agent_id, author_requester_id, body,"
                        " is_internal, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", comments)

        conn.commit()
        roles_ok = None if skip_roles else setup_roles(conn, ro_pw, rw_pw)

        cur.execute("SET LOCAL search_path = helpdesk")
        for t in ("departments", "requesters", "agents", "categories", "priorities", "statuses", "tickets", "ticket_comments"):
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
            print(f"{t:16} {cur.fetchone()[0]}")
        conn.commit()
        print("roles:", {None: "skipped", True: "helpdesk_ro/helpdesk_rw ready", False: "NOT created (see warning)"}[roles_ok])
    return 0 if roles_ok is not False else 3


if __name__ == "__main__":
    sys.exit(main())

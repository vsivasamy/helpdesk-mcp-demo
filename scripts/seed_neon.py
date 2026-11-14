"""Seed (or just check) the Neon database from this machine using .neon.env.

  python scripts/seed_neon.py           # drop/recreate schema, seed SEED_TICKETS (default 400), create roles
  python scripts/seed_neon.py --check   # connect as helpdesk_ro via the pooled endpoint and print row counts

.neon.env keys: NEON_ADMIN_URL (owner, pooled or direct), NEON_RO_PASSWORD, NEON_RW_PASSWORD (optional).
Uses the DIRECT endpoint for DDL. If raw Postgres (5432) is blocked on this network, it automatically
tunnels through Neon's WebSocket endpoint via scripts/neon_ws_bridge.py. Never prints secrets.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from with_env import parse  # noqa: E402


def reachable(url: str) -> bool:
    try:
        psycopg.connect(url, connect_timeout=8).close()
        return True
    except psycopg.Error:
        return False


def bridged(host: str, user: str, pw: str, port: int) -> tuple[str, subprocess.Popen]:
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/neon_ws_bridge.py"), host, "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return f"postgresql://{quote(user, safe='')}:{quote(pw, safe='')}@127.0.0.1:{port}/{dbname}?sslmode=disable", proc


env = parse(str(ROOT / ".neon.env"))
admin = urlsplit(env["NEON_ADMIN_URL"])
dbname = admin.path.lstrip("/") or "neondb"
pooled_host = admin.hostname
direct_host = pooled_host.replace("-pooler.", ".", 1)
procs = []
try:
    if "--check" in sys.argv:
        ro_url = env["NEON_RO_URL"]
        if not reachable(ro_url):
            print("note: 5432 blocked here, using WebSocket bridge")
            ro_url, p = bridged(pooled_host, "helpdesk_ro", env["NEON_RO_PASSWORD"], 15432); procs.append(p)
        with psycopg.connect(ro_url, connect_timeout=30) as c:
            for t in ("departments", "requesters", "agents", "categories", "priorities", "statuses", "tickets", "ticket_comments"):
                print(f"{t:16} {c.execute(f'SELECT count(*) FROM helpdesk.{t}').fetchone()[0]}")
            print("connected as", c.execute("select current_user").fetchone()[0])
        sys.exit(0)

    direct = env["NEON_ADMIN_URL"].replace("-pooler.", ".", 1)
    if not reachable(direct):
        print("note: 5432 blocked here, using WebSocket bridge to the direct endpoint")
        direct, p = bridged(direct_host, admin.username, admin.password, 15433); procs.append(p)
    child = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "HELPDESK_"))}
    child.update(DATABASE_ADMIN_URL=direct, HELPDESK_RO_PASSWORD=env["NEON_RO_PASSWORD"])
    if env.get("NEON_RW_PASSWORD"):
        child["HELPDESK_RW_PASSWORD"] = env["NEON_RW_PASSWORD"]
    sys.exit(subprocess.run([sys.executable, str(ROOT / "db/seed.py")], env=child).returncode)
finally:
    for p in procs:
        p.terminate()

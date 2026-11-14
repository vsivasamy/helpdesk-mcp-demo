"""Local-dev only: expose a Neon endpoint on 127.0.0.1 via Neon's Postgres-over-WebSocket (wss://<host>/v2).

Why: some sandboxes/corporate networks only allow HTTPS egress, so raw Postgres on 5432 fails.
Neon's serverless driver protocol tunnels the normal Postgres wire protocol inside a TLS WebSocket.
Connect psycopg/psql to 127.0.0.1:<port> with sslmode=disable (TLS is provided by wss).

  python scripts/neon_ws_bridge.py <neon-host> [--port 15432]
Not needed in Azure/CI, where port 5432 egress works.
"""
import argparse
import asyncio
import logging

import websockets

log = logging.getLogger("neon-ws-bridge")


async def handle(reader, writer, host):
    try:
        async with websockets.connect(f"wss://{host}/v2", max_size=None, open_timeout=30,
                                      ping_interval=20, compression=None) as ws:
            async def up():
                while data := await reader.read(65536):
                    await ws.send(data)
                await ws.close()

            async def down():
                async for msg in ws:
                    writer.write(msg if isinstance(msg, bytes) else msg.encode())
                    await writer.drain()

            await asyncio.gather(up(), down())
    except Exception as e:
        log.warning("bridge connection ended: %s", type(e).__name__)
    finally:
        writer.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=15432)
    a = ap.parse_args()
    srv = await asyncio.start_server(lambda r, w: handle(r, w, a.host), "127.0.0.1", a.port)
    log.warning("bridge listening on 127.0.0.1:%d -> wss://%s/v2", a.port, a.host[:20] + "...")
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

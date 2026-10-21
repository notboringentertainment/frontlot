"""Probe P2 only: a stand-in for the broker's live endpoint. Not product code.

Answers the add-on's Story-drive routes, answers /run with "probe-ok", and
3 s later queues one inbox submit so the probe can see an outcome message
arrive in the conversation. Every request is logged as one JSON line.
"""
from __future__ import annotations

import argparse, asyncio, json, os, time, uuid
from pathlib import Path


async def serve(sock: Path, token: str, log: Path) -> None:
    inbox: asyncio.Queue = asyncio.Queue()
    epoch = {"id": None}

    def note(route: str, body) -> None:
        with open(log, "a") as fh:
            fh.write(json.dumps({"at": time.time(), "route": route, "body": body}) + "\n")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            route = lines[0].split(" ")[1]
            headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
            raw = await reader.readexactly(int(headers.get("content-length", "0")))
            body = json.loads(raw or b"{}")
            status, reply = "200 OK", {}
            if headers.get("x-frontlot-token") != token:
                status = "401 Unauthorized"
            else:
                note(route, body)
                if route == "/hello":
                    epoch["id"] = body.get("epoch")
                elif route == "/report":
                    events = body.get("events") or []
                    reply = {"acceptedThrough": events[-1]["seq"] if events else 0}
                elif route == "/run":
                    reply = {"requestId": "r-probe", "status": "running", "plain": "probe-ok"}
                    asyncio.get_running_loop().call_later(3, inbox.put_nowait, {
                        "id": uuid.uuid4().hex, "epoch": epoch["id"],
                        "submit": "[Front Lot] Probe run finished: probe-ok-2."})
                elif route == "/inbox":
                    try:
                        reply = await asyncio.wait_for(inbox.get(), 25)
                    except asyncio.TimeoutError:
                        reply = {}
            payload = json.dumps(reply).encode()
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        finally:
            writer.close()

    sock.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(sock))
    os.chmod(sock, 0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", type=Path, default=Path("/tmp/fl-probe.sock"))
    ap.add_argument("--token", default="probe-token")
    ap.add_argument("--log", type=Path, default=Path("/tmp/fl-probe.log"))
    a = ap.parse_args()
    asyncio.run(serve(a.socket, a.token, a.log))

"""Shared fakes for Front Lot's Claude session tests (no real claude, no spend)."""
from __future__ import annotations

import json, os, signal, socket, sys, time
from pathlib import Path

from tests.backlot.tty_helpers import wait_until

REPO = Path(__file__).resolve().parents[2]

# Runs as `claude` in the film's work area with the broker's allowlisted env. It records the live
# endpoint it was given (tests cannot read Claude's env any other way), then echoes its stdin.
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open("live.json", "w") as fh:
    json.dump({"socket": os.environ.get("FRONTLOT_LIVE_SOCKET"), "token": os.environ.get("FRONTLOT_LIVE_TOKEN"),
               "pgid": os.getpgid(0)}, fh)
print("FAKE CLAUDE READY", flush=True)
for line in sys.stdin:
    print("echo:" + line.strip(), flush=True)
"""

# Test-only operations, injected into the broker subprocess (monkeypatching in pytest cannot reach it).
STUB_SITECUSTOMIZE = """import os, sys
sys.path.insert(0, os.environ["FRONTLOT_TEST_REPO"])
from backlot import claude_ops as _ops

def _build(paid):
    def build(ctx, p):
        return _ops.Prepared(ctx.op, paid, [sys.executable, "-c", "print('made 1')"], {},
                             summary="Make one test picture for hero-a", entity="hero-a",
                             estimate_usd=0.12 if paid else None)
    return build

_ops.OPERATIONS["test_paid"] = _ops.Operation(True, frozenset(), _build(True))
_ops.OPERATIONS["test_free"] = _ops.Operation(False, frozenset(), _build(False))

if os.environ.get("FRONTLOT_TEST_LAUNCH_OSERROR"):   # a launch that breaks with something other than Rejected
    from backlot import claude_requests as _req

    def _broken_launch(self, rid, **kw):
        raise OSError("no space left on device")
    _req.RequestStore.launch = _broken_launch
"""

# A `claude` that passes the version and sign-in checks, then hangs in the sandbox self-check (`-p`), recording
# its pid in the work area so a test can confirm a shutdown killed it.
SLOW_SELFCHECK_CLAUDE = """#!/usr/bin/env python3
import json, os, sys, time
if "--version" in sys.argv:
    print("2.1.300 (Claude Code)"); sys.exit(0)
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True})); sys.exit(0)
if "-p" in sys.argv:
    with open("selfcheck.pid", "w") as fh:
        fh.write(str(os.getpid()))
    time.sleep(120)
sys.exit(0)
"""


def write_fake_claude(directory: Path) -> Path:
    fake = directory / "claude"
    fake.write_text(FAKE_CLAUDE); fake.chmod(0o755)
    return fake


def write_slow_selfcheck_claude(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "claude"
    fake.write_text(SLOW_SELFCHECK_CLAUDE); fake.chmod(0o755)
    return fake


def stub_ops_env(directory: Path) -> dict[str, str]:
    hook = directory / "stub_ops"; hook.mkdir(exist_ok=True)
    (hook / "sitecustomize.py").write_text(STUB_SITECUSTOMIZE)
    existing = os.environ.get("PYTHONPATH")
    return {"PYTHONPATH": str(hook) if not existing else str(hook) + os.pathsep + existing,
            "FRONTLOT_TEST_REPO": str(REPO)}


def live_endpoint(film: Path, timeout: float = 10) -> tuple[str, str]:
    f = film / "frontlot-work" / "live.json"
    wait_until(f.exists, timeout=timeout, description="fake claude wrote live.json")
    data = json.loads(f.read_text())
    return data["socket"], data["token"]


def post_live(sock: str, token: str, route: str, body: dict, timeout: float = 10) -> dict:
    raw = json.dumps(body).encode()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout); s.connect(sock)
    s.sendall(f"POST {route} HTTP/1.1\r\nHost: frontlot\r\nContent-Type: application/json\r\n"
              f"x-frontlot-token: {token}\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    s.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200"), head
    return json.loads(payload or b"{}")


def hello(sock: str, token: str, epoch: str) -> None:
    post_live(sock, token, "/hello", {"epoch": epoch, "sessionId": "s", "source": "launch", "cwd": "/w",
                                      "claudeVersion": "2.1.294", "history": []})


def stop_brokers(gates: Path, timeout: float = 15) -> None:
    """SIGTERM every broker recorded under this test's private gates dir (never a global pkill)."""
    for ident in (gates / "claude").glob("*.json"):
        if ident.name.count(".") != 1:
            continue  # <slug>.unavailable.json, <slug>.sandbox-ok.json
        try:
            pid = json.loads(ident.read_text())["broker_pid"]
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            continue
        end = time.time() + timeout
        while time.time() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)

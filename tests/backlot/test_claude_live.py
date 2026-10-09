# tests/backlot/test_claude_live.py
import json, os, shutil, signal, tempfile, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import claude_live, server as server_mod, state as state_mod
from scripts.claude_session import proc_started
from tests.backlot.claude_fakes import stop_brokers, stub_ops_env, write_fake_claude

PORT, TOKEN = 4799, "t" * 43
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def app_world(tmp_path, monkeypatch):
    gates = Path(tempfile.mkdtemp(prefix="om-cl-", dir="/tmp"))  # AF_UNIX path limit
    projects = tmp_path / "projects"; film = projects / "film"; film.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(gates))
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", os.path.normcase(str(projects.resolve())))
    monkeypatch.setattr(server_mod, "_summary_cache", {})

    async def no_watch() -> None:
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    monkeypatch.setenv("FRONTLOT_CLAUDE", str(write_fake_claude(tmp_path)))
    monkeypatch.setenv("FRONTLOT_SKIP_PREFLIGHT", "1")
    for k, v in stub_ops_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    app = server_mod.create_app(port=PORT, capability_token=TOKEN)
    yield app, gates, film
    stop_brokers(gates)
    shutil.rmtree(gates, ignore_errors=True)


def ws(client, path):
    return client.websocket_connect(path, headers={"origin": ORIGIN})


def until(w, pred, limit=400):
    for _ in range(limit):
        m = w.receive_json()
        if pred(m):
            return m
    raise AssertionError("message never arrived")


def opened(c, kind, page):
    w = ws(c, "/api/project/film/claude/live").__enter__()
    # Starlette's session.close() only sends the disconnect; its portal task then sleeps until the
    # session's __exit__ cancels it, so a bare close() hangs TestClient's exit. Close the whole session.
    w.close = lambda: w.__exit__(None, None, None)
    w.send_text(json.dumps({"k": TOKEN})); w.send_text(json.dumps({"type": kind, "page": page, "from": 0}))
    return w


def test_state_none_before_start(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        assert c.get("/api/project/film/claude").json() == {"state": "none", "can_resume": False}


def test_bad_origin_and_token_refused(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        with c.websocket_connect("/api/project/film/claude/live", headers={"origin": "http://evil"}) as w:
            with pytest.raises(Exception):
                w.receive_text()
        with ws(c, "/api/project/film/claude/live") as w:
            w.send_text(json.dumps({"k": "wrong"}))
            with pytest.raises(Exception):
                w.receive_text()


def test_start_then_second_socket_is_read_only(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        assert until(a, lambda m: m["type"] == "status")["controller"] is True
        b = opened(c, "attach", "pb")
        assert until(b, lambda m: m["type"] == "status")["controller"] is False
        b.send_text(json.dumps({"type": "submit", "text": "hi"}))
        n = until(b, lambda m: m["type"] == "event" and m["event"].get("kind") == "notice")
        assert n["seq"] is None and "control" in n["event"]["plain"].lower()
        assert c.get("/api/project/film/claude").json()["state"] == "running"
        a.close(); b.close()


def test_new_replaces_only_after_the_previous_broker_exits(app_world):
    app, gates, film = app_world
    ident = gates / "claude" / "film.json"
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        until(a, lambda m: m["type"] == "status")
        first = json.loads(ident.read_text())
        a.send_text(json.dumps({"type": "new"}))
        until(a, lambda m: m["type"] == "status" and m["session_id"] not in (None, first["session_id"]))
        assert proc_started(first["broker_pid"]) is None            # the old broker is gone
        with pytest.raises(ProcessLookupError):
            os.killpg(first["claude_pgid"], 0)                       # and its whole Claude group
        assert json.loads(ident.read_text())["session_id"] != first["session_id"]
        a.close()


def test_a_read_only_tab_cannot_replace_the_conversation(app_world):
    app, gates, film = app_world
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        until(a, lambda m: m["type"] == "status")
        first = json.loads((gates / "claude" / "film.json").read_text())
        b = opened(c, "attach", "pb")
        until(b, lambda m: m["type"] == "status")
        b.send_text(json.dumps({"type": "new"}))
        until(b, lambda m: m["type"] == "event" and m["event"].get("kind") == "notice")
        assert json.loads((gates / "claude" / "film.json").read_text())["session_id"] == first["session_id"]
        a.close(); b.close()


def test_pick_back_up_works_on_an_open_socket(app_world):
    app, *_ = app_world
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        until(a, lambda m: m["type"] == "status")
        a.send_text(json.dumps({"type": "end"}))
        until(a, lambda m: m["type"] == "bye")
        assert c.get("/api/project/film/claude").json() == {"state": "ended", "can_resume": True}
        a.send_text(json.dumps({"type": "resume", "page": "pa", "from": 0}))
        assert until(a, lambda m: m["type"] == "status")["controller"] is True
        assert c.get("/api/project/film/claude").json()["state"] == "running"
        a.close()


def test_unavailable_state_names_the_reason(app_world, monkeypatch):
    app, *_ = app_world
    monkeypatch.setenv("FRONTLOT_CLAUDE", "/nonexistent/claude")
    with TestClient(app) as c:
        a = opened(c, "start", "pa")
        assert until(a, lambda m: m["type"] == "unavailable")["reason"] == "missing"
        assert c.get("/api/project/film/claude").json()["state"] == "unavailable"
        a.close()


def test_reconcile_stale_record_with_reused_pid_is_not_signalled(app_world):
    app, gates, _ = app_world
    d = gates / "claude"; d.mkdir(exist_ok=True)
    from scripts.claude_session import boot_time
    (d / "film.json").write_text(json.dumps({"claude_pid": os.getpid(), "claude_pgid": os.getpgid(0),
                                             "claude_started": 1.0, "boot_time": boot_time()}))  # same boot, wrong start
    assert claude_live.reconcile_orphans("film") == "stale-record"
    assert not (d / "film.json").exists()


def _owned_group(gates):
    """A recorded process group with a leader and one descendant (like Claude and a Bash child)."""
    import subprocess
    from scripts.claude_session import boot_time
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 300 & exec sleep 300"], start_new_session=True)
    time.sleep(0.3)
    d = gates / "claude"; d.mkdir(exist_ok=True)
    (d / "film.json").write_text(json.dumps({"claude_pid": p.pid, "claude_pgid": p.pid, "boot_time": boot_time(),
                                             "claude_started": proc_started(p.pid)}))
    return p, d


def test_reconcile_terminates_the_whole_owned_group(app_world):
    app, gates, _ = app_world
    p, d = _owned_group(gates)
    assert claude_live.reconcile_orphans("film") == "terminated"
    with pytest.raises(ProcessLookupError):
        os.killpg(p.pid, 0)
    assert not (d / "film.json").exists()


def test_reconcile_refuses_replacement_while_an_unprovable_group_survives(app_world):
    app, gates, _ = app_world
    p, d = _owned_group(gates)
    os.kill(p.pid, 9); p.wait()                         # leader gone, its child still in the group
    assert claude_live.reconcile_orphans("film") == "stuck"
    assert (d / "film.json").exists()                   # record kept; no replacement may start
    os.killpg(p.pid, 9); time.sleep(0.3)
    assert claude_live.reconcile_orphans("film") == "clean"
    assert not (d / "film.json").exists()


def test_reconcile_removes_sockets_nobody_owns(app_world):
    app, gates, _ = app_world
    d = gates / "claude"; d.mkdir(exist_ok=True)
    (d / "film.sock").write_text(""); (d / "film.live.sock").write_text("")
    assert claude_live.reconcile_orphans("film") == "clean"
    assert not (d / "film.sock").exists() and not (d / "film.live.sock").exists()

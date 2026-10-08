import json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

import pytest

from backlot import claude_frames as cf
from tests.backlot.claude_fakes import REPO, hello, live_endpoint, post_live, stub_ops_env, write_fake_claude
from tests.backlot.tty_helpers import recv_frame, wait_until


@pytest.fixture
def world(tmp_path):
    gates = Path(tempfile.mkdtemp(prefix="omcs-", dir="/tmp"))  # AF_UNIX path limit (~104 bytes)
    projects = tmp_path / "projects"; film = projects / "film"; film.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    env = dict(os.environ, OPENMONTAGE_GATES_DIR=str(gates), OPENMONTAGE_PROJECTS_DIR=str(projects),
               FRONTLOT_CLAUDE=str(write_fake_claude(tmp_path)), FRONTLOT_SKIP_PREFLIGHT="1",
               FRONTLOT_LEASE_SECONDS="1", FRONTLOT_STOP_NOTICE_SECONDS="1", FRONTLOT_REDELIVER_SECONDS="1", **stub_ops_env(tmp_path))
    procs: list[subprocess.Popen] = []
    yield {"env": env, "gates": gates, "film": film, "procs": procs, "dir": gates / "claude"}
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(5)
    shutil.rmtree(gates, ignore_errors=True)


def kill_group(pgid):
    """The broker's death hangs up Claude's terminal, so its group may already be gone (SIGHUP), or be only
    zombies awaiting launchd's reaping, which macOS reports as EPERM."""
    try:
        os.killpg(pgid, 9)
    except (ProcessLookupError, PermissionError):
        pass


def start(world, *extra, env=None):
    p = subprocess.Popen([sys.executable, "scripts/claude_session.py", "--broker", "--project", "film", *extra],
                         cwd=REPO, env=env or world["env"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    world["procs"].append(p)
    return p


def connect(world, *, page="pa", subscribe_from=0, controller=True):
    sock_path = world["dir"] / "film.sock"
    wait_until(sock_path.exists, timeout=10, description="broker socket")
    s = socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect(str(sock_path))
    s.sendall(cf.encode_json(cf.HELLO, {"subscribe_from": subscribe_from, "controller": controller, "page": page}))
    return s


def next_of(s, want_type, pred=lambda d: True):
    while True:
        t, payload = recv_frame(s)
        if t == want_type and (want_type == cf.OUT or pred(json.loads(payload))):
            return payload if want_type == cf.OUT else json.loads(payload)


def status(s):
    return next_of(s, cf.STATUS)


def test_identity_written_before_socket_published(world):
    start(world)
    wait_until((world["dir"] / "film.sock").exists, timeout=10, description="broker socket")
    data = json.loads((world["dir"] / "film.json").read_text())
    for k in ("session_id", "broker_pid", "broker_started", "claude_pid", "claude_pgid", "claude_started", "boot_time"):
        assert k in data
    assert json.loads((world["dir"] / "film.session").read_text())["session_id"] == data["session_id"]


def test_pty_output_and_input_relay(world):
    start(world)
    s = connect(world)
    s.sendall(cf.encode(cf.IN, b"hello\n"))
    buf, end = b"", time.time() + 10
    while b"echo:hello" not in buf and time.time() < end:
        t, payload = recv_frame(s)
        if t == cf.OUT:
            buf += payload
    assert b"echo:hello" in buf


def test_second_client_is_read_only_and_told_why(world):
    start(world)
    a = connect(world, page="pa"); assert status(a)["controller"] is True
    b = connect(world, page="pb"); assert status(b)["controller"] is False
    b.sendall(cf.encode_json(cf.ACTION, {"type": "submit", "text": "hi"}))
    n = next_of(b, cf.EVENT, lambda d: d["event"].get("kind") == "notice")
    assert n["seq"] is None and "control" in n["event"]["plain"].lower()


def test_controller_lease_survives_a_short_disconnect_then_expires(world):
    start(world)
    a = connect(world, page="pa"); assert status(a)["controller"] is True
    a.close(); time.sleep(0.2)
    b = connect(world, page="pb"); assert status(b)["controller"] is False      # held for pa
    a2 = connect(world, page="pa"); assert status(a2)["controller"] is True     # same page regains it
    a2.close(); time.sleep(1.5)                                                 # FRONTLOT_LEASE_SECONDS=1
    c = connect(world, page="pc"); assert status(c)["controller"] is True


def test_end_kills_claude_group_and_cleans_up(world):
    p = start(world)
    s = connect(world)
    ident = json.loads((world["dir"] / "film.json").read_text())
    s.sendall(cf.encode_json(cf.ACTION, {"type": "end"}))
    p.wait(timeout=20)
    with pytest.raises(ProcessLookupError):
        os.killpg(ident["claude_pgid"], 0)
    assert not (world["dir"] / "film.sock").exists() and not (world["dir"] / "film.json").exists()
    assert (world["dir"] / "film.session").exists()  # Pick back up still possible


def test_sigterm_shuts_down_cleanly(world):
    p = start(world)
    connect(world)
    p.terminate()
    p.wait(timeout=20)
    assert not (world["dir"] / "film.sock").exists() and not (world["dir"] / "film.live.sock").exists()


def test_unavailable_is_written_when_claude_is_missing(world):
    env = dict(world["env"], FRONTLOT_CLAUDE="/nonexistent/claude")
    p = start(world, env=env)
    assert p.wait(timeout=20) == 3
    assert json.loads((world["dir"] / "film.unavailable.json").read_text())["reason"] == "missing"
    assert not (world["dir"] / "film.sock").exists()


def test_run_is_idempotent_by_key_and_unknown_keys_are_not_received(world):
    start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    a = post_live(sock, token, "/run", {"key": "k1", "op": "test_paid", "params": {}, "epoch": "e1", "turnId": ""})
    b = post_live(sock, token, "/run", {"key": "k1", "op": "test_free", "params": {}, "epoch": "e1", "turnId": ""})
    assert a["status"] == "waiting-for-ben" and b["requestId"] == a["requestId"] and b["status"] == "waiting-for-ben"
    assert post_live(sock, token, "/run", {"key": "k2", "op": "rm_rf", "params": {}, "epoch": "e1", "turnId": ""})["status"] == "refused"
    assert post_live(sock, token, "/run-check", {"key": "k2"})["status"] == "not-received"
    assert post_live(sock, token, "/run-check", {"key": "k1"})["status"] == "waiting-for-ben"


def test_a_second_hello_cancels_unstarted_requests(world):
    start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    assert post_live(sock, token, "/run", {"key": "k1", "op": "test_paid", "params": {}, "epoch": "e1", "turnId": ""})["status"] == "waiting-for-ben"
    hello(sock, token, "e2")  # add-on reloaded
    assert post_live(sock, token, "/run-check", {"key": "k1"})["status"] == "cancelled"


def run(sock, token, key, *, epoch="e1", turn="", seq=1, op="test_paid"):
    return post_live(sock, token, "/run", {"key": key, "op": op, "params": {}, "epoch": epoch, "turnId": turn, "turnSeq": seq})


def test_requests_from_an_old_epoch_or_a_stopped_turn_are_refused(world):
    start(world)
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    assert run(sock, token, "k0", epoch="e0")["status"] == "refused"
    # Stop arrives before any turn-start report (the add-on queues reports asynchronously)
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))
    time.sleep(0.3)
    assert run(sock, token, "k1", turn="t1")["status"] == "refused"          # Stop not yet answered
    stop = post_live(sock, token, "/inbox", {"epoch": "e1"})
    assert stop["stop"] == {"turnId": "*"}
    post_live(sock, token, "/inbox-ack", {"id": stop["id"], "status": "submitted", "reason": "stopped-through:1"})
    assert run(sock, token, "k2", turn="t1", seq=1)["status"] == "refused"   # the aborted turn, late
    assert post_live(sock, token, "/run-check", {"key": "k2"})["status"] == "not-received"
    assert run(sock, token, "k3", turn="t2", seq=2)["status"] == "waiting-for-ben"  # a turn that began after


def test_a_turn_that_ended_before_the_stop_arrived_is_still_retired(world):
    start(world)
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))
    stop = post_live(sock, token, "/inbox", {"epoch": "e1"})
    # t1 completed before delivery: nothing to abort, but the ack still names the newest turn started
    post_live(sock, token, "/inbox-ack", {"id": stop["id"], "status": "rejected", "reason": "stopped-through:1"})
    assert run(sock, token, "k1", turn="t1", seq=1)["status"] == "refused"   # its delayed request
    assert run(sock, token, "k2", turn="t2", seq=2)["status"] == "waiting-for-ben"


def test_a_stop_from_an_ended_epoch_is_never_delivered(world):
    start(world)
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))
    assert post_live(sock, token, "/inbox", {"epoch": "e1"})["stop"]        # delivered, never acked
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))                   # a second one, still queued
    time.sleep(0.3)
    hello(sock, token, "e2")                                                 # reload retires both
    time.sleep(1.2)                                                          # past FRONTLOT_REDELIVER_SECONDS
    s.sendall(cf.encode_json(cf.ACTION, {"type": "submit", "text": "after reload"}))
    got = post_live(sock, token, "/inbox", {"epoch": "e2"})
    assert "stop" not in got and got["submit"] == "after reload" and got["epoch"] == "e2"
    assert run(sock, token, "k1", epoch="e2", seq=1)["status"] == "waiting-for-ben"  # nothing blocks the new epoch


def test_stop_wakes_a_waiting_poll(world):
    import threading
    start(world)
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    got = {}
    th = threading.Thread(target=lambda: got.update(post_live(sock, token, "/inbox", {"epoch": "e1"}, timeout=40)))
    th.start(); time.sleep(0.5)                                              # the add-on's poll is already waiting
    t0 = time.time()
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))
    th.join(40)
    assert got.get("stop") == {"turnId": "*"} and time.time() - t0 < 3


def test_an_unconfirmed_stop_keeps_runs_blocked_and_tells_ben(world):
    start(world)                                                             # FRONTLOT_STOP_NOTICE_SECONDS=1
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))
    n = next_of(s, cf.EVENT, lambda d: "confirmed it stopped" in d["event"].get("plain", ""))
    assert "new conversation" in n["event"]["plain"]
    time.sleep(1.5)
    assert run(sock, token, "k1", turn="t1")["status"] == "refused"          # never unblocked by time alone


def test_a_lost_stop_ack_is_replayed_with_its_floor(world):
    start(world)                                                             # FRONTLOT_REDELIVER_SECONDS=1
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    s.sendall(cf.encode_json(cf.ACTION, {"type": "stop"}))
    first = post_live(sock, token, "/inbox", {"epoch": "e1"})                # the add-on aborts t1; its ack is lost
    time.sleep(1.2)
    again = post_live(sock, token, "/inbox", {"epoch": "e1"})                # redelivered
    assert again["id"] == first["id"]
    # the add-on replays its cached final ack, floor included (Task 7)
    post_live(sock, token, "/inbox-ack", {"id": again["id"], "status": "submitted", "reason": "stopped-through:1"})
    assert run(sock, token, "k1", turn="t1", seq=1)["status"] == "refused"
    assert run(sock, token, "k2", turn="t2", seq=2)["status"] == "waiting-for-ben"


def test_a_poll_from_an_ended_epoch_never_eats_a_message(world):
    import threading
    start(world)
    s = connect(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    old = {}
    th = threading.Thread(target=lambda: old.update(post_live(sock, token, "/inbox", {"epoch": "e1"}, timeout=40)))
    th.start(); time.sleep(0.5)
    hello(sock, token, "e2")                                                 # reload while the old poll waits
    s.sendall(cf.encode_json(cf.ACTION, {"type": "submit", "text": "hello again"}))
    th.join(40)
    assert "submit" not in old
    assert post_live(sock, token, "/inbox", {"epoch": "e2"})["submit"] == "hello again"


def test_outcome_messages_are_resent_after_a_crash_until_acked(world):
    p = start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    run(sock, token, "k1", op="test_free")
    msg = post_live(sock, token, "/inbox", {"epoch": "e1"})                  # delivered, never acked
    assert msg["submit"].startswith("[Front Lot]")
    ident = json.loads((world["dir"] / "film.json").read_text())
    p.kill(); p.wait(10); kill_group(ident["claude_pgid"])
    (world["film"] / "frontlot-work" / "live.json").unlink()
    start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e2")
    again = post_live(sock, token, "/inbox", {"epoch": "e2"})
    assert again["submit"] == msg["submit"]
    post_live(sock, token, "/inbox-ack", {"id": again["id"], "status": "submitted"})
    time.sleep(2.5)                                                          # a watcher pass
    store_dir = world["gates"] / "claude" / "film" / "requests"
    assert all(json.loads(f.read_text()).get("notified") == "done" for f in store_dir.glob("r-*.json")
               if not f.name.endswith(".outcome.json"))


def test_a_new_broker_cancels_what_a_killed_broker_left_waiting(world):
    p = start(world)
    sock, token = live_endpoint(world["film"])
    hello(sock, token, "e1")
    post_live(sock, token, "/run", {"key": "k1", "op": "test_paid", "params": {}, "epoch": "e1", "turnId": ""})
    ident = json.loads((world["dir"] / "film.json").read_text())
    p.kill(); p.wait(10)                                   # no shutdown ran
    kill_group(ident["claude_pgid"])                       # the server's reconcile does this in production (Task 9)
    (world["film"] / "frontlot-work" / "live.json").unlink()
    start(world)
    sock, token = live_endpoint(world["film"])
    assert post_live(sock, token, "/run-check", {"key": "k1"})["status"] == "cancelled"


def test_control_belongs_to_a_page_across_its_connections(world):
    start(world)
    a1 = connect(world, page="pa"); assert status(a1)["controller"] is True
    a2 = connect(world, page="pa"); assert status(a2)["controller"] is True     # e.g. the page's tty socket
    a1.close(); time.sleep(1.5)                                                 # past FRONTLOT_LEASE_SECONDS
    b = connect(world, page="pb"); assert status(b)["controller"] is False      # pa still has a connection
    b.sendall(cf.encode_json(cf.ACTION, {"type": "take-control"}))
    assert status(a2)["controller"] is False                                    # every pa connection demoted
    assert status(b)["controller"] is True


def test_only_the_controlling_page_can_replace_the_conversation(world):
    p = start(world)
    a = connect(world, page="pa"); status(a)
    b = connect(world, page="pb"); status(b)
    b.sendall(cf.encode_json(cf.ACTION, {"type": "new"}))
    assert "control" in next_of(b, cf.EVENT, lambda d: d["event"].get("kind") == "notice")["event"]["plain"].lower()
    assert p.poll() is None
    a.sendall(cf.encode_json(cf.ACTION, {"type": "new"}))
    assert next_of(a, cf.BYE) == {"reason": "new", "page": "pa"}
    p.wait(timeout=20)


@pytest.mark.parametrize("stage", ["before-gate", "after-gate"])
def test_a_failed_start_leaves_nothing_behind(world, stage):
    p = start(world, env=dict(world["env"], FRONTLOT_TEST_FAIL_AT=stage))
    p.wait(timeout=30)
    for name in ("film.sock", "film.live.sock", "film.json"):
        assert not (world["dir"] / name).exists(), name
    live = world["film"] / "frontlot-work" / "live.json"
    if stage == "before-gate":
        assert not live.exists()                           # the gate never opened: Claude never ran
    elif live.exists():                                    # it ran briefly: its whole group must be gone
        with pytest.raises(ProcessLookupError):
            os.killpg(json.loads(live.read_text())["pgid"], 0)

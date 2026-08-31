"""Real-process and real-PTY tests for the detached gate-signing wrapper."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backlot.tty import (
    BYE,
    HELLO,
    IN,
    MAX_IN,
    MAX_JSON,
    MAX_OUT,
    OUT,
    RESIZE,
    STATUS,
    decode_json_payload,
    encode_frame,
    encode_json_frame,
    session_paths,
    valid_resize,
)
from lib import run_lease
from scripts import gate_sign
from tests.backlot.tty_helpers import (
    DisposableResources,
    REQUEST,
    _WRAPPER_BOOTSTRAP,
    add_real_signer_self_expiry,
    attach,
    attach_when_free,
    collect_until_bye,
    connect,
    fake_signer,
    finish_broker,
    launch_fake_broker,
    process_env,
    read_pty_until_exit,
    read_pty_until_marker,
    recv_frame,
    wait_for_terminal_output,
    wait_until,
    write_project,
)
from tests.lib.look_lock_helpers import PROJECT


@pytest.fixture
def broker_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    projects = tmp_path / "projects"
    root, _ = write_project(projects)
    gates = Path(tempfile.mkdtemp(prefix="om-gates-", dir="/tmp"))
    signer = fake_signer(tmp_path)
    owner = DisposableResources()
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(gates))
    try:
        yield {"projects": projects, "project": root, "gates": gates, "signer": signer, "owner": owner}
    finally:
        try:
            owner.cleanup()
        finally:
            shutil.rmtree(gates, ignore_errors=True)


def _bye_reason(sock: socket.socket) -> str:
    while True:
        frame_type, payload = recv_frame(sock)
        if frame_type == BYE:
            reason = decode_json_payload(BYE, payload).get("reason")
            assert sock.recv(1) == b""
            return str(reason)
        assert frame_type in (OUT, STATUS)


def test_disposable_resources_last_resort_reaps_exact_wrapper_and_closes_all_fds() -> None:
    owner = DisposableResources()
    master, slave = pty.openpty()
    owner.track_fd(master)
    owner.track_fd(slave)
    process = owner.track_process(
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ),
        expires_after=0.05,
        wrapper_last_resort=True,
        label="test wrapper",
    )

    with pytest.raises(AssertionError, match="required exact-wrapper last-resort cleanup"):
        owner.cleanup()

    assert process.poll() is not None
    for descriptor in (master, slave):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    owner.cleanup()


def test_fake_signer_uses_real_pty_and_exits_only_on_explicit_human_byte(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    sock, hello = attach(path)
    assert hello == {
        "project": PROJECT,
        "request_id": REQUEST,
        "session_id": hello["session_id"],
        "pid": process.pid,
        "lease_held": True,
        "signer": "running",
    }
    initial_output = wait_for_terminal_output(sock)
    sock.sendall(encode_frame(IN, b"y"))
    output, events = collect_until_bye(sock)
    sock.close()
    wrapper_output = process.communicate(timeout=8)[0]

    assert b"TTY_OK" in initial_output
    assert b"HUMAN_BYTE:y" in b"".join(output)
    assert events[-2:] == [
        {"frame_type": STATUS, "signer": "exited", "exit_code": 0},
        {"frame_type": BYE, "reason": "exited", "exit_code": 0},
    ]
    assert process.returncode == 0, wrapper_output.decode(errors="replace")


def test_disconnect_injects_nothing_and_broker_remains_attachable_with_replay(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"

    first, _ = attach(path)
    first.close()
    second, _ = attach_when_free(path)
    assert process.poll() is None
    frame_type, replay = recv_frame(second)
    assert frame_type == OUT and b"TTY_OK" in replay
    second.sendall(encode_frame(IN, b"y"))
    output, events = collect_until_bye(second)
    second.close()
    broker_log = process.communicate(timeout=8)[0]

    assert b"UNEXPECTED_BYTE" not in replay + b"".join(output) + broker_log
    assert any(event.get("exit_code") == 0 for event in events)
    assert process.returncode == 0


def test_second_client_and_two_client_race_are_refused_without_pty_access(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"

    racers = [connect(path), connect(path)]
    first_frames = [recv_frame(sock) for sock in racers]
    assert sorted(frame_type for frame_type, _ in first_frames) == [HELLO, BYE]
    busy_index = next(i for i, (frame_type, _) in enumerate(first_frames) if frame_type == BYE)
    assert decode_json_payload(BYE, first_frames[busy_index][1]) == {"reason": "busy"}
    winner_index = 1 - busy_index
    winner = racers[winner_index]
    winner.sendall(encode_json_frame(HELLO, {"project": PROJECT, "request_id": REQUEST, "token_ok": True}))
    racers[busy_index].close()

    third = connect(path)
    assert decode_json_payload(BYE, recv_frame(third)[1]) == {"reason": "busy"}
    third.close()
    finish_broker(path, process, winner)


@pytest.mark.parametrize(
    "bad_frame",
    [
        struct.pack("!BI", 99, 0),
        struct.pack("!BI", IN, MAX_IN + 1),
        struct.pack("!BI", OUT, MAX_OUT + 1),
        struct.pack("!BI", RESIZE, MAX_JSON + 1),
        encode_frame(RESIZE, b"{"),
    ],
    ids=["unknown-type", "oversize-in", "oversize-out", "oversize-json", "malformed-json"],
)
def test_broker_protocol_violations_send_protocol_bye_and_close(broker_world, bad_frame: bytes) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    sock, _ = attach(path)
    sock.sendall(bad_frame)
    assert _bye_reason(sock) == "protocol"
    sock.close()
    finish_broker(path, process)


def test_truncated_frame_sends_protocol_bye_and_closes(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    sock, _ = attach(path)
    sock.sendall(struct.pack("!BI", IN, 3) + b"x")
    sock.shutdown(socket.SHUT_WR)
    assert _bye_reason(sock) == "protocol"
    sock.close()
    finish_broker(path, process)


@pytest.mark.parametrize(
    ("client_hello", "expected"),
    [
        (encode_frame(IN, b"y"), "protocol"),
        (encode_json_frame(HELLO, {"project": "other", "request_id": REQUEST, "token_ok": True}), "mismatch"),
        (encode_json_frame(HELLO, {"project": PROJECT, "request_id": "other", "token_ok": True}), "mismatch"),
        (encode_json_frame(HELLO, {"project": PROJECT, "request_id": REQUEST, "token_ok": False}), "mismatch"),
        (encode_json_frame(HELLO, {"request_id": REQUEST, "token_ok": True}), "mismatch"),
        (encode_json_frame(HELLO, {"project": PROJECT, "token_ok": True}), "mismatch"),
        (encode_json_frame(HELLO, {"project": PROJECT, "request_id": REQUEST}), "mismatch"),
        (encode_frame(HELLO, b"[]"), "protocol"),
        (encode_frame(HELLO, b"{"), "protocol"),
    ],
    ids=[
        "first-frame-not-hello",
        "wrong-project",
        "wrong-request",
        "false-token",
        "missing-project",
        "missing-request",
        "missing-token",
        "nonobject",
        "malformed-json",
    ],
)
def test_client_first_identity_contract_refuses_wrong_frames(broker_world, client_hello: bytes, expected: str) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    sock = connect(path)
    assert recv_frame(sock)[0] == HELLO
    sock.sendall(client_hello)
    assert _bye_reason(sock) == expected
    sock.close()
    finish_broker(path, process)


def test_client_hello_refuses_extra_keys_as_identity_mismatch(broker_world) -> None:
    """The authenticated client identity is an exact three-key wire contract."""
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    sock = connect(path)
    assert recv_frame(sock)[0] == HELLO
    sock.sendall(
        encode_json_frame(
            HELLO,
            {"project": PROJECT, "request_id": REQUEST, "token_ok": True, "extra": "refuse-me"},
        )
        + encode_frame(IN, b"y")
    )
    reason = _bye_reason(sock)
    sock.close()
    if process.poll() is None:
        finish_broker(path, process)
    else:
        process.communicate(timeout=8)
    assert reason == "mismatch"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"cols": 10, "rows": 3}, (10, 3)),
        ({"cols": 500, "rows": 300}, (500, 300)),
        ({"cols": 9, "rows": 3}, None),
        ({"cols": 501, "rows": 3}, None),
        ({"cols": 10, "rows": 2}, None),
        ({"cols": 10, "rows": 301}, None),
        ({"cols": True, "rows": 24}, None),
        ({"cols": 80.0, "rows": 24}, None),
    ],
)
def test_resize_bounds_are_inclusive_and_invalid_values_are_safe(payload, expected) -> None:
    assert valid_resize(payload) == expected


def test_invalid_resize_frame_is_closed_as_protocol_error(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    sock, _ = attach(path)
    sock.sendall(encode_json_frame(RESIZE, {"cols": 9, "rows": 24}))
    assert _bye_reason(sock) == "protocol"
    sock.close()
    finish_broker(path, process)


def test_real_broker_sanitizes_split_hostile_output_replay_and_applies_inclusive_resize(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(
        w["projects"],
        w["gates"],
        w["signer"],
        owner=w["owner"],
        OM_HOSTILE_SPLIT="1",
        OM_EXPECT_COLS="10",
        OM_EXPECT_ROWS="3",
    )
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    first, _ = attach(path)
    prefix = wait_for_terminal_output(first, marker=b"SPLIT_BEFORE")
    first.sendall(encode_json_frame(RESIZE, {"cols": 10, "rows": 3}))
    suffix = wait_for_terminal_output(first, marker=b"RESIZE_OBSERVED:10x3")
    first.close()

    second, _ = attach_when_free(path)
    frame_type, replay = recv_frame(second)
    assert frame_type == OUT
    second.sendall(encode_frame(IN, b"y"))
    final_output, events = collect_until_bye(second)
    second.close()
    broker_log = process.communicate(timeout=8)[0]

    observed = prefix + suffix + replay + b"".join(final_output) + broker_log
    assert b"SPLIT_BEFORE" in replay and b"SPLIT_AFTER" in replay
    assert b"RESIZE_OBSERVED:10x3" in replay
    assert b"HOSTILE" not in observed
    assert b"\x1b]52" not in observed
    assert b"UNEXPECTED_SIGNAL" not in observed and b"UNEXPECTED_BYTE" not in observed
    assert events[-1] == {"frame_type": BYE, "reason": "exited", "exit_code": 0}


def test_stale_socket_is_untouched_under_independent_flock_then_replaced_after_release(broker_world) -> None:
    w = broker_world
    session_dir = w["gates"] / "sessions"
    session_dir.mkdir(parents=True)
    stale_path = session_dir / f"{PROJECT}.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(stale_path))
    stale.close()
    sidecar_path = session_dir / f"{PROJECT}.session.json"
    stale_sidecar = b"stale-sidecar-sentinel"
    sidecar_path.write_bytes(stale_sidecar)
    stale_inode = stale_path.lstat().st_ino

    ready = w["gates"] / "holder-ready"
    holder_code = r"""
import os
import time
from pathlib import Path
from scripts import gate_sign
with gate_sign._project_flock(os.environ["OM_PROJECT"]):
    Path(os.environ["OM_READY"]).write_text("held", encoding="utf-8")
    time.sleep(1.0)
"""
    env = process_env(w["projects"], w["gates"], w["signer"])
    env["OM_READY"] = str(ready)
    holder = w["owner"].track_process(
        subprocess.Popen(
            [sys.executable, "-c", holder_code],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ),
        expires_after=3.0,
    )
    wait_until(ready.exists, description="independent project flock")

    contender_env = dict(env)
    contender_env["OM_BROKER"] = "1"
    contender = subprocess.run(
        [sys.executable, "-c", _WRAPPER_BOOTSTRAP],
        cwd=Path(__file__).resolve().parents[2],
        env=contender_env,
        capture_output=True,
        timeout=3,
    )
    assert contender.returncode == 6
    assert stale_path.lstat().st_ino == stale_inode
    assert sidecar_path.read_bytes() == stale_sidecar

    assert holder.wait(timeout=3) == 0

    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    paths = {name: w["gates"] / "sessions" / f"{PROJECT}{suffix}" for name, suffix in {
        "socket": ".sock", "sidecar": ".session.json", "lock": ".lock"
    }.items()}
    assert stat.S_IMODE(session_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths["socket"].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths["sidecar"].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths["lock"].stat().st_mode) == 0o600
    assert paths["socket"].lstat().st_ino != stale_inode
    assert paths["sidecar"].read_bytes() != stale_sidecar
    finish_broker(paths["socket"], process)


def test_broker_exit_unlinks_session_and_releases_lease_and_flock(broker_world) -> None:
    w = broker_world
    process = launch_fake_broker(w["projects"], w["gates"], w["signer"], owner=w["owner"])
    path = w["gates"] / "sessions" / f"{PROJECT}.sock"
    finish_broker(path, process)
    assert not path.exists()
    assert not (w["gates"] / "sessions" / f"{PROJECT}.session.json").exists()
    assert not (w["project"] / ".run-lease").exists()
    with gate_sign._project_flock(PROJECT):
        pass


def test_direct_wrapper_with_live_lease_prints_exact_wait_message_and_exits_5(broker_world) -> None:
    w = broker_world
    env = process_env(w["projects"], w["gates"], w["signer"])
    env["OM_BROKER"] = "1"
    with run_lease.acquire(w["project"], 10):
        result = subprocess.run(
            [sys.executable, "-c", _WRAPPER_BOOTSTRAP],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
    assert result.returncode == 5
    assert result.stdout == f"a run holds the project lease (pid {os.getpid()}); wait for it to block or finish\n"
    assert result.stderr == ""


def test_foreground_wrapper_spawns_child_and_waits_inside_lease_and_flock(broker_world) -> None:
    w = broker_world
    env = process_env(w["projects"], w["gates"], w["signer"])
    env.update({"OM_PROJECT_ROOT": str(w["project"]), "OM_CHECK_CONTEXTS": "1", "OM_SIGNER_EXIT": "7"})
    master, slave = pty.openpty()
    w["owner"].track_fd(master)
    w["owner"].track_fd(slave)
    process = None
    try:
        process = w["owner"].track_process(
            subprocess.Popen(
                [sys.executable, "-c", _WRAPPER_BOOTSTRAP],
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
            ),
            expires_after=8.0,
            wrapper_last_resort=True,
            label="foreground fake-signer wrapper",
        )
        w["owner"].close_fd(slave)
        slave = -1
        prefix = read_pty_until_marker(process, master, b"TTY_OK")
        os.write(master, b"y")
        output = prefix + read_pty_until_exit(process, master)
    finally:
        if process is not None and process.poll() is not None:
            w["owner"].close_fd(master)
        if slave >= 0:
            w["owner"].close_fd(slave)
    assert process.returncode == 7, output.decode(errors="replace")
    assert f"TTY_OK PARENT:{process.pid}".encode() in output
    assert b"HUMAN_BYTE:y" in output
    assert b"UNEXPECTED_SIGNAL" not in output and b"UNEXPECTED_BYTE" not in output
    assert not (w["project"] / ".run-lease").exists()
    with gate_sign._project_flock(PROJECT):
        pass


def test_nonblocking_project_flock_refuses_a_second_independent_process(broker_world) -> None:
    w = broker_world
    holder_code = r"""
import os, time
from scripts import gate_sign
with gate_sign._project_flock(os.environ["OM_PROJECT"]):
    print("LOCKED", flush=True)
    time.sleep(1.0)
"""
    contender_code = r"""
import os
from scripts import gate_sign
from lib.run_common import RunError
try:
    with gate_sign._project_flock(os.environ["OM_PROJECT"]):
        print("ACQUIRED")
except RunError as exc:
    print(str(exc))
    raise SystemExit(6)
"""
    env = process_env(w["projects"], w["gates"], w["signer"])
    holder = w["owner"].track_process(
        subprocess.Popen(
            [sys.executable, "-c", holder_code],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
        expires_after=3.0,
    )
    assert holder.stdout is not None
    wait_until(lambda: bool(select.select([holder.stdout], [], [], 0)[0]), description="flock holder readiness")
    assert holder.stdout.readline().strip() == "LOCKED"
    contender = subprocess.run([sys.executable, "-c", contender_code], cwd=Path(__file__).resolve().parents[2], env=env,
                               capture_output=True, text=True, timeout=3)
    assert holder.wait(timeout=3) == 0
    assert contender.returncode == 6
    assert contender.stdout == "a signing terminal is already open for this project\n"


def test_real_signer_config_gate_under_pty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mandatory proof: real gate_sign -> real gate_approve under a hermetic PTY."""
    projects = tmp_path / "projects"
    project, request = write_project(projects)
    gates = tmp_path / "external-gates"
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(gates))
    env = add_real_signer_self_expiry(tmp_path, process_env(projects, gates), timeout=18.0)
    owner = DisposableResources()
    master, slave = pty.openpty()
    owner.track_fd(master)
    owner.track_fd(slave)
    process = None
    try:
        process = owner.track_process(
            subprocess.Popen(
                [sys.executable, "scripts/gate_sign.py", "--project", PROJECT, "--request", REQUEST],
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
            ),
            expires_after=22.0,
            wrapper_last_resort=True,
            label="real gate-sign wrapper",
        )
        owner.close_fd(slave)
        slave = -1
        approve_prompt = b"Approve? y to sign, n to decline, q to quit without deciding: "
        output = read_pty_until_marker(process, master, approve_prompt, timeout=15)
        assert process.poll() is None
        os.write(master, b"y\n")
        output += read_pty_until_marker(process, master, b"Note (optional): ", timeout=5)
        assert process.poll() is None and b"approved" not in output.lower()
        os.write(master, b"\n")
        output += read_pty_until_exit(process, master, timeout=15)
    finally:
        if process is not None and process.poll() is not None:
            owner.close_fd(master)
        if slave >= 0:
            owner.close_fd(slave)
        owner.cleanup()

    assert process.returncode == 0, output.decode(errors="replace")
    assert b"UNEXPECTED_SIGNAL" not in output
    assert b"approved -- receipt" in output.replace(b"\xe2\x80\x94", b"--")
    rows = [json.loads(line) for line in (project / "approvals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "config"
    done_path = project / ".gate-requests" / "done" / f"{REQUEST}.json"
    done = json.loads(done_path.read_text(encoding="utf-8"))
    assert done["approval_receipt_id"] == rows[0]["receipt_id"]
    assert not (project / ".gate-requests" / f"{REQUEST}.json").exists()
    assert not (project / ".run-lease").exists()
    assert str(project).startswith(str(tmp_path)) and str(gates).startswith(str(tmp_path))
    with gate_sign._project_flock(PROJECT):
        pass

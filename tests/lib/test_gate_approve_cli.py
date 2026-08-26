import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import gate_approve  # noqa: E402
from lib.receipts import find_approval  # noqa: E402
from lib.canonical_json import record_sha256  # noqa: E402
from tests.lib.look_lock_helpers import approve_request, decline_request  # noqa: E402


@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / "gates"))


PROJECT_CONFIG = {
    "version": "1.0", "budget_usd_cap": 50.0, "wall_time_minutes": 30,
    "cast_cap": {"characters": 2, "locations": 2},
    "default_video_endpoint": "fal-ai/kling-video/o3/pro/reference-to-video",
    "provider_egress": {"provider": "fal", "content_classes": ["prompts", "reference_images"]},
}


def _project(tmp_path):
    import hashlib

    import yaml

    root = tmp_path / "projects" / "demo"
    (root / ".gate-requests").mkdir(parents=True)
    raw = yaml.safe_dump(PROJECT_CONFIG, sort_keys=True).encode("utf-8")
    (root / "project.yaml").write_bytes(raw)
    req = {
        "request_id": "req-1", "project_id": "demo", "stage": "proposal",
        "scope": "config", "kind": "config",
        "entity_id": "project_config", "artifact": None,
        "approval_record": {"config_sha256": hashlib.sha256(raw).hexdigest()},
        "source_checkpoint_digest": None, "summary": "Approve project.yaml.", "preview_paths": [],
    }
    (root / ".gate-requests" / "req-1.json").write_text(json.dumps(req))
    return root, req


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def human_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())


def test_refuses_without_tty():
    with pytest.raises(gate_approve.GateHandlerError):
        gate_approve.require_tty(io.StringIO())


def test_decide_refuses_without_tty(tmp_path, monkeypatch):
    """_decide() is not a bypass around main()'s TTY check (#1)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    root, req = _project(tmp_path)
    with pytest.raises(gate_approve.GateHandlerError, match="interactive terminal"):
        approve_request(req, root, note=None)
    assert not (root / "approvals.jsonl").exists()
    assert (root / ".gate-requests" / "req-1.json").exists()


def test_main_exits_2_without_tty(tmp_path, monkeypatch, capsys):
    root, _ = _project(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    rc = gate_approve.main(["--project", "demo", "--projects-dir", str(tmp_path / "projects")])
    assert rc == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_approve_writes_verified_receipt_and_moves_request(tmp_path, human_tty):
    root, req = _project(tmp_path)
    receipt = approve_request(req, root, note="looks right")
    assert receipt["kind"] == "config"
    found = find_approval(root, "config", record_sha256=record_sha256(req["approval_record"]))
    assert found and found["receipt_id"] == receipt["receipt_id"]
    assert not (root / ".gate-requests" / "req-1.json").exists()
    assert (root / ".gate-requests" / "done" / "req-1.json").exists()


def test_decline_writes_no_receipt(tmp_path, human_tty):
    root, req = _project(tmp_path)
    assert decline_request(req, root, note="face is wrong") is None
    assert not (root / "approvals.jsonl").exists()
    declined = json.loads((root / ".gate-requests" / "declined" / "req-1.json").read_text())
    assert declined["declined_note"] == "face is wrong"


@pytest.mark.parametrize("bad_id", ["../escape", "/abs/path", "a/b", ".hidden", "x" * 65, ""])
def test_decide_rejects_malformed_request_ids(tmp_path, human_tty, bad_id):
    root, req = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    req["request_id"] = bad_id
    with pytest.raises(gate_approve.GateHandlerError):
        approve_request(req, root, note=None)
    with pytest.raises(gate_approve.GateHandlerError):
        decline_request(req, root, note=None)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert not (root / "approvals.jsonl").exists()


def test_decide_requires_request_id_to_match_file_stem(tmp_path, human_tty):
    root, req = _project(tmp_path)
    req["request_id"] = "req-2"  # valid grammar, but no such pending file
    with pytest.raises(gate_approve.GateHandlerError):
        decline_request(req, root, note=None)
    assert (root / ".gate-requests" / "req-1.json").exists()


def test_load_request_rejects_id_not_matching_stem(tmp_path):
    root, req = _project(tmp_path)
    p = root / ".gate-requests" / "req-1.json"
    req["request_id"] = "../req-1"
    p.write_text(json.dumps(req))
    with pytest.raises(gate_approve.GateHandlerError):
        gate_approve.load_request(p)

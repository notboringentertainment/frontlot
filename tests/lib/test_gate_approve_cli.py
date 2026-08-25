import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import gate_approve  # noqa: E402
from lib.receipts import find_approval  # noqa: E402
from lib.canonical_json import record_sha256  # noqa: E402


@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / "gates"))


def _project(tmp_path):
    root = tmp_path / "projects" / "demo"
    (root / ".gate-requests").mkdir(parents=True)
    req = {
        "request_id": "req-1", "project_id": "demo", "stage": "visual_bible",
        "scope": "character:character-0-abcd1234", "kind": "hero",
        "entity_id": "character-0-abcd1234", "artifact": None,
        "approval_record": {"id": "character-0-abcd1234", "assets": {"hero": "a" * 64}},
        "source_checkpoint_digest": None, "summary": "Pick hero portrait.", "preview_paths": [],
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
    """decide() is not a bypass around main()'s TTY check (#1)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    root, req = _project(tmp_path)
    with pytest.raises(gate_approve.GateHandlerError, match="interactive terminal"):
        gate_approve.decide(req, root, answer="y", note=None)
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
    receipt = gate_approve.decide(req, root, answer="y", note="looks right")
    assert receipt["kind"] == "hero"
    found = find_approval(root, "hero", entity_id=req["entity_id"], record_sha256=record_sha256(req["approval_record"]))
    assert found and found["receipt_id"] == receipt["receipt_id"]
    assert not (root / ".gate-requests" / "req-1.json").exists()
    assert (root / ".gate-requests" / "done" / "req-1.json").exists()


def test_decline_writes_no_receipt(tmp_path, human_tty):
    root, req = _project(tmp_path)
    assert gate_approve.decide(req, root, answer="n", note="face is wrong") is None
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
        gate_approve.decide(req, root, answer="y", note=None)
    with pytest.raises(gate_approve.GateHandlerError):
        gate_approve.decide(req, root, answer="n", note=None)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert not (root / "approvals.jsonl").exists()


def test_decide_requires_request_id_to_match_file_stem(tmp_path, human_tty):
    root, req = _project(tmp_path)
    req["request_id"] = "req-2"  # valid grammar, but no such pending file
    with pytest.raises(gate_approve.GateHandlerError):
        gate_approve.decide(req, root, answer="n", note=None)
    assert (root / ".gate-requests" / "req-1.json").exists()


def test_load_request_rejects_id_not_matching_stem(tmp_path):
    root, req = _project(tmp_path)
    p = root / ".gate-requests" / "req-1.json"
    req["request_id"] = "../req-1"
    p.write_text(json.dumps(req))
    with pytest.raises(gate_approve.GateHandlerError):
        gate_approve.load_request(p)

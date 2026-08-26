"""gate_approve.py: new kinds, envelopes, and the headshot selection gate (R7#3)."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import gate_approve  # noqa: E402
from lib.headshots import active_headshots  # noqa: E402
from lib.look_spec import look_hash  # noqa: E402
from lib.pipeline_pin import pinned_pipeline, prepare_migration_request  # noqa: E402
from lib.receipts import find_approval  # noqa: E402
from tests.lib.look_lock_helpers import (  # noqa: E402
    CHAR,
    activate_look,
    character_look,
    image,
    look_refs_for,
    prompt_recipe,
)


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def human_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "projects" / "p"
    (root / ".gate-requests").mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": "p", "pipeline_type": "authored-film"}))
    return root


def _pending_checkpoint(root, c, candidates, *, status="awaiting_human"):
    from lib.look_ingest import active_looks

    rec = active_looks(root)[("character", CHAR)]
    cp = {"version": "1.0", "project_id": "p", "pipeline_type": "authored-film", "stage": "headshots",
          "status": status, "timestamp": "2026-08-26T00:00:00+00:00", "artifacts": {"headshot_packet": {
              "version": "1.0", "state": "pending", "characters": [{
                  "entity_kind": "character", "entity_id": CHAR,
                  "look_ref": {"entity_kind": "character", "entity_id": CHAR, "look_hash": rec.look_hash, "receipt_id": rec.receipt_id},
                  "prompt_recipe": prompt_recipe(c), "candidates": candidates}]}}}
    (root / "checkpoint_headshots.json").write_text(json.dumps(cp, indent=2))
    return cp


def _headshot_request(root):
    from lib.headshots import headshot_request

    return json.loads(headshot_request(root, "p", CHAR).read_text())


class TestSelectionGate:
    def test_selection_constructs_signed_record_from_packet(self, root, human_tty):
        c = character_look()
        activate_look(root, c)
        cands = [image(root, f"c{i}", look_refs=look_refs_for(c)) for i in range(3)]
        _pending_checkpoint(root, c, cands)
        req = _headshot_request(root)
        assert req["approval_record"] is None
        receipt = gate_approve.decide(req, root, answer="y", note="the second one", selection=2)
        rec = receipt["record"]
        assert rec["asset_id"] == cands[1]["asset_id"] and rec["origin"] == "generated"
        assert rec["look_hash"] == look_hash(c) and rec["import_receipt_id"] is None
        from lib.checkpoint import checkpoint_digest

        assert rec["candidates_checkpoint_digest"] == checkpoint_digest(root / "checkpoint_headshots.json")
        assert receipt["action"] == "activate" and receipt["supersedes_receipt_id"] is None
        assert active_headshots(root)[CHAR].receipt_id == receipt["receipt_id"]
        assert receipt["user_response"]["selection"] == 2

    def test_replacement_supersedes_tip(self, root, human_tty):
        c = character_look()
        activate_look(root, c)
        cands = [image(root, "c0", look_refs=look_refs_for(c))]
        _pending_checkpoint(root, c, cands)
        first = gate_approve.decide(_headshot_request(root), root, answer="y", note=None, selection=1)
        cands = [image(root, "c1", look_refs=look_refs_for(c))]
        _pending_checkpoint(root, c, cands)
        second = gate_approve.decide(_headshot_request(root), root, answer="y", note=None, selection=1)
        assert second["supersedes_receipt_id"] == first["receipt_id"]
        assert active_headshots(root)[CHAR].asset_id == cands[0]["asset_id"]

    def test_reject_all_requires_note_and_writes_no_receipt(self, root, human_tty):
        c = character_look()
        activate_look(root, c)
        _pending_checkpoint(root, c, [image(root, "c0", look_refs=look_refs_for(c))])
        req = _headshot_request(root)
        with pytest.raises(gate_approve.GateHandlerError, match="note"):
            gate_approve.decide(req, root, answer="n", note=None)
        assert gate_approve.decide(req, root, answer="n", note="all wrong jaw") is None
        assert active_headshots(root) == {}
        assert find_approval(root, "headshot", entity_id=CHAR) is None
        declined = json.loads((root / ".gate-requests" / "declined" / f"{req['request_id']}.json").read_text())
        assert declined["declined_note"] == "all wrong jaw"

    def test_out_of_range_selection_and_agent_authored_record_refused(self, root, human_tty):
        c = character_look()
        activate_look(root, c)
        _pending_checkpoint(root, c, [image(root, "c0", look_refs=look_refs_for(c))])
        req = _headshot_request(root)
        with pytest.raises(gate_approve.GateHandlerError, match="1..1"):
            gate_approve.decide(req, root, answer="y", note=None, selection=2)
        with pytest.raises(gate_approve.GateHandlerError, match="selection"):
            gate_approve.decide(req, root, answer="y", note=None)
        req["approval_record"] = {"asset_id": "z" * 64}
        (root / ".gate-requests" / f"{req['request_id']}.json").write_text(json.dumps(req))
        with pytest.raises(gate_approve.GateHandlerError, match="must not carry approval_record"):
            gate_approve.load_request(root / ".gate-requests" / f"{req['request_id']}.json")

    def test_tampered_preview_bytes_are_refused(self, root, human_tty):
        c = character_look()
        activate_look(root, c)
        cand = image(root, "c0", look_refs=look_refs_for(c))
        _pending_checkpoint(root, c, [cand])
        (root / cand["path"]).write_bytes(b"swapped")
        with pytest.raises(gate_approve.GateHandlerError, match="hashes to"):
            gate_approve.decide(_headshot_request(root), root, answer="y", note=None, selection=1)

    def test_requires_awaiting_human_pending_packet(self, root, human_tty):
        c = character_look()
        activate_look(root, c)
        _pending_checkpoint(root, c, [image(root, "c0", look_refs=look_refs_for(c))], status="completed")
        with pytest.raises(gate_approve.GateHandlerError, match="awaiting_human"):
            gate_approve.decide(_headshot_request(root), root, answer="y", note=None, selection=1)

    def test_main_prompts_for_candidate(self, root, human_tty, monkeypatch, capsys):
        c = character_look()
        activate_look(root, c)
        _pending_checkpoint(root, c, [image(root, "c0", look_refs=look_refs_for(c)), image(root, "c1", look_refs=look_refs_for(c))])
        req = _headshot_request(root)
        answers = iter(["2", ""])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        rc = gate_approve.main(["--project", "p", "--request", req["request_id"], "--projects-dir", str(root.parent)])
        out = capsys.readouterr().out
        assert rc == 0 and "[1]" in out and "[2]" in out and "approved" in out


class TestNewKinds:
    def test_look_lock_request_with_envelope(self, root, human_tty):
        from lib.look_ingest import active_looks, look_lock_request, parse_look_ticket
        from tests.lib.look_lock_helpers import write_ticket

        look = parse_look_ticket(write_ticket(root.parent, character_look()))
        req = json.loads(look_lock_request(root, "p", look).read_text())
        receipt = gate_approve.decide(req, root, answer="y", note=None)
        assert receipt["look_hash"] == look.look_hash and receipt["source_ticket_ref"] == {"id": "wf-0badc0de"}
        assert active_looks(root)[("character", CHAR)].receipt_id == receipt["receipt_id"]

    def test_pipeline_migration_refreshes_cache(self, root, human_tty):
        req = json.loads(prepare_migration_request(root, "p", "authored-film", "1.2").read_text())
        receipt = gate_approve.decide(req, root, answer="y", note=None)
        assert find_approval(root, "pipeline_migration", entity_id="authored-film")["receipt_id"] == receipt["receipt_id"]
        assert pinned_pipeline(root, "authored-film").version == "1.2"
        assert json.loads((root / "project.json").read_text())["pipeline_manifest_version"] == "1.2"

    def test_unknown_kind_is_refused(self, root, human_tty):
        req = {"request_id": "r1", "project_id": "p", "stage": "x", "scope": "x", "kind": "blessing",
               "approval_record": {}, "summary": "s"}
        (root / ".gate-requests" / "r1.json").write_text(json.dumps(req))
        with pytest.raises(gate_approve.GateHandlerError, match="unknown kind"):
            gate_approve.load_request(root / ".gate-requests" / "r1.json")

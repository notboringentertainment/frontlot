"""D20.1 — scripts/look_run.py: ticket → look_lock gate, run state, the five
recovery states, --supersede, --casting. Invented names only."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from lib.look_ingest import active_look_for
from lib.look_spec import look_hash as _look_hash
from lib.reference_import import tainted_hashes
from scripts import look_run
from scripts.look_run import Declined, LookRunError, run_look
from tests.lib.d19_helpers import config_1_1
from tests.lib.look_lock_helpers import (
    CHAR, CHAR2, LOC, PROJECT, approve_request, character_look, decline_request, pin_project, write_ticket,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401
    PIPELINE, approval_policy_decision, gates_dir, plain_decision_log, project, write_project_config,
)
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11


def write(pipeline_dir, stage, artifacts, *, status="completed"):
    from lib.checkpoint import write_checkpoint
    return write_checkpoint(pipeline_dir, PROJECT, stage, status, artifacts, pipeline_type=PIPELINE, human_approved=True)


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    pipeline_dir, project_dir = project
    import sys

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())
    import lib.events as events_mod
    import lib.paths as paths_mod
    monkeypatch.setattr(events_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", pipeline_dir)
    wf = tmp_path / "wayfinder-root"
    wf.mkdir()
    cfg = config_1_1(); cfg["wayfinder_root"] = str(wf)
    digest = write_project_config(project_dir, cfg)
    pin_project(project_dir, "1.3")
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    log = plain_decision_log(); log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11((CHAR, CHAR2), (LOC,)), "decision_log": log})
    c = character_look()
    ticket = write_ticket(wf, c)
    return {"pipeline": pipeline_dir, "project": project_dir, "wf": wf, "c": c, "ticket": ticket, "out": io.StringIO()}


def _req(w, request_id):
    return json.loads((w["project"] / ".gate-requests" / f"{request_id}.json").read_text())


def _state(w, entity=CHAR):
    return look_run._run_state(w["project"], entity)


class TestHappyPath:
    def test_ticket_to_gate_to_packet(self, world):
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        assert r["status"] == "pending" and r["request_id"] == f"look-{CHAR}-1"
        req = _req(w, r["request_id"])
        assert req["kind"] == "look_lock" and req["envelope"]["look_hash"] == _look_hash(w["c"])
        assert req["source_checkpoint_digest"] and "gate_sign.py" in r["command"]
        st = _state(w)
        assert st["mode"] == "look_lock" and st["request_id"] == r["request_id"] and st["look_hash"] == _look_hash(w["c"])
        # idempotent while pending
        assert run_look(w["project"], CHAR, out=w["out"])["status"] == "pending"
        receipt = approve_request(req, w["project"])
        r2 = run_look(w["project"], CHAR, out=w["out"])
        assert r2["status"] == "ratified" and r2["receipt_id"] == receipt["receipt_id"]
        cp = json.loads((w["project"] / "checkpoint_look_lock.json").read_text())
        looks = cp["artifacts"]["look_packet"]["looks"]
        assert [l["entity_id"] for l in looks] == [CHAR] and cp["artifacts"]["look_packet"]["complete"] is False
        assert cp["status"] == "in_progress" and cp["metadata"]["run_state"] == {}
        assert active_look_for(w["project"], "character", CHAR).look_hash == _look_hash(w["c"])
        assert "headshot_run.py" in w["out"].getvalue()
        # nothing to do afterwards
        assert run_look(w["project"], CHAR, out=w["out"])["status"] == "active"

    def test_dry_run_writes_nothing(self, world):
        w = world
        r = run_look(w["project"], CHAR, dry_run=True, out=w["out"])
        assert r["status"] == "dry_run" and not (w["project"] / ".gate-requests").exists()
        assert not (w["project"] / "checkpoint_look_lock.json").exists()

    def test_no_or_ambiguous_ticket(self, world):
        w = world
        with pytest.raises(LookRunError, match="no resolved look ticket"):
            run_look(w["project"], CHAR2, out=w["out"])
        write_ticket(w["wf"], w["c"], name="dup.md", ticket_id="wf-0badc0df")
        with pytest.raises(LookRunError, match="2 resolved tickets claim"):
            run_look(w["project"], CHAR, out=w["out"])


class TestRecovery:
    def test_declined_is_consumed_into_the_decision_log(self, world):
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        decline_request(_req(w, r["request_id"]), w["project"], note="hair is wrong, fixing the ticket")
        with pytest.raises(Declined):
            run_look(w["project"], CHAR, out=w["out"])
        log = json.loads((w["project"] / "decision_log.json").read_text())
        mine = [d for d in log["decisions"] if d["category"] == "revision"]
        assert mine and mine[-1]["reason"] == "hair is wrong, fixing the ticket"
        assert _state(w) is None
        # a fresh run gets revision 2
        assert run_look(w["project"], CHAR, out=w["out"])["request_id"] == f"look-{CHAR}-2"

    def test_missing_request_is_republished_with_the_same_id(self, world):
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()
        r2 = run_look(w["project"], CHAR, out=w["out"])
        assert r2["status"] == "pending" and r2["request_id"] == r["request_id"]
        assert _req(w, r["request_id"])["envelope"]["look_hash"] == _look_hash(w["c"])
        # ticket changed in the crash window: fail closed
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()
        write_ticket(w["wf"], dict(w["c"], hair="shaved"))
        with pytest.raises(LookRunError, match="ticket changed mid-run"):
            run_look(w["project"], CHAR, out=w["out"])

    def test_done_request_of_another_shape_fails_closed(self, world):
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        path = w["project"] / ".gate-requests" / f"{r['request_id']}.json"
        done = path.parent / "done"; done.mkdir()
        req = json.loads(path.read_text()); req["kind"] = "headshot"
        (done / path.name).write_text(json.dumps(req)); path.unlink()
        with pytest.raises(LookRunError, match="refusing to guess"):
            run_look(w["project"], CHAR, out=w["out"])
        assert _state(w) is not None  # nothing consumed

    def test_supersede(self, world):
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        approve_request(_req(w, r["request_id"]), w["project"])
        run_look(w["project"], CHAR, out=w["out"])
        write_ticket(w["wf"], dict(w["c"], hair="shaved close"))
        with pytest.raises(LookRunError, match="--supersede"):
            run_look(w["project"], CHAR, out=w["out"])
        r2 = run_look(w["project"], CHAR, supersede=True, out=w["out"])
        req = _req(w, r2["request_id"])
        assert req["envelope"]["supersedes_look_hash"] == _look_hash(w["c"]) and r2["request_id"] == f"look-{CHAR}-2"
        approve_request(req, w["project"])
        assert run_look(w["project"], CHAR, out=w["out"])["status"] == "ratified"
        assert active_look_for(w["project"], "character", CHAR).payload["hair"] == "shaved close"


def _jpeg(path, seed=7):
    im = Image.new("RGB", (640, 800), (seed * 9 % 256, 40, 90))
    im.save(path, format="JPEG", quality=95)
    return path


class TestCasting:
    def test_casting_then_look_is_one_transition(self, world, tmp_path):
        w = world
        original = _jpeg(tmp_path / "somebody.jpg")
        r = run_look(w["project"], CHAR, casting=original, out=w["out"])
        assert r["status"] == "pending" and r["request_id"] == f"casting-{CHAR}-1"
        assert original.exists()  # the writer's original is never consumed
        req = _req(w, r["request_id"])
        assert req["kind"] == "reference_import" and req["envelope"]["origin_class"] == "casting_inspiration"
        assert req["approval_record"]["entity_id"] == CHAR and req["source_checkpoint_digest"]
        st = _state(w)
        assert st["mode"] == "casting" and st["expected_kind"] == "reference_import"
        # the model-facing text carries no description of the image, only the hash
        assert "somebody" not in json.dumps(req)
        approve_request(req, w["project"])
        r2 = run_look(w["project"], CHAR, out=w["out"])
        assert r2["status"] == "pending" and r2["request_id"] == f"look-{CHAR}-2"
        pixel = st["normalized_pixel_hash"]
        assert (w["project"] / "canon/visual/casting-inspiration" / f"{pixel}.png").is_file()
        assert pixel in tainted_hashes(w["project"])
        assert _state(w)["mode"] == "look_lock"
        approve_request(_req(w, r2["request_id"]), w["project"])
        assert run_look(w["project"], CHAR, out=w["out"])["status"] == "ratified"
        with pytest.raises(LookRunError, match="before ratification only"):
            run_look(w["project"], CHAR, casting=original, out=w["out"])

    def test_casting_missing_request_republished_and_declined_consumed(self, world, tmp_path):
        w = world
        original = _jpeg(tmp_path / "somebody.jpg", seed=3)
        r = run_look(w["project"], CHAR, casting=original, out=w["out"])
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()
        assert run_look(w["project"], CHAR, out=w["out"])["request_id"] == r["request_id"]
        decline_request(_req(w, r["request_id"]), w["project"], note="wrong photo")
        with pytest.raises(Declined):
            run_look(w["project"], CHAR, out=w["out"])
        assert _state(w) is None


class TestInspectionRound2:
    def test_finish_requires_the_receipt_signed_for_this_request(self, world):
        """Inspection #1: a done request whose receipt was signed for another checkpoint cannot finish this run."""
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        approve_request(_req(w, r["request_id"]), w["project"])
        done = w["project"] / ".gate-requests" / "done" / f"{r['request_id']}.json"
        req = json.loads(done.read_text()); req["source_checkpoint_digest"] = "0" * 64
        done.write_text(json.dumps(req))
        with pytest.raises(LookRunError, match="not the one that produced it"):
            run_look(w["project"], CHAR, out=w["out"])
        assert _state(w) is not None

    def test_long_entity_ids_do_not_collide(self):
        from lib.run_common import request_id_for
        a = "a" * 80 + "-one"; b = "a" * 80 + "-two"
        ra, rb = request_id_for("look", a, 1), request_id_for("look", b, 1)
        assert ra != rb and len(ra) <= 64 and ra.endswith("-1") and request_id_for("look", a, 2).endswith("-2")
        assert request_id_for("look", CHAR, 3) == f"look-{CHAR}-3"

    def test_ticket_edit_after_approval_does_not_trap_the_run(self, world):
        """Inspection #9: finish from the signed receipt; the edited ticket is then a --supersede."""
        w = world
        r = run_look(w["project"], CHAR, out=w["out"])
        approve_request(_req(w, r["request_id"]), w["project"])
        write_ticket(w["wf"], dict(w["c"], hair="shaved close"))
        r2 = run_look(w["project"], CHAR, out=w["out"])
        assert r2["status"] == "ratified" and _state(w) is None
        assert "ticket changed after ratification" in w["out"].getvalue()
        looks = json.loads((w["project"] / "checkpoint_look_lock.json").read_text())["artifacts"]["look_packet"]["looks"]
        assert looks[0]["look_spec"]["hair"] == w["c"]["hair"]  # the SIGNED payload, not the edited ticket
        r3 = run_look(w["project"], CHAR, supersede=True, out=w["out"])
        assert r3["status"] == "pending" and _req(w, r3["request_id"])["envelope"]["supersedes_look_hash"] == _look_hash(w["c"])

    def test_casting_is_character_only(self, world, tmp_path):
        with pytest.raises(LookRunError, match="characters only"):
            run_look(world["project"], LOC, entity_kind="location", casting=_jpeg(tmp_path / "x.jpg"), out=world["out"])

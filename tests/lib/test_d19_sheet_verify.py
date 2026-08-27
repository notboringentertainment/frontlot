"""D19 step 5: under authored-film 1.3 an approved character entry needs a
passing, config-bound, attempt-chained QC verdict per role — at checkpoint
write AND at the gate; failed items only via a signed qc_override. Invented names only."""
from __future__ import annotations

import copy
import hashlib
import io
import json

import pytest
from PIL import Image

from lib import qc_receipts as qr
from lib.canon_enforcement import character_approval_record
from lib.checkpoint import CheckpointValidationError, write_checkpoint
from lib.look_spec import look_hash as _look_hash
from lib.sheet_qc import policy
from scripts.gate_approve import GateHandlerError
from tests.lib.d19_helpers import JUDGE_MODEL, config_1_1
from tests.lib.look_lock_helpers import (
    CHAR, CHAR2, LOC, PROJECT, activate_look, approve_headshot, approve_request, character_look, image,
    location_look, look_packet_for, look_ref, look_refs_for, pin_project, prompt_recipe,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401
    GENERATOR_DEFAULTS, PALETTE, PIPELINE, approval_policy_decision, gates_dir, plain_decision_log, project,
    write_project_config,
)
from tests.lib.test_per_entity_flow import one_character_through_headshot, write
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11
from tests.tools.test_sheet_judge import FakeAdapter, _all
from tools.qa.sheet_judge import SheetJudge


def _png(w, h, seed):
    d = hashlib.sha256(seed.encode()).digest()
    buf = io.BytesIO(); Image.new("RGB", (w, h), (d[0], d[1], d[2])).save(buf, "PNG"); return buf.getvalue()


@pytest.fixture
def world(project, monkeypatch):
    pipeline_dir, project_dir = project
    import sys

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())  # the gate is run by a human
    import lib.events as events_mod
    import lib.paths as paths_mod
    monkeypatch.setattr(events_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")
    cfg = config_1_1(max_attempts=2)
    digest = write_project_config(project_dir, cfg)
    pin_project(project_dir, "1.3")
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    log = plain_decision_log(); log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11((CHAR, CHAR2), (LOC,)), "decision_log": log})
    c, l, approved, hs = one_character_through_headshot(pipeline_dir, project_dir)
    hero = approved["characters"][0]["hero"]
    return {"pipeline": pipeline_dir, "project": project_dir, "c": c, "l": l, "hero": hero, "hs": hs, "log": log}


def _sheet_asset(w, role, seed):
    """A real full-size PNG with a governed generation receipt (look_refs + headshot_ref)."""
    from lib import receipts
    size = (2560, 1600) if role == "turnaround" else (1536, 1024)
    data = _png(*size, seed); sha = hashlib.sha256(data).hexdigest()
    rel = f"canon/visual/objects/{sha}.png"; (w["project"] / rel).write_bytes(data)
    href = {"entity_id": CHAR, "asset_id": w["hero"]["asset_id"], "approval_receipt_id": w["hs"]["receipt_id"]}
    rec = prompt_recipe(w["c"])
    row = receipts.record_generation(w["project"], execution_id=f"exec-{seed}", tool="seedream_image", normalized_inputs_hash="a" * 64,
                                     output_sha256=sha, cost_usd=0.1, started_at="t", finished_at="t", model_endpoint="fake/edit",
                                     prompt=w["c"]["prompt_safe_description"], look_refs=look_refs_for(w["c"]), headshot_ref=href,
                                     prompt_recipe=rec, references_applied=[{"asset_id": w["hero"]["asset_id"], "path": w["hero"]["path"], "role": "hero"}])
    return {"asset_id": sha, "path": rel, "role": role, "provenance": {"generator_kind": "model", "model_endpoint": "fake/edit",
            "prompt": w["c"]["prompt_safe_description"], "generation_receipt_id": row["receipt_id"]}}, row


def _series(w, role):
    return {"entity_kind": "character", "entity_id": CHAR, "role": role, "look_hash": _look_hash(w["c"]),
            "headshot_receipt_id": w["hs"]["receipt_id"], "policy_bundle_sha256": policy.bundle_sha256(),
            "builder_policy_sha256": "b" * 64, "generation_endpoint": "fake/edit", "generation_model": "fake/edit",
            "judge_provider": "openai", "judge_model": JUDGE_MODEL}


def _judge(w, role, ref, gen, answers):
    a = qr.start_attempt(w["project"], _series(w, role), max_attempts=2)
    qr.attach_generation(w["project"], a["attempt_id"], generation_receipt_id=gen["receipt_id"], asset_id=ref["asset_id"])
    r = SheetJudge(adapter=FakeAdapter(answers)).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": ref["asset_id"]})
    assert r.success, r.error
    return r.data["qc_receipt_id"], r.data["verdict"]


def _entry(w, sheet, qc=None, status="draft"):
    from lib.look_ingest import active_looks
    active = active_looks(w["project"])
    e = {"id": CHAR, "hero": w["hero"], "sheet": sheet, "wardrobe_negative": "no hat", "prompt_recipe": prompt_recipe(w["c"]),
         "look_ref": look_ref(w["c"], {"receipt_id": active[("character", CHAR)].receipt_id}), "sheet_revision": 1, "status": status}
    if qc is not None:
        e["qc_receipts"] = qc
    return e


def _bible(entry):
    return {"version": "1.1", "project_slug": "test-feature", "palette": PALETTE, "generator_defaults": GENERATOR_DEFAULTS,
            "characters": [entry], "locations": []}


_REQ_N = [0]


def _persist(root, req):
    """Gate requests live on disk under .gate-requests/<id>.json."""
    d = root / ".gate-requests"; d.mkdir(exist_ok=True)
    (d / f"{req['request_id']}.json").write_text(json.dumps(req))
    return req


def _request(entry, root=None):
    _REQ_N[0] += 1
    req = {"request_id": f"sheet-x-{_REQ_N[0]}", "project_id": PROJECT, "stage": "visual_bible", "scope": f"character:{CHAR}", "kind": "sheet",
           "entity_id": CHAR, "summary": "s", "approval_record": character_approval_record(entry, PALETTE)}
    return _persist(root, req) if root is not None else req


def test_full_path_write_and_gate_with_passing_verdicts(world):
    w = world
    t, tg = _sheet_asset(w, "turnaround", "t1"); e, eg = _sheet_asset(w, "expressions", "e1")
    qt, vt = _judge(w, "turnaround", t, tg, _all("turnaround"))
    qe, ve = _judge(w, "expressions", e, eg, _all("expressions", head_height="no"))
    assert (vt, ve) == ("pass", "pass")
    entry = _entry(w, {"turnaround": t, "expressions": e}, {"turnaround": qt, "expressions": qe})
    write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")
    receipt = approve_request(_request(entry, w["project"]), w["project"])
    assert receipt["record"]["qc_receipts"] == {"turnaround": qt, "expressions": qe}
    entry["status"] = "approved"; entry["approval_receipt_id"] = receipt["receipt_id"]
    write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="in_progress")


def test_missing_and_mismatched_qc_refused(world):
    w = world
    t, tg = _sheet_asset(w, "turnaround", "t1"); e, eg = _sheet_asset(w, "expressions", "e1")
    # draft without qc: writable as a draft, refused at the gate
    entry = _entry(w, {"turnaround": t, "expressions": e})
    write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")
    with pytest.raises(GateHandlerError, match="names no verdict"):
        approve_request(_request(entry, w["project"]), w["project"])
    # approved without qc: refused at write
    bad = dict(entry, status="approved", approval_receipt_id="x")
    with pytest.raises(CheckpointValidationError, match="names no verdict"):
        write(w["pipeline"], "visual_bible", {"visual_bible": _bible(bad)}, status="in_progress")
    # verdict for the wrong asset (judged t, declared for e)
    qt, _ = _judge(w, "turnaround", t, tg, _all("turnaround"))
    entry2 = _entry(w, {"turnaround": t, "expressions": e}, {"turnaround": qt, "expressions": qt})
    with pytest.raises(CheckpointValidationError, match="verdict is for asset"):
        write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry2)}, status="awaiting_human")
    # legacy role under 1.3
    f, fg = _sheet_asset(w, "expressions", "front-legacy")
    entry3 = _entry(w, {"turnaround": t, "expressions": e, "front": f})
    with pytest.raises(CheckpointValidationError, match="legacy roles"):
        write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry3)}, status="awaiting_human")


def test_failed_verdict_blocks_until_override(world):
    w = world
    t, tg = _sheet_asset(w, "turnaround", "t1"); e, eg = _sheet_asset(w, "expressions", "e1")
    qt, vt = _judge(w, "turnaround", t, tg, _all("turnaround", shoes_visible="no"))
    qe, _ = _judge(w, "expressions", e, eg, _all("expressions"))
    assert vt == "fail"
    entry = _entry(w, {"turnaround": t, "expressions": e}, {"turnaround": qt, "expressions": qe})
    # a draft declaring a failed verdict is refused at write (nothing is presented)
    with pytest.raises(CheckpointValidationError, match="no signed qc_override covers \\['shoes_visible'\\]"):
        write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")
    # override request: wrong item refused, right item signed
    oreq = {"request_id": "override-1", "project_id": PROJECT, "stage": "visual_bible", "scope": f"character:{CHAR}",
            "kind": "qc_override", "entity_id": CHAR, "summary": "s", "qc_receipt_id": qt, "item_ids": ["hands_empty"], "reason": "judge wrong"}
    with pytest.raises(GateHandlerError, match="not failing items"):
        approve_request(_persist(w["project"], oreq), w["project"])
    oreq["item_ids"] = ["shoes_visible"]
    orc = approve_request(_persist(w["project"], oreq), w["project"])
    assert orc["kind"] == "qc_override" and orc["qc_receipt_id"] == qt and orc["record"]["item_ids"] == ["shoes_visible"]
    write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")
    receipt = approve_request(_request(entry, w["project"]), w["project"])
    assert receipt["kind"] == "sheet"


def test_local_failure_cannot_be_overridden_and_stale_policy_refused(world, monkeypatch):
    w = world
    small = _png(640, 400, "tiny"); sha = hashlib.sha256(small).hexdigest()
    rel = f"canon/visual/objects/{sha}.png"; (w["project"] / rel).write_bytes(small)
    from lib import receipts
    href = {"entity_id": CHAR, "asset_id": w["hero"]["asset_id"], "approval_receipt_id": w["hs"]["receipt_id"]}
    row = receipts.record_generation(w["project"], execution_id="exec-tiny", tool="seedream_image", normalized_inputs_hash="a" * 64,
                                     output_sha256=sha, cost_usd=0.1, started_at="t", finished_at="t", model_endpoint="fake/edit",
                                     prompt="p", look_refs=look_refs_for(w["c"]), headshot_ref=href)
    ref = {"asset_id": sha, "path": rel, "role": "turnaround", "provenance": {"generator_kind": "model", "model_endpoint": "fake/edit", "prompt": "p", "generation_receipt_id": row["receipt_id"]}}
    q, v = _judge(w, "turnaround", ref, row, _all("turnaround"))
    assert v == "fail"
    oreq = {"request_id": "override-2", "project_id": PROJECT, "stage": "visual_bible", "scope": f"character:{CHAR}",
            "kind": "qc_override", "entity_id": CHAR, "summary": "s", "qc_receipt_id": q, "item_ids": ["size"], "reason": "x"}
    with pytest.raises(GateHandlerError, match="cannot be overridden"):
        approve_request(_persist(w["project"], oreq), w["project"])
    # a policy edit after judging invalidates the verdict at verification time
    t, tg = _sheet_asset(w, "turnaround", "t9"); e, eg = _sheet_asset(w, "expressions", "e9")
    qt, _ = _judge(w, "turnaround", t, tg, _all("turnaround")); qe, _ = _judge(w, "expressions", e, eg, _all("expressions"))
    entry = _entry(w, {"turnaround": t, "expressions": e}, {"turnaround": qt, "expressions": qe})
    write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")
    monkeypatch.setattr(policy, "SYSTEM_PROMPT", policy.SYSTEM_PROMPT + " (edited)")
    from lib.project_config import VerifiedProjectConfig
    real = VerifiedProjectConfig.require_qc
    def stale(self):
        q = real(self)
        return type(q)(q.judge_provider, q.judge_model, policy.bundle_sha256(), q.max_attempts_per_series)
    monkeypatch.setattr(VerifiedProjectConfig, "require_qc", stale)
    with pytest.raises(GateHandlerError, match="policy bundle is not the one pinned"):
        approve_request(_request(entry, w["project"]), w["project"])


def test_attempt_cap_is_governed(world):
    w = world
    t, tg = _sheet_asset(w, "turnaround", "t1")
    _judge(w, "turnaround", t, tg, _all("turnaround", hands_empty="no"))
    t2, tg2 = _sheet_asset(w, "turnaround", "t2")
    _judge(w, "turnaround", t2, tg2, _all("turnaround", hands_empty="no"))
    with pytest.raises(qr.AttemptCapExceeded):
        qr.start_attempt(w["project"], _series(w, "turnaround"), max_attempts=2)
    # CLI-style "raise the cap" is refused by the signed config in the verifier even if a row existed
    a = qr.start_attempt(w["project"], _series(w, "turnaround"), max_attempts=5)   # library allows; config does not
    assert a["attempt_n"] == 3
    t3, tg3 = _sheet_asset(w, "turnaround", "t3")
    qr.attach_generation(w["project"], a["attempt_id"], generation_receipt_id=tg3["receipt_id"], asset_id=t3["asset_id"])
    r = SheetJudge(adapter=FakeAdapter(_all("turnaround"))).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": t3["asset_id"]})
    assert r.success and r.data["verdict"] == "pass"
    e, eg = _sheet_asset(w, "expressions", "e1"); qe, _ = _judge(w, "expressions", e, eg, _all("expressions"))
    entry = _entry(w, {"turnaround": t3, "expressions": e}, {"turnaround": r.data["qc_receipt_id"], "expressions": qe})
    with pytest.raises(CheckpointValidationError, match="exceeds the signed cap"):
        write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")

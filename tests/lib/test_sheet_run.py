"""D19 step 6: scripts/sheet_run.py with a fake generator and fake judge. Invented names only."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from lib import qc_receipts as qr, receipts
from lib.checkpoint import checkpoint_digest, read_checkpoint, write_checkpoint
from lib.look_spec import look_hash as _look_hash
from lib.sheet_qc import policy
from scripts import sheet_run
from tests.lib.d19_helpers import JUDGE_MODEL
from tests.lib.look_lock_helpers import CHAR, CHAR2, PROJECT, approve_request, decline_request, look_refs_for, prompt_recipe
from tests.lib.test_authored_film_contract import gates_dir, project  # noqa: F401
from tests.lib.test_d19_sheet_verify import world  # noqa: F401
from tests.tools.test_sheet_judge import FakeAdapter, _all
from tools import prompt_builder as pb
from tools.video import _shared

PALETTE = ["moss", "brass", "slate"]


def _png(w, h, seed):
    d = hashlib.sha256(seed.encode()).digest()
    buf = io.BytesIO(); Image.new("RGB", (w, h), (d[0], d[1], d[2])).save(buf, "PNG"); return buf.getvalue()


class FakeGen:
    """Stands in for the governed Seedream call: runs the pre-submit hook (as
    the tool does after reserving), writes a real PNG, records a governed
    generation receipt. Each call yields new pixels."""

    def __init__(self):
        self.n = 0

    def __call__(self, root, role, ctx):
        self.n += 1
        _shared.run_pre_submit(root, f"res-fake-{self.n}")
        size = (2560, 1600) if role == "turnaround" else (1536, 1024)
        data = _png(*size, f"{role}-{self.n}"); sha = hashlib.sha256(data).hexdigest()
        (root / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
        look, head = ctx["look"], ctx["headshot"]
        built = pb.build_prompt(look.payload, role=role, palette=ctx["palette"])
        row = receipts.record_generation(root, execution_id=f"exec-{role}-{self.n}", tool="seedream_image", normalized_inputs_hash="a" * 64,
                                         output_sha256=sha, cost_usd=0.1, started_at="t", finished_at="t", model_endpoint="bytedance/seedream/v5/pro/edit",
                                         prompt=built["prompt"], prompt_recipe=built["prompt_recipe"], look_refs=look_refs_for(look.payload),
                                         headshot_ref={"entity_id": look.entity_id, "asset_id": head.asset_id, "approval_receipt_id": head.receipt_id},
                                         references_applied=[{"asset_id": head.asset_id, "path": f"canon/visual/objects/{head.asset_id}.png", "role": "hero"}])
        return sha, row["receipt_id"]


class SeqJudge(FakeAdapter):
    """Answers per call, by role, in order."""

    def __init__(self, plan):
        super().__init__(None); self.plan = plan; self.seen = []

    def submit(self, *, model, system, user, images, schema):
        role = user.split("\n")[0].replace("Sheet role: ", "").strip(".")
        self.seen.append(role)
        self.answers = self.plan[role].pop(0)
        return f"resp_{len(self.seen)}"


def test_happy_path_writes_draft_and_gate(world):
    w = world
    judge = SeqJudge({"turnaround": [_all("turnaround")], "expressions": [_all("expressions")]})
    out = io.StringIO()
    res = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(), judge_adapter=judge, out=out)
    assert res["revision"] == 1 and set(res["roles"]) == {"turnaround", "expressions"}
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    entry = cp["artifacts"]["visual_bible"]["characters"][0]
    assert cp["status"] == "awaiting_human" and entry["status"] == "draft" and set(entry["qc_receipts"]) == {"turnaround", "expressions"}
    assert entry["prompt_recipe"]["builder_policy_sha256"] == pb.builder_policy_sha256()
    req = json.loads((w["project"] / ".gate-requests" / res["request_id"]).with_suffix(".json").read_text())
    receipt = approve_request(req, w["project"])
    assert receipt["kind"] == "sheet" and receipt["record"]["qc_receipts"] == entry["qc_receipts"]
    assert "Approve in Terminal" in out.getvalue()
    # re-run after signing FINISHES revision 1 (F0): approved, receipt bound, checkpoint back in progress, no run state
    out2 = io.StringIO()
    res2 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(), judge_adapter=judge, out=out2)
    assert res2 == {"entity_id": CHAR, "status": "approved", "revision": 1, "receipt_id": receipt["receipt_id"]}
    assert f"approved {CHAR} rev 1 receipt {receipt['receipt_id']}" in out2.getvalue()
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    entry = cp["artifacts"]["visual_bible"]["characters"][0]
    assert cp["status"] == "in_progress" and entry["status"] == "approved" and entry["approval_receipt_id"] == receipt["receipt_id"]
    assert cp["metadata"]["run_state"] == {} and cp["metadata"]["sheet_prev_entry"] == {} and cp["metadata"]["sheet_revisions"] == {CHAR: 1}
    view = w["project"] / "canon/visual/by-entity" / CHAR
    assert all((view / f"{r}.png").is_symlink() and (view / f"{r}.png").is_file() for r in ("hero", "turnaround", "expressions"))
    assert not (view / "wardrobe.png").exists()
    assert not any((view / f"{r}.png").exists() for r in ("front", "three_quarter", "profile", "full_body"))
    assert set(json.loads((w["project"] / ".gate-requests/done" / f"{res['request_id']}.json").read_text())) >= {"approval_receipt_id", "source_checkpoint_digest"}
    # a third run with identical state reuses passes: no new attempts, revision 2 draft replacing the approved entry
    res3 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(), judge_adapter=judge, out=io.StringIO())
    assert res3["revision"] == 2 and judge.seen == ["turnaround", "expressions"]
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    assert cp["metadata"]["run_state"][CHAR]["revision"] == 2 and cp["metadata"]["sheet_prev_entry"][CHAR]["status"] == "approved"


def _publish(w, judge=None, gen=None, **kw):
    judge = judge or SeqJudge({"turnaround": [_all("turnaround")], "expressions": [_all("expressions")]})
    gen = gen or FakeGen()
    res = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO(), **kw)
    req = json.loads((w["project"] / ".gate-requests" / f"{res['request_id']}.json").read_text())
    return res, req, gen, judge


def _finish(w, **kw):
    return sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")),
                               out=io.StringIO(), finish=True, **kw)


def _meta(w):
    return read_checkpoint(w["pipeline"], PROJECT, "visual_bible")["metadata"]


def _decisions(w, subject_part):
    log = json.loads((w["project"] / "decision_log.json").read_text())
    return [d for d in log["decisions"] if subject_part in d["subject"]]


def test_finish_pending_prints_gate_command_and_requires_state(world):
    w = world
    with pytest.raises(sheet_run.SheetRunError, match="nothing to finish"):
        _finish(w)
    res, req, _, _ = _publish(w)
    out = io.StringIO()
    res2 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")), out=out, finish=True)
    assert res2["status"] == "pending" and res2["request_id"] == res["request_id"] and "gate_sign.py" in out.getvalue()
    assert req["source_checkpoint_digest"] == checkpoint_digest(w["project"] / "checkpoint_visual_bible.json")


def test_finish_refuses_tampered_digest_and_other_kind(world):
    w = world
    res, req, _, _ = _publish(w)
    approve_request(req, w["project"])
    done = w["project"] / ".gate-requests/done" / f"{res['request_id']}.json"
    signed = json.loads(done.read_text())
    done.write_text(json.dumps(dict(signed, source_checkpoint_digest="f" * 64)))
    with pytest.raises(sheet_run.SheetRunError, match="changed after signing|does not verify"):
        _finish(w)
    done.write_text(json.dumps(dict(signed, kind="headshot")))
    with pytest.raises(sheet_run.SheetRunError, match="another kind"):
        _finish(w)
    entry = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")["artifacts"]["visual_bible"]["characters"][0]
    assert entry["status"] == "draft" and _meta(w)["run_state"][CHAR]["request_id"] == res["request_id"]
    # the untouched marker still finishes
    done.write_text(json.dumps(signed))
    assert _finish(w)["status"] == "approved"


def test_declined_restores_prev_records_rejections_and_regenerates(world):
    w = world
    judge = SeqJudge({"turnaround": [_all("turnaround"), _all("turnaround")], "expressions": [_all("expressions"), _all("expressions")]})
    res, req, gen, _ = _publish(w, judge=judge)
    entry = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")["artifacts"]["visual_bible"]["characters"][0]
    decline_request(req, w["project"], note="the turnaround reads as a different person")
    with pytest.raises(sheet_run.Declined, match="different person"):
        _finish(w)
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    assert cp["status"] == "in_progress" and cp["artifacts"]["visual_bible"]["characters"] == [] and cp["metadata"]["run_state"] == {}
    rejected = set(cp["metadata"]["rejected_sheets"][CHAR])
    assert rejected == set(entry["qc_receipts"].values()) | {r["asset_id"] for r in entry["sheet"].values()}
    assert _decisions(w, "declined")[0]["reason"] == "the turnaround reads as a different person"
    # the next run never reuses the rejected passes: both roles regenerate, revision 2
    res2 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO())
    assert res2["revision"] == 2 and gen.n == 4
    entry2 = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")["artifacts"]["visual_bible"]["characters"][0]
    assert not ({r["asset_id"] for r in entry2["sheet"].values()} & rejected)


def test_declined_at_cap_one_is_a_dedicated_block(world):
    w = world
    res, req, gen, judge = _publish(w, max_attempts=1)
    decline_request(req, w["project"], note="no")
    with pytest.raises(sheet_run.Declined):
        _finish(w)
    with pytest.raises(sheet_run.Blocked, match="human-rejected sheet exhausted its budget for turnaround"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, max_attempts=1, generate=gen, judge_adapter=judge, out=io.StringIO())
    assert gen.n == 2 and list((w["project"] / ".gate-requests").glob("override-*.json")) == []
    assert _meta(w)["run_state"] == {}


def test_missing_request_is_republished_after_reverification(world):
    w = world
    res, req, _, _ = _publish(w)
    path = w["project"] / ".gate-requests" / f"{res['request_id']}.json"
    path.unlink()
    res2 = _finish(w)
    assert res2["status"] == "pending" and path.is_file()
    again = json.loads(path.read_text())
    assert again["source_checkpoint_digest"] == checkpoint_digest(w["project"] / "checkpoint_visual_bible.json")
    assert again["approval_record"] == req["approval_record"] and again["source_checkpoint_digest"] != req["source_checkpoint_digest"]
    assert approve_request(again, w["project"])["kind"] == "sheet" and _finish(w)["status"] == "approved"


def test_second_entity_blocked_while_first_pending(world):
    w = world
    _publish(w)
    with pytest.raises(sheet_run.SheetRunError, match=f"finish or decline {CHAR} first"):
        sheet_run.run_sheet(w["project"], CHAR2, palette=PALETTE, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")), out=io.StringIO())


def _rewrite_checkpoint(w):
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    write_checkpoint(w["pipeline"], PROJECT, "visual_bible", "awaiting_human", cp["artifacts"], pipeline_type="authored-film",
                     human_approval_required=True, metadata=dict(cp["metadata"], touched=True))


def test_stale_checkpoint_refuses_finish_then_abandon_restores(world, monkeypatch):
    w = world
    res, req, gen, judge = _publish(w)
    receipt = approve_request(req, w["project"])
    with pytest.raises(sheet_run.SheetRunError, match="not stale"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO(), abandon=True)
    _rewrite_checkpoint(w)
    with pytest.raises(sheet_run.SheetRunError, match="--abandon"):
        _finish(w)
    # crash after the marker move (step d): the transition is completed by the next resume
    real = sheet_run._clear_abandon_state
    monkeypatch.setattr(sheet_run, "_clear_abandon_state", lambda *a: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO(), abandon=True)
    marker = w["project"] / ".gate-requests/abandoned" / f"{res['request_id']}.json"
    assert marker.is_file() and not (w["project"] / ".gate-requests/done" / f"{res['request_id']}.json").exists()
    assert _meta(w)["run_state"][CHAR]["mode"] == "abandoning" and _meta(w)["run_state"][CHAR]["receipt_id"] == receipt["receipt_id"]
    monkeypatch.setattr(sheet_run, "_clear_abandon_state", real)
    assert _finish(w)["status"] == "abandoned"
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    assert cp["status"] == "in_progress" and cp["artifacts"]["visual_bible"]["characters"] == [] and cp["metadata"]["run_state"] == {}
    assert len(_decisions(w, "abandon sheet")) == 1 and marker.is_file()
    # the receipt stays unused; the next run reuses the passes and publishes revision 2
    res2 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO())
    assert res2["revision"] == 2 and gen.n == 2 and res2["request_id"] != res["request_id"]


def test_abandon_refuses_when_entry_was_edited(world):
    w = world
    res, req, gen, judge = _publish(w)
    approve_request(req, w["project"])
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    cp["artifacts"]["visual_bible"]["characters"][0]["wardrobe_negative"] = "edited by hand"
    write_checkpoint(w["pipeline"], PROJECT, "visual_bible", "awaiting_human", cp["artifacts"], pipeline_type="authored-film",
                     human_approval_required=True, metadata=cp["metadata"])
    with pytest.raises(sheet_run.SheetRunError, match="newer edit"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO(), abandon=True)
    assert _meta(w)["run_state"][CHAR]["mode"] == "sheet"


def test_partial_rerun_regenerates_a_rejected_kept_role(world):
    w = world
    judge = SeqJudge({"turnaround": [_all("turnaround"), _all("turnaround")], "expressions": [_all("expressions"), _all("expressions")]})
    res, req, gen, _ = _publish(w, judge=judge)
    approve_request(req, w["project"])
    assert _finish(w)["status"] == "approved" and gen.n == 2
    # revision 2 reruns only expressions (reused pass; turnaround kept from the approved entry) and the writer declines it
    res2, req2, _, _ = _publish(w, judge=judge, gen=gen, roles=["expressions"])
    assert res2["revision"] == 2 and gen.n == 2
    decline_request(req2, w["project"], note="both views are off")
    with pytest.raises(sheet_run.Declined):
        _finish(w)
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    assert cp["artifacts"]["visual_bible"]["characters"][0]["status"] == "approved"  # revision 1 restored
    # the kept turnaround is in rejection memory: a subset rerun regenerates it too
    out = io.StringIO()
    res3 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, roles=["expressions"], generate=gen, judge_adapter=judge, out=out)
    assert res3["revision"] == 3 and gen.n == 4 and set(res3["roles"]) == {"expressions", "turnaround"}
    assert "regenerating it instead of keeping it" in out.getvalue()


def test_retry_then_block_then_override(world):
    w = world  # cap is 2 (config_1_1(max_attempts=2))
    judge = SeqJudge({"turnaround": [_all("turnaround", shoes_visible="no"), _all("turnaround", shoes_visible="no", hands_empty="no")],
                      "expressions": [_all("expressions")]})
    gen = FakeGen()
    with pytest.raises(sheet_run.Blocked):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO())
    assert gen.n == 2  # two turnaround attempts, then blocked before expressions
    reqs = sorted((w["project"] / ".gate-requests").glob("override-*.json"))
    assert len(reqs) == 1
    req = json.loads(reqs[0].read_text())
    assert req["kind"] == "qc_override" and req["item_ids"] == ["shoes_visible"]  # the best failed attempt (fewest items)
    # re-running without an override stays blocked: no new attempt, no duplicate request
    with pytest.raises(sheet_run.Blocked):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO())
    assert gen.n == 2 and len(list((w["project"] / ".gate-requests").glob("override-*.json"))) == 1
    # the human signs the override with a real reason; the run resumes past the block
    req["reason"] = "shoes are visible; judge misread the shadow"
    reqs[0].write_text(json.dumps(req))
    approve_request(req, w["project"])
    judge.plan["expressions"] = [_all("expressions")]
    res = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=gen, judge_adapter=judge, out=io.StringIO())
    assert res["roles"]["turnaround"]["qc_receipt_id"] == req["qc_receipt_id"] and gen.n == 3
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    assert cp["status"] == "awaiting_human"


def test_cap_and_role_subset_rules(world):
    w = world
    with pytest.raises(sheet_run.SheetRunError, match="exceeds the signed cap"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, max_attempts=5, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")))
    with pytest.raises(sheet_run.SheetRunError, match="omits mandatory"):
        sheet_run.run_sheet(w["project"], CHAR, roles=["turnaround"], palette=PALETTE, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")))
    with pytest.raises(sheet_run.SheetRunError, match="unknown roles"):
        sheet_run.run_sheet(w["project"], CHAR, roles=["front"], palette=PALETTE, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")))
    with pytest.raises(sheet_run.SheetRunError, match="no visual_bible palette"):
        sheet_run.run_sheet(w["project"], CHAR, generate=FakeGen(), judge_adapter=FakeAdapter(_all("turnaround")))


def test_generator_without_hook_records_no_attempt(world):
    w = world

    def rogue(root, role, ctx):  # never runs the pre-submit hook
        return "0" * 64, "gen-x"

    with pytest.raises(sheet_run.SheetRunError, match="did not run the pre-submit hook"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=rogue, judge_adapter=FakeAdapter(_all("turnaround")))
    assert qr.rows_of_kind(w["project"], "attempt_started") == []

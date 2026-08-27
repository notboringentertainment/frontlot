"""D19 step 4: SheetJudge with a fake adapter (test mode only). Invented names only."""
import hashlib
import io
import json

import pytest
from PIL import Image

from lib import gates, qc_receipts as qr
from lib.look_spec import look_hash as _look_hash
from lib.sheet_qc import policy
from lib.state_io import read_jsonl
from tests.lib import look_lock_helpers as H
from tests.lib.d19_helpers import JUDGE_MODEL, make_qc_project
from tools.cost_tracker import IndeterminatePaidCallError, load_reservations, resume_check
from tools.qa.sheet_judge import JudgeAdapter, SheetJudge


def _png(w, h, seed="x"):
    h_ = hashlib.sha256(seed.encode()).digest()
    buf = io.BytesIO(); Image.new("RGB", (w, h), (h_[0], h_[1], h_[2])).save(buf, "PNG"); return buf.getvalue()


class FakeAdapter(JudgeAdapter):
    provider = "openai"

    def __init__(self, answers=None, *, fail_submit=False, fail_wait=False):
        self.answers, self.fail_submit, self.fail_wait, self.calls = answers, fail_submit, fail_wait, []

    def submit(self, *, model, system, user, images, schema):
        self.calls.append({"model": model, "user": user, "n_images": len(images), "schema": schema})
        if self.fail_submit:
            raise ConnectionError("boom before id")
        return "resp_fake_1"

    def wait(self, provider_id, *, deadline_s, poll_s):
        if self.fail_wait:
            raise TimeoutError("poll timeout")
        text = json.dumps({"items": self.answers})
        return {"status": "completed", "output_text": text, "model": "judge-x-2026", "usage": {"total_tokens": 10},
                "raw": {"id": provider_id, "status": "completed", "output_text": text}}


def _all(role, answer="yes", **over):
    out = [{"id": i.id, "answer": over.get(i.id, answer), "note": ""} for i in policy.checklist(role)]
    return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")
    project = make_qc_project(tmp_path, monkeypatch, slug=H.PROJECT)
    look = H.character_look()
    look_receipt = H.activate_look(project, look)
    hero = H.image(project, "hero-seed", role="hero", look_refs=H.look_refs_for(look))
    _, hs_receipt = H.approve_headshot(project, look, hero["asset_id"], "c" * 64)
    # a 2560x1600 turnaround asset with a generation receipt
    data = _png(2560, 1600, "turn")
    sha = hashlib.sha256(data).hexdigest()
    (project / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
    from lib import receipts
    gen = receipts.record_generation(project, execution_id="exec-turn", tool="seedream_image", normalized_inputs_hash="a" * 64,
                                     output_sha256=sha, cost_usd=0.1, started_at="t", finished_at="t", model_endpoint=H.IMAGE_MODEL,
                                     prompt="p", look_refs=H.look_refs_for(look),
                                     headshot_ref={"entity_id": H.CHAR, "asset_id": hero["asset_id"], "approval_receipt_id": hs_receipt["receipt_id"]})
    key = {"entity_kind": "character", "entity_id": H.CHAR, "role": "turnaround", "look_hash": _look_hash(look),
           "headshot_receipt_id": hs_receipt["receipt_id"], "policy_bundle_sha256": policy.bundle_sha256(),
           "builder_policy_sha256": "b" * 64, "generation_endpoint": H.IMAGE_MODEL, "generation_model": H.IMAGE_MODEL,
           "judge_provider": "openai", "judge_model": JUDGE_MODEL}
    return {"project": project, "look": look, "hero": hero, "asset": sha, "gen": gen, "key": key}


def _attempt(w, asset=None):
    a = qr.start_attempt(w["project"], w["key"], max_attempts=3)
    qr.attach_generation(w["project"], a["attempt_id"], generation_receipt_id=w["gen"]["receipt_id"], asset_id=asset or w["asset"])
    return a


def test_pass_verdict_is_signed_and_reused(world):
    w = world; a = _attempt(w)
    fake = FakeAdapter(_all("turnaround"))
    r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["asset"]})
    assert r.success, r.error
    assert r.data["verdict"] == "pass" and r.cost_usd > 0
    assert fake.calls[0]["n_images"] == 1 and "grey work coat" in fake.calls[0]["user"]
    row = qr.find_verdict_by_id(w["project"], r.data["qc_receipt_id"])
    assert row["provider"] == "openai" and row["model"] == JUDGE_MODEL and row["attempt_id"] == a["attempt_id"]
    assert (w["project"] / "canon/qc/objects" / f"{row['raw_response_asset_id']}.json").exists()
    assert qr.attempt_rows(w["project"], a["attempt_id"])["verdict"]["qc_receipt_id"] == row["receipt_id"]
    assert all(v["state"] == "completed" for v in load_reservations(w["project"]).values())
    assert gates.qc_wal_read(row["tuple_sha256"]) is None
    # second attempt on identical pixels: reused, no provider call
    a2 = _attempt(w)
    fake2 = FakeAdapter(_all("turnaround", "no"))
    r2 = SheetJudge(adapter=fake2).execute({"project_dir": str(w["project"]), "attempt_id": a2["attempt_id"], "asset_id": w["asset"]})
    assert r2.success and r2.data["reused"] and r2.data["verdict"] == "pass" and fake2.calls == []
    assert qr.attempt_rows(w["project"], a2["attempt_id"])["verdict"]["reused"] is True


def test_fail_and_warn_scoring(world):
    w = world; a = _attempt(w)
    fake = FakeAdapter(_all("turnaround", profiles_opposite="no", head_height="unsure"))
    r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["asset"]})
    assert r.success and r.data["verdict"] == "fail"
    assert r.data["failing_items"] == ["profiles_opposite"] and r.data["warnings"] == ["head_height"]


def test_local_precheck_fails_without_provider_call(world):
    w = world
    small = _png(640, 400, "small"); sha = hashlib.sha256(small).hexdigest()
    (w["project"] / "canon/visual/objects" / f"{sha}.png").write_bytes(small)
    a = _attempt(w, asset=sha)
    fake = FakeAdapter(_all("turnaround"))
    r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": sha})
    assert r.success and r.data["local"] and r.data["failing_items"] == ["size"] and fake.calls == []
    row = qr.find_verdict_by_id(w["project"], r.data["qc_receipt_id"])
    assert row["provider"] == "local" and row["cost_usd"] == 0


def test_timeout_leaves_reconcilable_state(world):
    w = world; a = _attempt(w)
    r = SheetJudge(adapter=FakeAdapter(_all("turnaround"), fail_wait=True)).execute(
        {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["asset"]})
    assert not r.success and "submitted" in r.error
    wal = [e for e in gates.qc_wal_entries() if e["attempt_id"] == a["attempt_id"]][0]
    assert wal["state"] == "unknown" and wal["provider_request_id"] == "resp_fake_1"
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(w["project"])
    # the attempt is still open; a second judge call refuses until reconciled
    r2 = SheetJudge(adapter=FakeAdapter(_all("turnaround"))).execute(
        {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["asset"]})
    assert not r2.success


def test_submit_failure_before_id(world):
    w = world; a = _attempt(w)
    r = SheetJudge(adapter=FakeAdapter(_all("turnaround"), fail_submit=True)).execute(
        {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["asset"]})
    assert not r.success and "not submitted" in r.error
    wal = [e for e in gates.qc_wal_entries() if e["attempt_id"] == a["attempt_id"]][0]
    assert wal["state"] == "claimed" and "provider_request_id" not in wal


def test_injected_adapter_refused_outside_test_mode(world, monkeypatch):
    w = world; a = _attempt(w)
    monkeypatch.delenv("OPENMONTAGE_TEST_MODE")
    r = SheetJudge(adapter=FakeAdapter(_all("turnaround"))).execute(
        {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["asset"]})
    assert not r.success and "OPENMONTAGE_TEST_MODE" in r.error
    assert qr.attempt_rows(w["project"], a["attempt_id"])["verdict"] is None


def test_wrong_asset_for_attempt_refused(world):
    w = world; a = _attempt(w)
    r = SheetJudge(adapter=FakeAdapter(_all("turnaround"))).execute(
        {"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": w["hero"]["asset_id"]})
    assert not r.success and "generation_attached" in r.error

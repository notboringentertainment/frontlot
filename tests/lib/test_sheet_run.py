"""D19 step 6: scripts/sheet_run.py with a fake generator and fake judge. Invented names only."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from lib import qc_receipts as qr, receipts
from lib.checkpoint import read_checkpoint
from lib.look_spec import look_hash as _look_hash
from lib.sheet_qc import policy
from scripts import sheet_run
from tests.lib.d19_helpers import JUDGE_MODEL
from tests.lib.look_lock_helpers import CHAR, PROJECT, approve_request, look_refs_for, prompt_recipe
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
    # second run with identical state reuses passes: no new attempts, revision 2 draft
    res2 = sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(), judge_adapter=judge, out=io.StringIO())
    assert res2["revision"] == 2 and judge.seen == ["turnaround", "expressions"]


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

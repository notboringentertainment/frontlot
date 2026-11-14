"""D19 step 2: policy bundle, scoring, local checks, signed QC stream. Invented names only."""
import io
import json

import pytest
from PIL import Image

from lib import gates, qc_receipts as qr
from lib.sheet_qc import local_checks, policy, scoring
from lib.state_io import read_jsonl

SHA = "b" * 64


@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / "gates"))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj-lantern"; p.mkdir(); return p


SERIES = {"entity_kind": "character", "entity_id": "marlow-vex", "role": "turnaround", "look_hash": "l" * 40,
          "headshot_receipt_id": "hs-1", "policy_bundle_sha256": policy.bundle_sha256(), "builder_policy_sha256": "c" * 64,
          "generation_endpoint": "vendor/edit", "generation_model": "vendor/edit", "judge_provider": "openai", "judge_model": "judge-x"}


def _verdict(project, attempt_id, *, verdict="pass", asset=SHA):
    items = [{"id": i.id, "answer": "yes", "note": ""} for i in policy.checklist("turnaround")]
    if verdict == "fail":
        items[1]["answer"] = "no"
    sc = scoring.score("turnaround", items)
    v = {"project_id": project.name, "entity_kind": "character", "entity_id": "marlow-vex", "role": "turnaround",
         "asset_id": asset, "look_hash": "l" * 40, "look_receipt_id": "lk-1", "headshot_asset_id": "d" * 64,
         "headshot_receipt_id": "hs-1", "policy_version": policy.QC_POLICY_VERSION, "policy_bundle_sha256": policy.bundle_sha256(),
         "provider": "openai", "model": "judge-x", "prompt_sha256": "e" * 64, "provider_request_id": "resp_1",
         "provider_model_version": "judge-x-2026", "raw_response_asset_id": "f" * 64, "attempt_id": attempt_id,
         "attempt_n": 1, "items": sc["items"], "verdict": sc["verdict"], "failing_items": sc["failing_items"],
         "warnings": sc["warnings"], "cost_usd": 0.03, "judged_at": "2026-08-27T00:00:00+00:00"}
    v["tuple_sha256"] = qr.tuple_sha256(v)
    return v


# ---- policy / scoring ----

def test_bundle_hash_changes_with_any_input(monkeypatch):
    before = policy.bundle_sha256()
    monkeypatch.setattr(policy, "SYSTEM_PROMPT", policy.SYSTEM_PROMPT + " ")
    assert policy.bundle_sha256() != before


def test_scoring_requires_bijection():
    items = policy.checklist("turnaround")
    full = [{"id": i.id, "answer": "yes", "note": ""} for i in items]
    assert scoring.score("turnaround", full)["verdict"] == "pass"
    assert scoring.score("turnaround", full[:-1])["failing_items"] == ["coverage"]
    assert scoring.score("turnaround", full + [full[0]])["failing_items"] == ["coverage"]
    assert scoring.score("turnaround", full + [{"id": "bogus", "answer": "yes", "note": ""}])["failing_items"] == ["coverage"]
    unsure = [dict(x) for x in full]; unsure[3]["answer"] = "unsure"
    assert scoring.score("turnaround", unsure)["failing_items"] == ["shoes_visible"]
    warn = [dict(x) for x in full]; warn[6]["answer"] = "no"   # head_height is warn
    r = scoring.score("turnaround", warn)
    assert r["verdict"] == "pass" and r["warnings"] == ["head_height"]


# ---- local checks ----

def _png(w, h, mode="RGB"):
    buf = io.BytesIO(); Image.new(mode, (w, h), (10, 20, 30) if mode == "RGB" else (10, 20, 30, 255)).save(buf, "PNG"); return buf.getvalue()


def test_local_checks_size_alpha_and_symlink(tmp_path):
    import hashlib
    objects = tmp_path / "objects"; objects.mkdir()
    good = _png(2560, 1600); sha = hashlib.sha256(good).hexdigest(); (objects / f"{sha}.png").write_bytes(good)
    data = local_checks.read_asset_bytes(objects, sha)
    assert local_checks.check_image("turnaround", data) == {"width": 2560, "height": 1600}
    with pytest.raises(local_checks.LocalCheckError) as e:
        local_checks.check_image("turnaround", _png(1024, 1024))
    assert e.value.item == "size"
    with pytest.raises(local_checks.LocalCheckError) as e:
        local_checks.check_image("turnaround", _png(2560, 1600, "RGBA"))
    assert e.value.item == "alpha"
    link = objects / f"{'9' * 64}.png"; link.symlink_to(objects / f"{sha}.png")
    with pytest.raises(local_checks.LocalCheckError):
        local_checks.read_asset_bytes(objects, "9" * 64)
    (objects / f"{'8' * 64}.png").write_bytes(good)
    with pytest.raises(local_checks.LocalCheckError) as e:
        local_checks.read_asset_bytes(objects, "8" * 64)
    assert e.value.item == "hash"


# ---- QC stream ----

def test_attempt_lifecycle_and_cap(project):
    a1 = qr.start_attempt(project, SERIES, max_attempts=2, generation_reservation_id="res-1")
    assert a1["attempt_n"] == 1 and gates.verify_qc_row(a1)
    with pytest.raises(qr.QCReceiptError, match="open attempt"):
        qr.start_attempt(project, SERIES, max_attempts=2)
    qr.attach_generation(project, a1["attempt_id"], generation_receipt_id="gen-1", asset_id=SHA)
    v = qr.record_verdict(project, _verdict(project, a1["attempt_id"], verdict="fail"))
    qr.attach_verdict(project, a1["attempt_id"], qc_receipt_id=v["receipt_id"])
    a2 = qr.start_attempt(project, SERIES, max_attempts=2)
    assert a2["attempt_n"] == 2
    qr.attach_verdict(project, a2["attempt_id"], qc_receipt_id=v["receipt_id"], reused=True)
    with pytest.raises(qr.AttemptCapExceeded):
        qr.start_attempt(project, SERIES, max_attempts=2)
    # a different builder policy opens a fresh series
    a3 = qr.start_attempt(project, dict(SERIES, builder_policy_sha256="0" * 64), max_attempts=2)
    assert a3["attempt_n"] == 1
    rows = read_jsonl(project / "qc-receipts.jsonl")
    assert [r["kind"] for r in rows] == ["attempt_started", "generation_attached", "verdict", "verdict_attached",
                                         "attempt_started", "verdict_attached", "attempt_started"]


def test_verdict_unique_per_tuple_and_tamper_evident(project):
    a = qr.start_attempt(project, SERIES, max_attempts=3)
    v = _verdict(project, a["attempt_id"])
    row = qr.record_verdict(project, v)
    assert qr.record_verdict(project, v)["receipt_id"] == row["receipt_id"]  # idempotent
    other = _verdict(project, a["attempt_id"], verdict="fail")
    with pytest.raises(qr.QCReceiptError, match="already has verdict"):
        qr.record_verdict(project, other)
    bad = dict(v); bad["tuple_sha256"] = "1" * 64
    with pytest.raises(qr.QCReceiptError, match="tuple_sha256"):
        qr.record_verdict(project, bad)
    # deleting the local row breaks the chain projection
    path = project / "qc-receipts.jsonl"
    rows = read_jsonl(path); path.write_text("\n".join(json.dumps(r) for r in rows[:-1]) + "\n")
    with pytest.raises(Exception):
        qr.verified_qc_rows(project)
    # a forged row (unsigned) is invisible
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert qr.find_verdict(project, v["tuple_sha256"])["receipt_id"] == row["receipt_id"]
    forged = dict(row, receipt_id="forged", verdict="pass"); forged.pop("signature")
    with open(path, "a") as f: f.write(json.dumps(forged) + "\n")
    with pytest.raises(Exception):
        qr.verified_qc_rows(project)

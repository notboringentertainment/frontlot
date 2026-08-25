import json

import pytest

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.state_io import read_jsonl


@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    d = tmp_path / "gates"
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(d))
    return d


@pytest.fixture
def project(tmp_path):
    # Directory name == project id: find_approval derives the expected
    # project_id from the directory when none is passed (#9).
    p = tmp_path / "proj-alpha"
    p.mkdir()
    return p


RECORD = {"asset_id": "aa" * 32, "role": "hero", "approved_prompt_block": "a figure at dusk", "palette": ["#112233"]}
PROJECT_ID = "proj-alpha"
STAGE = "visual_bible"
SCOPE = "hero:ch-001"


def _mint(record=RECORD, **kw):
    args = dict(project_id=PROJECT_ID, stage=STAGE, scope=SCOPE, record_sha256=record_sha256(record))
    args.update(kw)
    return gates.mint_gate_token(**args, user_response="approve")


def test_record_human_approval_writes_signed_ledgered_receipt(project, gates_dir):
    token = _mint()
    receipt = receipts.record_human_approval(
        project, PROJECT_ID, STAGE, SCOPE, RECORD, token, "hero",
        entity_id="ch-001", source_checkpoint_digest="cp" * 32,
    )
    assert receipt["record_sha256"] == record_sha256(RECORD)
    assert receipt["entity_id"] == "ch-001"
    assert receipt["user_response"] == "approve"
    assert receipt["source_checkpoint_digest"] == "cp" * 32
    assert gates.verify_receipt(receipt)
    rows = read_jsonl(project / "approvals.jsonl")
    assert rows == [receipt]
    assert gates.ledger_has(receipt["receipt_id"], receipt["record_sha256"])
    # Token is consumed: same token cannot approve again
    with pytest.raises(gates.GateTokenInvalid):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, token, "hero", entity_id="ch-001")


def test_record_human_approval_rejects_mismatched_record(project):
    token = _mint()
    changed = dict(RECORD, approved_prompt_block="edited")
    with pytest.raises(gates.GateTokenInvalid):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, changed, token, "hero", entity_id="ch-001")
    assert not (project / "approvals.jsonl").exists()


def test_record_human_approval_kind_validation(project):
    with pytest.raises(ValueError):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, _mint(), "bogus", entity_id="x")
    with pytest.raises(ValueError):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, _mint(), "hero")
    with pytest.raises(ValueError):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, _mint(), "artifact_review")


def test_artifact_review_receipt_fields(project):
    artifact = {
        "artifact_type": "scene_plan",
        "artifact_version": "1.1",
        "artifact_digest": "dd" * 32,
        "migration_status": "migrated",
    }
    receipt = receipts.record_human_approval(
        project, PROJECT_ID, STAGE, "artifact:scene_plan", RECORD, _mint(scope="artifact:scene_plan"),
        "artifact_review", artifact=artifact,
    )
    for k, v in artifact.items():
        assert receipt[k] == v
    assert receipts.find_approval(project, "artifact_review", record_sha256=receipt["record_sha256"]) == receipt


def test_find_approval_ignores_forged_rows(project):
    token = _mint()
    real = receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, token, "hero", entity_id="ch-001")

    forged_record = dict(RECORD, approved_prompt_block="self-approved")
    forged = dict(real, receipt_id="forged-1", entity_id="ch-002", record_sha256=record_sha256(forged_record))
    # (a) no signature at all
    unsigned = {k: v for k, v in forged.items() if k != "signature"}
    # (b) copied signature from the real receipt (wrong for this content)
    wrong_sig = dict(forged)
    # (c) valid signature but no ledger entry (attacker who somehow got a signature)
    signed_no_ledger = dict(forged, receipt_id="forged-2")
    signed_no_ledger["signature"] = gates.sign_receipt(signed_no_ledger)
    with open(project / "approvals.jsonl", "a") as fh:
        for row in (unsigned, wrong_sig, signed_no_ledger):
            fh.write(json.dumps(row) + "\n")

    assert receipts.find_approval(project, "hero", entity_id="ch-002") is None
    assert receipts.find_approval(project, "hero", record_sha256=record_sha256(forged_record)) is None
    assert receipts.find_approval(project, "hero", entity_id="ch-001") == real
    assert receipts.find_approval(project, "hero", entity_id="ch-001", record_sha256=real["record_sha256"]) == real
    assert receipts.find_approval(project, "hero", entity_id="ch-001", record_sha256="00" * 32) is None
    assert receipts.find_approval(project, "poster") is None


def test_find_approval_returns_latest_valid(project):
    r1 = receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, _mint(), "hero", entity_id="ch-001")
    r2 = receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, _mint(), "hero", entity_id="ch-001")
    assert r1 != r2
    assert receipts.find_approval(project, "hero", entity_id="ch-001") == r2


def test_generation_receipt_roundtrip(project):
    receipt = receipts.record_generation(
        project,
        execution_id="exec-1",
        tool="image_gen",
        model_endpoint="fal-ai/example/edit",
        provider_request_id="req-123",
        normalized_inputs_hash="11" * 32,
        output_sha256="22" * 32,
        cost_usd=0.04,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:05+00:00",
        input_asset_ids=["33" * 32],
    )
    assert receipt["generator_kind"] == "model"
    assert receipts.find_generation(project, "22" * 32) == receipt
    assert receipts.find_generation(project, "99" * 32) is None
    assert read_jsonl(project / "generation-receipts.jsonl") == [receipt]


def test_generation_receipt_local_kind_and_validation(project):
    common = dict(
        execution_id="e", tool="ffmpeg_tool", normalized_inputs_hash="a" * 64, output_sha256="b" * 64,
        cost_usd=0, started_at="t0", finished_at="t1",
    )
    local = receipts.record_generation(
        project, generator_kind="local", local_tool="ffmpeg", local_tool_version="7.0",
        parameters_hash="c" * 64, **common,
    )
    assert local["model_endpoint"] is None and local["local_tool"] == "ffmpeg"
    with pytest.raises(ValueError):
        receipts.record_generation(project, generator_kind="model", **common)
    with pytest.raises(ValueError):
        receipts.record_generation(project, generator_kind="local", **common)
    with pytest.raises(ValueError):
        receipts.record_generation(project, generator_kind="alien", **common)


def test_generation_receipt_write_failure_raises(project, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(receipts, "append_jsonl", boom)
    with pytest.raises(OSError):
        receipts.record_generation(
            project, execution_id="e", tool="t", model_endpoint="m", normalized_inputs_hash="a" * 64,
            output_sha256="b" * 64, cost_usd=0.1, started_at="t0", finished_at="t1",
        )


# ---- #9 project_id binding ----

def test_receipt_copied_into_another_project_is_ignored(tmp_path):
    src = tmp_path / "proj-alpha"; src.mkdir()
    real = receipts.record_human_approval(src, PROJECT_ID, STAGE, SCOPE, RECORD, _mint(), "hero", entity_id="ch-001")
    assert gates.verify_receipt(real)
    other = tmp_path / "proj-beta"; other.mkdir()
    (other / "approvals.jsonl").write_text(json.dumps(real) + "\n")
    # Same signed, ledgered receipt — but it names proj-alpha, and this is proj-beta.
    assert receipts.find_approval(other, "hero", entity_id="ch-001") is None
    assert receipts.find_approval(other, "hero", entity_id="ch-001", project_id="proj-beta") is None
    # Explicit project_id overrides the directory-derived default.
    assert receipts.find_approval(other, "hero", entity_id="ch-001", project_id=PROJECT_ID) == real
    assert receipts.find_approval(src, "hero", entity_id="ch-001", project_id="proj-beta") is None


# ---- #18 crash-safe approval (WAL) ----

def _pending_wal():
    return list(gates.wal_dir().glob("*.json"))


def test_crash_between_consume_and_append_is_recovered(project, monkeypatch):
    token = _mint()
    token_hmac = gates.token_digest(token)
    real_append = receipts.append_jsonl

    def crash(path, row):
        raise OSError("disk full")

    monkeypatch.setattr(receipts, "append_jsonl", crash)
    with pytest.raises(OSError):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, token, "hero", entity_id="ch-001")
    monkeypatch.setattr(receipts, "append_jsonl", real_append)
    # Token is consumed, nothing was written, WAL entry survives.
    assert gates.consumed_binding(token_hmac) is not None
    assert not (project / "approvals.jsonl").exists()
    assert len(_pending_wal()) == 1
    # Recovery (also run by find_approval on entry) replays the receipt + ledger.
    recovered = receipts.recover_pending_approvals(project)
    assert len(recovered) == 1
    receipt = recovered[0]
    assert receipt["token_hmac"] == token_hmac
    assert gates.verify_receipt(receipt)
    assert read_jsonl(project / "approvals.jsonl") == [receipt]
    assert _pending_wal() == []
    assert receipts.find_approval(project, "hero", entity_id="ch-001") == receipt
    # Idempotent: a second recovery adds nothing.
    assert receipts.recover_pending_approvals(project) == []
    assert read_jsonl(project / "approvals.jsonl") == [receipt]
    assert sum(1 for r in read_jsonl(gates.ledger_path()) if r["receipt_id"] == receipt["receipt_id"]) == 1


def test_crash_between_append_and_ledger_is_recovered_by_find_approval(project, monkeypatch):
    token = _mint()

    def crash(binding, receipt_id):
        raise OSError("ledger unwritable")

    real_ledger_append = gates.ledger_append
    monkeypatch.setattr(gates, "ledger_append", crash)
    with pytest.raises(OSError):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, token, "hero", entity_id="ch-001")
    monkeypatch.setattr(gates, "ledger_append", real_ledger_append)
    rows = read_jsonl(project / "approvals.jsonl")
    assert len(rows) == 1 and not gates.verify_receipt(rows[0])  # receipt row without ledger: not yet valid
    found = receipts.find_approval(project, "hero", entity_id="ch-001")  # runs recovery
    assert found == rows[0] and gates.verify_receipt(found)
    assert read_jsonl(project / "approvals.jsonl") == rows  # no duplicate row
    assert _pending_wal() == []


def test_wal_entry_for_unconsumed_token_is_dropped_without_writing(project):
    token = _mint()
    token_hmac = gates.token_digest(token)
    gates.wal_write(token_hmac, {"token_hmac": token_hmac, "project_id": PROJECT_ID,
                                 "project_root": str(project.resolve()), "receipt": {"receipt_id": "x"}})
    assert receipts.recover_pending_approvals(project) == []
    assert _pending_wal() == []
    assert not (project / "approvals.jsonl").exists()
    assert gates.is_pending(token_hmac)  # token still usable


def test_unknown_token_fails_before_any_wal_entry(project):
    with pytest.raises(gates.GateTokenInvalid):
        receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE, RECORD, "not-a-token", "hero", entity_id="ch-001")
    assert _pending_wal() == []


# ---- #8 signed + ledgered generation receipts ----

def _gen(project, sha, **kw):
    args = dict(execution_id="e-1", tool="seedream_image", normalized_inputs_hash="11" * 32,
                output_sha256=sha, cost_usd=0.02, started_at="t0", finished_at="t1",
                model_endpoint="fake/text-to-image")
    args.update(kw)
    return receipts.record_generation(project, **args)


def test_generation_receipt_is_signed_ledgered_and_project_bound(project):
    r = _gen(project, "aa" * 32)
    assert r["project_id"] == "proj-alpha"
    assert gates.verify_generation_receipt(r)
    assert receipts.find_generation(project, "aa" * 32) == r
    assert receipts.find_generation(project, "aa" * 32, project_id="proj-beta") is None


def test_fabricated_generation_row_is_invisible(project):
    real = _gen(project, "aa" * 32)
    imported = "bb" * 32
    # (a) unsigned row, (b) real signature copied onto a different hash,
    # (c) correctly signed (attacker with the key) but not in the generation ledger
    forged_a = dict(real, receipt_id="f-a", output_sha256=imported); forged_a.pop("signature")
    forged_b = dict(real, receipt_id="f-b", output_sha256=imported)
    forged_c = dict(real, receipt_id="f-c", output_sha256=imported)
    forged_c["signature"] = gates.sign_receipt(forged_c)
    with open(project / "generation-receipts.jsonl", "a") as fh:
        for row in (forged_a, forged_b, forged_c):
            fh.write(json.dumps(row) + "\n")
    assert receipts.find_generation(project, imported) is None
    assert receipts.find_generation(project, "aa" * 32) == real
    assert [r["receipt_id"] for r in receipts.verified_generation_receipts(project)] == [real["receipt_id"]]


# ---- #6 per-shot storyboard receipt ----

def test_require_storyboard_receipt_matches_exact_shot_and_hash(project):
    from lib.canon_enforcement import storyboard_batch_record

    frames = {"shot-1": "aa" * 32, "shot-2": "bb" * 32}
    record = storyboard_batch_record(frames)
    token = _mint(record, stage="assets", scope="storyboard_batch")
    receipt = receipts.record_human_approval(project, PROJECT_ID, "assets", "storyboard_batch", record, token,
                                             "storyboard_batch", entity_id="storyboard_batch")
    assert receipts.require_storyboard_receipt(project, "shot-1", "aa" * 32) == receipt
    assert receipts.require_storyboard_receipt(project, "shot-2", "bb" * 32) == receipt
    # swapped between shots, unknown shot, wrong hash, wrong project
    for shot, sha in (("shot-1", "bb" * 32), ("shot-3", "aa" * 32), ("shot-1", "cc" * 32)):
        with pytest.raises(receipts.ReceiptError, match=shot):
            receipts.require_storyboard_receipt(project, shot, sha)
    with pytest.raises(receipts.ReceiptError):
        receipts.require_storyboard_receipt(project, "shot-1", "aa" * 32, project_id="proj-beta")
    # ordering of the map does not matter (canonical hashing sorts keys)
    assert record_sha256(storyboard_batch_record({"shot-2": "bb" * 32, "shot-1": "aa" * 32})) == record_sha256(record)


def test_require_storyboard_receipt_rejects_tampered_record(project):
    from lib.canon_enforcement import storyboard_batch_record

    record = storyboard_batch_record({"shot-1": "aa" * 32})
    token = _mint(record, stage="assets", scope="storyboard_batch")
    receipt = receipts.record_human_approval(project, PROJECT_ID, "assets", "storyboard_batch", record, token,
                                             "storyboard_batch", entity_id="storyboard_batch")
    # Edit the embedded record in the project-writable file to cover another frame.
    tampered = dict(receipt, record=storyboard_batch_record({"shot-1": "dd" * 32}))
    (project / "approvals.jsonl").write_text(json.dumps(tampered) + "\n")
    with pytest.raises(receipts.ReceiptError):
        receipts.require_storyboard_receipt(project, "shot-1", "dd" * 32)

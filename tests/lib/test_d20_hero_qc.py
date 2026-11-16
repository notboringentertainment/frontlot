"""D20 step 3 — hero QC: the hero budget across rotated series, the judge's
hero branch, both verifiers (candidate-time and downstream), the 1.4 gate
(record 1.1), the grandfather attestation and migration coverage. Invented
names only; real signed receipts against a per-test gates dir."""
from __future__ import annotations

import copy
import hashlib
import io
import json

import pytest
from PIL import Image

from lib import qc_receipts as qr
from lib import receipts
from lib.checkpoint import CheckpointValidationError, checkpoint_digest, write_checkpoint
from lib.headshot_verify import (
    HeadshotVerifyError, hero_migration_blockers, require_hero_verdict, verify_active_headshot, verify_headshot_candidate,
)
from lib.headshots import HeadshotError, active_headshots, headshot_request, verify_headshot_ref
from lib.look_spec import look_hash as _look_hash
from lib.pipeline_pin import PipelinePinError, prepare_migration_request
from lib.reference_import import (
    ORIGIN_IMPORTED_SYNTHETIC, finalize_reference_import, imported_image_ref, prepare_reference_import,
)
from lib.sheet_qc import policy
from scripts.gate_approve import GateHandlerError
from tests.lib.d19_helpers import JUDGE_MODEL, config_1_2
from tests.lib.look_lock_helpers import (
    CHAR, CHAR2, LOC, PROJECT, activate_look, approve_headshot, approve_request, character_look, location_look,
    look_packet_for, look_refs_for, pin_project,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401
    PIPELINE, approval_policy_decision, gates_dir, plain_decision_log, project, write_project_config,
)
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11
from tests.tools.test_sheet_judge import FakeAdapter, _all
from tools import prompt_builder as pb
from tools.qa.sheet_judge import SheetJudge

IMAGE_MODEL = "fake/text-to-image"


def write(pipeline_dir, stage, artifacts, *, status="completed"):
    return write_checkpoint(pipeline_dir, PROJECT, stage, status, artifacts, pipeline_type=PIPELINE, human_approved=True)


def _png(w, h, seed):
    d = hashlib.sha256(seed.encode()).digest()
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (d[0], d[1], d[2])).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def world(project, monkeypatch):
    """Registered project, signed 1.2 config, pinned 1.4, cast (CHAR, CHAR2),
    CHAR's look ratified, look_lock checkpoint written. No headshot yet."""
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
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")
    digest = write_project_config(project_dir, config_1_2(max_hero_attempts=3))
    pin_project(project_dir, "1.4")
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    log = plain_decision_log(); log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11((CHAR, CHAR2), (LOC,)), "decision_log": log})
    c, l = character_look(), location_look()
    activate_look(project_dir, c); activate_look(project_dir, l)
    write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)}, status="in_progress")
    return {"pipeline": pipeline_dir, "project": project_dir, "c": c, "l": l}


def _hero_asset(w, seed, *, size=(1024, 1024), entity=CHAR, look=None):
    """A real hero-sized PNG with a governed receipt (look_refs, sealed recipe, no headshot_ref)."""
    look = look or w["c"]
    data = _png(*size, seed); sha = hashlib.sha256(data).hexdigest()
    rel = f"canon/visual/objects/{sha}.png"
    (w["project"] / rel).parent.mkdir(parents=True, exist_ok=True)
    (w["project"] / rel).write_bytes(data)
    built = pb.build_prompt(look, role="hero", palette=["moss"])
    row = receipts.record_generation(w["project"], execution_id=f"exec-{seed}", tool="seedream_image", normalized_inputs_hash="a" * 64,
                                     output_sha256=sha, cost_usd=0.07, started_at="t", finished_at="t", model_endpoint=IMAGE_MODEL,
                                     prompt=built["prompt"], look_refs=look_refs_for(look), prompt_recipe=built["prompt_recipe"])
    ref = {"asset_id": sha, "path": rel, "role": "hero", "provenance": {"generator_kind": "model", "model_endpoint": IMAGE_MODEL,
           "prompt": built["prompt"], "generation_receipt_id": row["receipt_id"]}}
    return ref, row, built["prompt_recipe"]


def _series(w, *, entity=CHAR, look=None, builder=None, imported=False, grandfather=None):
    look = look or w["c"]
    key = {"entity_kind": "character", "entity_id": entity, "role": "hero", "look_hash": _look_hash(look),
           "headshot_receipt_id": None, "policy_bundle_sha256": policy.hero_bundle_sha256(),
           "builder_policy_sha256": "imported" if imported else (builder or pb.builder_policy_sha256()),
           "generation_endpoint": "imported" if imported else IMAGE_MODEL, "generation_model": "imported" if imported else IMAGE_MODEL,
           "judge_provider": "openai", "judge_model": JUDGE_MODEL}
    if grandfather is not None:
        key["grandfather"] = grandfather
    return key


def _budget(w, entity=CHAR, look=None):
    return qr.hero_budget_key(PROJECT, entity, _look_hash(look or w["c"]))


def _judge(w, ref, gen, answers, *, key=None, cap=3):
    a = qr.start_attempt(w["project"], key or _series(w), max_attempts=cap, budget_key=_budget(w), budget_cap=cap)
    qr.attach_generation(w["project"], a["attempt_id"], generation_receipt_id=gen["receipt_id"], asset_id=ref["asset_id"])
    r = SheetJudge(adapter=FakeAdapter(answers)).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": ref["asset_id"]})
    assert r.success, r.error
    return r.data["qc_receipt_id"], r.data["verdict"], a


def _config(w):
    from lib.project_config import load_verified_project_config
    return load_verified_project_config(w["project"])


def _pin(w):
    from lib.pipeline_pin import pinned_pipeline
    return pinned_pipeline(w["project"], "authored-film")


def _look(w, entity=CHAR):
    from lib.look_ingest import active_look_for
    return active_look_for(w["project"], "character", entity)


def _entry(w, candidates, *, recipe, entity=CHAR):
    look = _look(w, entity)
    e = {"entity_kind": "character", "entity_id": entity,
         "look_ref": {"entity_kind": "character", "entity_id": entity, "look_hash": look.look_hash, "receipt_id": look.receipt_id},
         "candidates": candidates}
    if recipe is not None:
        e["prompt_recipe"] = recipe
    return e


# ---- budget ----

class TestHeroBudget:
    def test_one_budget_across_rotated_series(self, world):
        w = world
        cap = 3
        a1 = qr.start_attempt(w["project"], _series(w), max_attempts=cap, budget_key=_budget(w), budget_cap=cap)
        assert a1["budget_n"] == 1 and a1["budget_cap"] == cap
        qr.void_attempt(w["project"], a1["attempt_id"], reason="reservation failed")  # still counts
        rotated = _series(w, builder="b" * 64)  # a different builder policy: new series, same allowance
        a2 = qr.start_attempt(w["project"], rotated, max_attempts=cap, budget_key=_budget(w), budget_cap=cap)
        qr.void_attempt(w["project"], a2["attempt_id"], reason="x")
        a3 = qr.start_attempt(w["project"], _series(w, builder="c" * 64), max_attempts=cap, budget_key=_budget(w), budget_cap=cap)
        assert a3["budget_n"] == 3 and a3["attempt_n"] == 1
        qr.void_attempt(w["project"], a3["attempt_id"], reason="x")
        with pytest.raises(qr.AttemptCapExceeded, match="hero budget"):
            qr.start_attempt(w["project"], _series(w, builder="d" * 64), max_attempts=cap, budget_key=_budget(w), budget_cap=cap)
        # another look is another allowance
        other = dict(w["c"], hair="a different cut")
        assert len(qr.hero_attempts_started(w["project"], CHAR, _look_hash(w["c"]))) == 3
        assert qr.hero_attempts_started(w["project"], CHAR, _look_hash(other)) == []

    def test_hero_attempt_shape_is_enforced(self, world):
        w = world
        with pytest.raises(qr.QCReceiptError, match="budget_key and budget_cap"):
            qr.start_attempt(w["project"], _series(w), max_attempts=3)
        with pytest.raises(qr.QCReceiptError, match="headshot_receipt_id must be null"):
            qr.start_attempt(w["project"], dict(_series(w), headshot_receipt_id="hs-1"), max_attempts=3, budget_key=_budget(w), budget_cap=3)
        with pytest.raises(qr.QCReceiptError, match="not the series' own"):
            qr.start_attempt(w["project"], _series(w), max_attempts=3, budget_key=_budget(w, entity=CHAR2), budget_cap=3)
        sheet_key = dict(_series(w), role="turnaround", headshot_receipt_id="hs-1", policy_bundle_sha256=policy.bundle_sha256())
        with pytest.raises(qr.QCReceiptError, match="only hero attempts"):
            qr.start_attempt(w["project"], sheet_key, max_attempts=3, budget_key=_budget(w), budget_cap=3)
        gf = dict(_series(w, grandfather=True), project_id=PROJECT)
        assert qr.series_sha256(gf) != qr.series_sha256(dict(_series(w), project_id=PROJECT))
        assert "grandfather" in qr.series_fields(gf) and "grandfather" not in qr.series_fields(dict(_series(w), project_id=PROJECT))


# ---- judge + candidate verifier ----

class TestHeroJudge:
    def test_generated_candidate_judged_and_verified(self, world):
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-a")
        qc_id, verdict, attempt = _judge(w, ref, gen, _all("hero"))
        row = qr.find_verdict_by_id(w["project"], qc_id)
        assert verdict == "pass" and row["role"] == "hero" and row["policy_bundle_sha256"] == policy.hero_bundle_sha256()
        assert row["headshot_asset_id"] is None and row["headshot_receipt_id"] is None
        entry = _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe=recipe)
        out = verify_headshot_candidate(w["project"], entry, entry["candidates"][0], active_look=_look(w), config=_config(w), pin=_pin(w))
        assert out["receipt_id"] == qc_id
        # judge saw the look's hair text, not a hero image
        with pytest.raises(HeadshotVerifyError, match="names no hero verdict"):
            verify_headshot_candidate(w["project"], _entry(w, [ref], recipe=recipe), ref, active_look=_look(w), config=_config(w), pin=_pin(w))
        with pytest.raises(HeadshotVerifyError, match="must carry the sealed prompt_recipe"):
            verify_headshot_candidate(w["project"], _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe=None),
                                      dict(ref, qc_receipt_id=qc_id), active_look=_look(w), config=_config(w), pin=_pin(w))

        class Pin13:
            name, version = "authored-film", "1.3"
        assert verify_headshot_candidate(w["project"], _entry(w, [ref], recipe=recipe), ref, active_look=_look(w), config=None, pin=Pin13) is None

    def test_judge_prompt_carries_look_text_and_local_rules_apply(self, world):
        w = world
        ref, gen, _ = _hero_asset(w, "hero-b")
        fake = FakeAdapter(_all("hero"))
        a = qr.start_attempt(w["project"], _series(w), max_attempts=3, budget_key=_budget(w), budget_cap=3)
        qr.attach_generation(w["project"], a["attempt_id"], generation_receipt_id=gen["receipt_id"], asset_id=ref["asset_id"])
        r = SheetJudge(adapter=fake).execute({"project_dir": str(w["project"]), "attempt_id": a["attempt_id"], "asset_id": ref["asset_id"]})
        assert r.success, r.error
        call = fake.calls[0]
        assert call["n_images"] == 1 and "Described hair:" in call["user"] and w["c"]["hair"] in call["user"]
        assert call["schema"]["properties"]["items"]["items"]["properties"]["id"]["enum"][0] == "single_subject"
        # landscape hero fails locally at $0
        wide, gen2, _ = _hero_asset(w, "hero-wide", size=(1536, 1024))
        a2 = qr.start_attempt(w["project"], _series(w), max_attempts=3, budget_key=_budget(w), budget_cap=3)
        qr.attach_generation(w["project"], a2["attempt_id"], generation_receipt_id=gen2["receipt_id"], asset_id=wide["asset_id"])
        r2 = SheetJudge(adapter=FakeAdapter(_all("hero"))).execute({"project_dir": str(w["project"]), "attempt_id": a2["attempt_id"], "asset_id": wide["asset_id"]})
        assert r2.success and r2.data["local"] and r2.data["failing_items"] == ["orientation"] and r2.cost_usd == 0

    def test_failed_verdict_needs_override_and_warn_is_free(self, world):
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-c")
        qc_id, verdict, _ = _judge(w, ref, gen, _all("hero", hair_matches="no", age_matches="unsure"))
        assert verdict == "fail"
        entry = _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe=recipe)
        with pytest.raises(HeadshotVerifyError, match="no signed qc_override covers \\['hair_matches'\\]"):
            verify_headshot_candidate(w["project"], entry, entry["candidates"][0], active_look=_look(w), config=_config(w), pin=_pin(w))
        ref2, gen2, _ = _hero_asset(w, "hero-d")
        qc2, verdict2, _ = _judge(w, ref2, gen2, _all("hero", age_matches="no", build_matches="unsure"))
        assert verdict2 == "pass"
        row = qr.find_verdict_by_id(w["project"], qc2)
        assert sorted(row["warnings"]) == ["age_matches", "build_matches"]

    def test_sheet_role_verdict_never_satisfies_hero(self, world):
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-e")
        qc_id, _, _ = _judge(w, ref, gen, _all("hero"))
        entry = _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe=recipe)
        other_ref, other_gen, _ = _hero_asset(w, "hero-f")
        with pytest.raises(HeadshotVerifyError, match="verdict is for asset"):
            verify_headshot_candidate(w["project"], _entry(w, [dict(other_ref, qc_receipt_id=qc_id)], recipe=recipe),
                                      dict(other_ref, qc_receipt_id=qc_id), active_look=_look(w), config=_config(w), pin=_pin(w))
        assert verify_headshot_candidate(w["project"], entry, entry["candidates"][0], active_look=_look(w), config=_config(w), pin=_pin(w))


# ---- imported candidate ----

def _import_for(w, entity, seed):
    src = w["project"] / ".staging" / f"{seed}.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_png(1024, 1280, seed))
    prepared = prepare_reference_import(w["project"], PROJECT, src, origin_class=ORIGIN_IMPORTED_SYNTHETIC,
                                        origin_tool="elsewhere-gen", entity_id=entity, request_id=f"import-{entity}-1")
    req = json.loads(prepared.request_path.read_text())
    receipt = approve_request(req, w["project"])
    imported = finalize_reference_import(w["project"], receipt["receipt_id"])
    return imported_image_ref(w["project"], imported), receipts.find_generation(w["project"], imported.normalized_pixel_hash)


class TestImportedCandidate:
    def test_import_is_judged_and_bound_to_its_character(self, world):
        w = world
        ref, gen = _import_for(w, CHAR, "face-import")
        assert gen["generator_kind"] == "imported"
        qc_id, verdict, _ = _judge(w, ref, gen, _all("hero"), key=_series(w, imported=True))
        assert verdict == "pass"
        entry = _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe=None)
        assert verify_headshot_candidate(w["project"], entry, entry["candidates"][0], active_look=_look(w), config=_config(w), pin=_pin(w))
        # a generated-style series over an imported receipt is refused by the judge's preflight
        wrong = qr.start_attempt(w["project"], dict(_series(w), builder_policy_sha256="e" * 64), max_attempts=3, budget_key=_budget(w), budget_cap=3)
        qr.attach_generation(w["project"], wrong["attempt_id"], generation_receipt_id=gen["receipt_id"], asset_id=ref["asset_id"])
        r = SheetJudge(adapter=FakeAdapter(_all("hero"))).execute({"project_dir": str(w["project"]), "attempt_id": wrong["attempt_id"], "asset_id": ref["asset_id"]})
        assert not r.success and "imported" in r.error  # the attempt stays open in its own series; it counted against the budget
        # the same file cited for CHAR2 (whose look is not even active): refused on entity binding
        c2 = dict(w["c"], entity_id=CHAR2)
        activate_look(w["project"], c2)
        entry2 = _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe=None, entity=CHAR2)
        with pytest.raises(HeadshotVerifyError, match="bound to character"):
            verify_headshot_candidate(w["project"], entry2, entry2["candidates"][0], active_look=_look(w, CHAR2), config=_config(w), pin=_pin(w))
        # inspection #6: a permitted re-import of the same pixels for the same character (a later
        # attestation receipt) must not invalidate the candidate bound to the first one
        src = w["project"] / ".staging" / "again.png"; src.write_bytes(_png(1024, 1280, "face-import"))
        again = prepare_reference_import(w["project"], PROJECT, src, origin_class=ORIGIN_IMPORTED_SYNTHETIC,
                                         origin_tool="elsewhere-gen", entity_id=CHAR, request_id=f"import-{CHAR}-2")
        approve_request(json.loads(again.request_path.read_text()), w["project"])
        assert verify_headshot_candidate(w["project"], entry, entry["candidates"][0], active_look=_look(w), config=_config(w), pin=_pin(w))
        with pytest.raises(HeadshotVerifyError, match="carries no prompt_recipe"):
            verify_headshot_candidate(w["project"], _entry(w, [dict(ref, qc_receipt_id=qc_id)], recipe={"look_hash": _look(w).look_hash,
                                      "builder_version": "1.3", "fields_used": ["hair"], "rendered_sha256": "f" * 64}),
                                      dict(ref, qc_receipt_id=qc_id), active_look=_look(w), config=_config(w), pin=_pin(w))


class TestLegacyImport:
    def test_unbound_legacy_import_accepted_only_for_a_grandfathered_hero(self, world):
        """A pre-D20 import has no character binding; only the grandfathered legacy path accepts it."""
        w = world
        src = w["project"] / ".staging" / "old.png"; src.parent.mkdir(exist_ok=True); src.write_bytes(_png(1024, 1280, "old-import"))
        prepared = prepare_reference_import(w["project"], PROJECT, src, origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="t", request_id="import-old-1")
        receipt = approve_request(json.loads(prepared.request_path.read_text()), w["project"])
        imported = finalize_reference_import(w["project"], receipt["receipt_id"])
        ref = imported_image_ref(w["project"], imported)
        entry = _entry(w, [ref], recipe=None)
        with pytest.raises(HeadshotVerifyError, match="bound to character"):
            verify_headshot_candidate(w["project"], entry, ref, active_look=_look(w), config=_config(w), pin=_pin(w))
        assert verify_headshot_candidate(w["project"], entry, ref, active_look=_look(w), config=_config(w), pin=_pin(w), require_verdict=False) is None


# ---- checkpoint + gate ----

def _pending_packet(w, candidates, recipe, entity=CHAR):
    return {"version": "1.1", "state": "pending", "characters": [_entry(w, candidates, recipe=recipe, entity=entity)]}


class TestGate14:
    def test_pending_write_refuses_unjudged_and_gate_seals_record_1_1(self, world):
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-g")
        with pytest.raises(CheckpointValidationError, match="names no hero verdict|qc_receipt_id"):
            write(w["pipeline"], "headshots", {"headshot_packet": _pending_packet(w, [ref], recipe)}, status="awaiting_human")
        with pytest.raises(CheckpointValidationError, match="1.4 writes headshot_packet 1.1"):
            packet10 = dict(_pending_packet(w, [ref], recipe), version="1.0")
            write(w["pipeline"], "headshots", {"headshot_packet": packet10}, status="awaiting_human")
        qc_id, _, _ = _judge(w, ref, gen, _all("hero"))
        write(w["pipeline"], "headshots", {"headshot_packet": _pending_packet(w, [dict(ref, qc_receipt_id=qc_id)], recipe)}, status="awaiting_human")
        digest = checkpoint_digest(w["pipeline"] / PROJECT / "checkpoint_headshots.json")
        req_path = headshot_request(w["project"], PROJECT, CHAR, request_id=f"headshot-{CHAR}-1")
        req = json.loads(req_path.read_text()); req["source_checkpoint_digest"] = digest
        req_path.write_text(json.dumps(req))  # the gate reloads the pending file under its lock
        receipt = approve_request(req, w["project"], selection=1)
        rec = receipt["record"]
        assert rec["record_version"] == "1.1" and rec["qc_receipt_id"] == qc_id and rec["generation_receipt_id"] == gen["receipt_id"]
        current = active_headshots(w["project"])[CHAR]
        assert current.asset_id == ref["asset_id"]
        assert verify_active_headshot(w["project"], current, active_look=_look(w), config=_config(w), pin=_pin(w))["receipt_id"] == qc_id
        assert verify_headshot_ref(w["project"], {"entity_id": CHAR, "asset_id": ref["asset_id"], "approval_receipt_id": receipt["receipt_id"]})
        # approved packet 1.1 must carry the same qc receipt
        approved = {"version": "1.1", "state": "approved", "characters": [{
            "entity_kind": "character", "entity_id": CHAR, "look_ref": _entry(w, [], recipe=recipe)["look_ref"], "prompt_recipe": recipe,
            "hero": dict(ref, qc_receipt_id=qc_id), "origin": "generated", "normalized_pixel_hash": ref["asset_id"],
            "approval_receipt_id": receipt["receipt_id"], "candidates_checkpoint_digest": digest, "candidates_rejected": [],
            "qc_receipt_id": qc_id}]}
        write(w["pipeline"], "headshots", {"headshot_packet": approved}, status="in_progress")
        bad = copy.deepcopy(approved); bad["characters"][0]["qc_receipt_id"] = "other"
        with pytest.raises(CheckpointValidationError, match="differ from the signed headshot record"):
            write(w["pipeline"], "headshots", {"headshot_packet": bad}, status="in_progress")

    def test_gate_refuses_a_candidate_whose_verdict_is_missing(self, world):
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-h")
        qc_id, _, _ = _judge(w, ref, gen, _all("hero"))
        write(w["pipeline"], "headshots", {"headshot_packet": _pending_packet(w, [dict(ref, qc_receipt_id=qc_id)], recipe)}, status="awaiting_human")
        # tamper after the write: point the candidate at a foreign verdict id
        path = w["pipeline"] / PROJECT / "checkpoint_headshots.json"
        cp = json.loads(path.read_text()); cp["artifacts"]["headshot_packet"]["characters"][0]["candidates"][0]["qc_receipt_id"] = "nope"
        path.write_text(json.dumps(cp))
        req = json.loads(headshot_request(w["project"], PROJECT, CHAR, request_id=f"headshot-{CHAR}-2").read_text())
        # inspection #3: refused at DISPLAY time, before any selection
        from scripts.gate_approve import headshot_candidates
        with pytest.raises(GateHandlerError, match="not presentable.*not a verified verdict"):
            headshot_candidates(req, w["project"])
        with pytest.raises(GateHandlerError, match="not a verified verdict"):
            approve_request(req, w["project"], selection=1)
        assert CHAR not in active_headshots(w["project"])

    def test_pre_commit_rechecks_the_supersession_tip(self, world):
        """Inspection #4: a record constructed while no hero was active must not
        commit after another approval made one."""
        from scripts.gate_approve import construct
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-i")
        qc_id, _, _ = _judge(w, ref, gen, _all("hero"))
        write(w["pipeline"], "headshots", {"headshot_packet": _pending_packet(w, [dict(ref, qc_receipt_id=qc_id)], recipe)}, status="awaiting_human")
        req_a = json.loads(headshot_request(w["project"], PROJECT, CHAR, request_id=f"headshot-{CHAR}-a").read_text())
        req_b = json.loads(headshot_request(w["project"], PROJECT, CHAR, request_id=f"headshot-{CHAR}-b").read_text())
        built_a = construct(w["project"], req_a, selection=1)
        assert built_a.envelope["supersedes_receipt_id"] is None
        receipt_b = approve_request(req_b, w["project"], selection=1)
        with pytest.raises(GateHandlerError, match="changed while approving"):
            built_a.pre_commit_check()
        assert active_headshots(w["project"])[CHAR].receipt_id == receipt_b["receipt_id"]

    def test_migration_coverage_verifies_1_1_records_too(self, world):
        """Inspection #5: a 1.1 record whose raw judge response vanished is a blocker, not trusted by version."""
        w = world
        ref, gen, recipe = _hero_asset(w, "hero-j")
        qc_id, _, _ = _judge(w, ref, gen, _all("hero"))
        write(w["pipeline"], "headshots", {"headshot_packet": _pending_packet(w, [dict(ref, qc_receipt_id=qc_id)], recipe)}, status="awaiting_human")
        req = json.loads(headshot_request(w["project"], PROJECT, CHAR, request_id=f"headshot-{CHAR}-j").read_text())
        approve_request(req, w["project"], selection=1)
        assert hero_migration_blockers(w["project"]) == []
        row = qr.find_verdict_by_id(w["project"], qc_id)
        (w["project"] / "canon/qc/objects" / f"{row['raw_response_asset_id']}.json").unlink()
        blockers = hero_migration_blockers(w["project"])
        assert blockers and "raw provider response" in blockers[0]


# ---- legacy heroes: grandfather + migration coverage ----

@pytest.fixture
def legacy(project, monkeypatch):
    """A 1.3 project whose CHAR has a legacy (1.0-record) hero, then re-signed to config 1.2."""
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
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")
    digest = write_project_config(project_dir, config_1_2(max_hero_attempts=3))
    pin_project(project_dir, "1.3")
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    log = plain_decision_log(); log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11((CHAR,), (LOC,)), "decision_log": log})
    c, l = character_look(), location_look()
    activate_look(project_dir, c); activate_look(project_dir, l)
    write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)}, status="in_progress")
    w = {"pipeline": pipeline_dir, "project": project_dir, "c": c, "l": l}
    ref, gen, recipe = _hero_asset(w, "legacy-hero")
    _, hs = approve_headshot(project_dir, c, ref["asset_id"], "d" * 64, recipe=recipe)
    w.update({"ref": ref, "gen": gen, "recipe": recipe, "hs": hs})
    return w


class TestGrandfather:
    def test_migration_refused_until_grandfathered_then_verified_under_1_4(self, legacy):
        w = legacy
        assert hero_migration_blockers(w["project"])
        with pytest.raises(PipelinePinError, match="headshot_grandfather"):
            prepare_migration_request(w["project"], PROJECT, "authored-film", "1.4")
        # the grandfather attempt runs under pin 1.3 (flagged in its series)
        with pytest.raises(Exception, match="hero QC needs authored-film 1.4"):
            _judge(w, w["ref"], w["gen"], _all("hero"))
        qc_id, verdict, _ = _judge(w, w["ref"], w["gen"], _all("hero"), key=_series(w, grandfather=True))
        assert verdict == "pass"
        req = {"request_id": f"grandfather-{CHAR}-1", "project_id": PROJECT, "stage": "headshots", "scope": f"character:{CHAR}",
               "kind": "headshot_grandfather", "entity_id": CHAR, "artifact": None, "approval_record": None, "qc_receipt_id": qc_id,
               "source_checkpoint_digest": None, "summary": "attest", "preview_paths": []}
        (w["project"] / ".gate-requests").mkdir(exist_ok=True)
        (w["project"] / ".gate-requests" / f"{req['request_id']}.json").write_text(json.dumps(req))
        receipt = approve_request(req, w["project"])  # the gate constructs the record itself
        assert receipt["kind"] == "headshot_grandfather" and receipt["attests_receipt_id"] == w["hs"]["receipt_id"]
        assert receipt["record"]["legacy_headshot_receipt_id"] == w["hs"]["receipt_id"] and receipt["record"]["qc_receipt_id"] == qc_id
        # the legacy receipt is still the chain tip; nothing was superseded
        assert active_headshots(w["project"])[CHAR].receipt_id == w["hs"]["receipt_id"]
        assert hero_migration_blockers(w["project"]) == []
        prepare_migration_request(w["project"], PROJECT, "authored-film", "1.4", request_id="migrate-1-4")
        mig = json.loads((w["project"] / ".gate-requests" / "migrate-1-4.json").read_text())
        approve_request(mig, w["project"])
        from lib.pipeline_pin import refresh_cache
        refresh_cache(w["project"], "authored-film")
        assert _pin(w).version == "1.4"
        current = active_headshots(w["project"])[CHAR]
        assert verify_active_headshot(w["project"], current, active_look=_look(w), config=_config(w), pin=_pin(w))["receipt_id"] == qc_id
        assert verify_headshot_ref(w["project"], {"entity_id": CHAR, "asset_id": w["ref"]["asset_id"], "approval_receipt_id": w["hs"]["receipt_id"]})
        # inspection #1: the grandfathered hero can be carried in an approved 1.1 packet (cast extension)
        look = _look(w)
        approved = {"version": "1.1", "state": "approved", "characters": [{
            "entity_kind": "character", "entity_id": CHAR,
            "look_ref": {"entity_kind": "character", "entity_id": CHAR, "look_hash": look.look_hash, "receipt_id": look.receipt_id},
            "prompt_recipe": w["recipe"], "hero": w["ref"], "origin": "generated", "normalized_pixel_hash": w["ref"]["asset_id"],
            "approval_receipt_id": w["hs"]["receipt_id"], "candidates_checkpoint_digest": "d" * 64, "candidates_rejected": [],
            "qc_receipt_id": qc_id}]}
        write(w["pipeline"], "headshots", {"headshot_packet": approved}, status="in_progress")
        bad = copy.deepcopy(approved); bad["characters"][0]["qc_receipt_id"] = "other"
        with pytest.raises(CheckpointValidationError, match="not the verdict its headshot_grandfather attestation names"):
            write(w["pipeline"], "headshots", {"headshot_packet": bad}, status="in_progress")

    def test_sheet_verifier_refuses_an_unattested_legacy_hero_under_1_4(self, legacy):
        """Inspection #2: no sheet is accepted on a hero that does not verify under the current hero policy."""
        from lib.checkpoint import CheckpointValidationError as CVE
        from lib.sheet_verify import verify_character_sheet
        w = legacy

        class Pin14:
            name, version = "authored-film", "1.4"
        current = active_headshots(w["project"])[CHAR]
        receipts_by_sha = {r["output_sha256"]: r for r in receipts.verified_generation_receipts(w["project"])}
        entry = {"id": CHAR, "hero": w["ref"], "sheet": {}, "status": "draft"}
        with pytest.raises(CVE, match="no headshot_grandfather attestation"):
            verify_character_sheet(w["project"], entry, active_look=_look(w), active_headshot=current, receipts_by_sha=receipts_by_sha,
                                   qc_required=True, config=_config(w), qc_must_be_present=False, pin=Pin14)
        # under 1.3 the same entry is fine (nothing a 1.3 project loses)
        verify_character_sheet(w["project"], entry, active_look=_look(w), active_headshot=current, receipts_by_sha=receipts_by_sha,
                               qc_required=True, config=_config(w), qc_must_be_present=False, pin=_pin(w))

    def test_grandfather_verdict_must_be_flagged_and_for_the_tip(self, legacy):
        w = legacy
        qc_id, _, _ = _judge(w, w["ref"], w["gen"], _all("hero"), key=_series(w, grandfather=True))
        with pytest.raises(HeadshotVerifyError, match="grandfather flag"):
            require_hero_verdict(w["project"], qc_receipt_id=qc_id, asset_id=w["ref"]["asset_id"], entity_id=CHAR, active_look=_look(w),
                                 config=_config(w), gen_receipt=w["gen"], grandfather=False)
        other, other_gen, _ = _hero_asset(w, "not-the-tip")
        req = {"request_id": f"grandfather-{CHAR}-2", "project_id": PROJECT, "stage": "headshots", "scope": f"character:{CHAR}",
               "kind": "headshot_grandfather", "entity_id": CHAR, "artifact": None, "approval_record": None,
               "source_checkpoint_digest": None, "summary": "attest", "preview_paths": []}
        (w["project"] / ".gate-requests").mkdir(exist_ok=True)
        bad = dict(req, qc_receipt_id="nope")
        (w["project"] / ".gate-requests" / f"{req['request_id']}.json").write_text(json.dumps(bad))
        with pytest.raises(GateHandlerError, match="not a verified verdict"):
            approve_request(bad, w["project"])
        # under 1.4 without any attestation the legacy hero cannot back a sheet
        class Pin14:
            name, version = "authored-film", "1.4"
        with pytest.raises(HeadshotVerifyError, match="no headshot_grandfather attestation"):
            verify_active_headshot(w["project"], active_headshots(w["project"])[CHAR], active_look=_look(w), config=_config(w), pin=Pin14)
        assert verify_active_headshot(w["project"], active_headshots(w["project"])[CHAR], active_look=_look(w), config=_config(w), pin=_pin(w)) is None

"""Visual bible contract tests (PLAN §12, Slice 3).

The visual_bible stage and the 1.1 scene_plan / asset_manifest checks are
enforced at checkpoint-write time by lib/canon_enforcement.py. Every fixture
here mints a real one-use gate token (per-test OPENMONTAGE_GATES_DIR) and
records real generation receipts for fake PNG bytes, so the tests exercise
the receipt path, not a stub of it. Invented names only.
"""

import copy
import json
import os

import pytest

from lib import receipts
from lib.canon_enforcement import (
    character_approval_record,
    storyboard_batch_record,
)
from lib.checkpoint import CheckpointValidationError, write_checkpoint
from lib.state_io import append_jsonl
from schemas.artifacts import validate_artifact
from tests.contracts.test_phase0_contracts import sample_artifact
from tests.lib.test_authored_film_contract import (  # noqa: F401  (gates_dir autouse)
    PALETTE,
    PIPELINE,
    PROJECT_CONFIG,
    VIDEO_ENDPOINT,
    approval_policy_decision,
    approve,
    approved_visual_bible,
    authored_script,
    canon_packet,
    character_entry,
    fake_image,
    gates_dir,
    plain_decision_log,
    project,
    write_project_config,
)

CHAR = "char-01-deadbeef"
CHAR2 = "char-02-cafef00d"
CHAR3 = "char-03-0badf00d"
LOC = "loc-01-feedface"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def canon_packet_v11() -> dict:
    packet = canon_packet()
    packet["version"] = "1.1"
    packet["characters"] = [
        {"id": CHAR, "name": "Orrin Vale"},
        {"id": CHAR2, "name": "Tamsin Reel"},
        {"id": CHAR3, "name": "Bex Halloran"},
    ]
    packet["locations"] = [{"id": LOC, "name": "The Salt Station"}]
    return packet


def proposal_v11(characters=(CHAR,), locations=(LOC,)) -> dict:
    packet = sample_artifact("proposal_packet")
    packet["version"] = "1.1"
    packet["runtime_shape"] = {"format": "trailer"}
    packet["cast"] = {"character_ids": list(characters), "location_ids": list(locations)}
    return packet


def scene_plan_v11(*, entity_free: bool = False, endpoint: str = VIDEO_ENDPOINT) -> dict:
    scene = {
        "id": "scene-1",
        "type": "generated",
        "description": "Wide shot of the empty station corridor.",
        "start_seconds": 0,
        "end_seconds": 60,
        "script_section_id": "s1",
        "canon_refs": ["beat-001", "lock-001"],
        "character_refs": [] if entity_free else [CHAR],
        "location_ref": None if entity_free else LOC,
        "entity_free": entity_free,
        "model_endpoint": endpoint,
        "shots": [{"shot_id": "shot-1", "description": "Corridor, dolly in."}],
    }
    return {"version": "1.1", "scenes": [scene]}


def _ref(image_ref: dict, entity_id: str) -> dict:
    return {
        "asset_id": image_ref["asset_id"],
        "path": image_ref["path"],
        "role": image_ref["role"],
        "visual_bible_entity_id": entity_id,
    }


def shot_asset(project_dir, seed: str, *, asset_class: str, references: list[dict],
               usage_status: str = "selected", endpoint: str = VIDEO_ENDPOINT,
               shot_id: str = "shot-1", receipt: bool = True) -> dict:
    image = fake_image(project_dir, seed, subdir="assets/shots", receipt=receipt, role="take")
    asset = {
        "id": f"asset-{seed}",
        "type": "image" if asset_class == "storyboard_frame" else "video",
        "path": image["path"],
        "source_tool": "seedance_video",
        "scene_id": "scene-1",
        "asset_class": asset_class,
        "shot_id": shot_id,
        "continuity": {
            "canon_refs": ["lock-001"],
            "references_applied": references,
            "risk_notes_applied": [],
        },
    }
    if asset_class == "shot_visual":
        asset.update({"take_id": f"take-{seed}", "usage_status": usage_status,
                      "model_endpoint": endpoint})
    return asset


def approve_storyboards(project_dir, *assets: dict) -> None:
    from lib.pathsafe import sha256_file

    frames = {a["shot_id"]: sha256_file(project_dir / a["path"]) for a in assets}
    approve(project_dir, "assets", "storyboard_batch", storyboard_batch_record(frames),
            "storyboard_batch", "storyboard_batch")


def write(pipeline_dir, stage: str, artifacts: dict, *, status: str = "completed"):
    return write_checkpoint(
        pipeline_dir, "p", stage, status, artifacts,
        pipeline_type=PIPELINE, human_approved=True,
    )


def setup_through_proposal(pipeline_dir, *, cast_characters=(CHAR,), cast_locations=(LOC,)):
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    write(pipeline_dir, "proposal", {
        "proposal_packet": proposal_v11(cast_characters, cast_locations),
        "decision_log": plain_decision_log(),
    })


def setup_through_visual_bible(pipeline_dir, project_dir, **cast) -> dict:
    setup_through_proposal(pipeline_dir, **cast)
    bible = approved_visual_bible(
        project_dir,
        characters=list(cast.get("cast_characters", (CHAR,))),
        locations=list(cast.get("cast_locations", (LOC,))),
    )
    write(pipeline_dir, "visual_bible", {"visual_bible": bible})
    write(pipeline_dir, "script", {"script": authored_script()})
    return bible


def setup_through_scene_plan(pipeline_dir, project_dir, *, entity_free=False) -> dict:
    bible = setup_through_visual_bible(pipeline_dir, project_dir)
    write(pipeline_dir, "scene_plan", {"scene_plan": scene_plan_v11(entity_free=entity_free)})
    return bible


def hero_ref(bible: dict, cid: str = CHAR) -> dict:
    entry = next(c for c in bible["characters"] if c["id"] == cid)
    return _ref(entry["hero"], cid)


def establishing_ref(bible: dict, lid: str = LOC) -> dict:
    entry = next(l for l in bible["locations"] if l["id"] == lid)
    return _ref(entry["establishing"], lid)


# ---------------------------------------------------------------------------
# visual_bible stage
# ---------------------------------------------------------------------------

class TestVisualBibleStage:
    def test_schema_valid_stage_write_completes(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC], poster=True)
        validate_artifact("visual_bible", bible)
        path = write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["status"] == "completed"
        assert saved["artifacts"]["visual_bible"]["characters"][0]["status"] == "approved"

    def test_approved_entry_without_receipt_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, locations=[LOC])
        entry = character_entry(project_dir, CHAR, approve_entry=False)
        entry["status"] = "approved"
        entry["approval_receipt_id"] = "self-attested"
        bible["characters"].append(entry)
        with pytest.raises(CheckpointValidationError, match="no verified approval receipt"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_prompt_edit_after_approval_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        bible["characters"][0]["approved_prompt_block"] += " wearing a hat"
        with pytest.raises(CheckpointValidationError, match="changed after approval"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_forged_approval_row_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, locations=[LOC])
        entry = character_entry(project_dir, CHAR, approve_entry=False)
        entry["status"] = "approved"
        entry["approval_receipt_id"] = "forged-1"
        bible["characters"].append(entry)
        from lib.canonical_json import record_sha256

        # Correct kind, entity, and record hash — but written straight into the
        # project-writable approvals.jsonl with no gate signature/ledger entry.
        append_jsonl(receipts.approvals_path(project_dir), {
            "receipt_id": "forged-1", "kind": "sheet", "project_id": "p",
            "stage": "visual_bible", "scope": f"sheet:{CHAR}", "entity_id": CHAR,
            "record_sha256": record_sha256(character_approval_record(entry, PALETTE)),
            "approved_at": "2026-08-25T00:00:00+00:00", "user_response": "approve",
        })
        with pytest.raises(CheckpointValidationError, match="no verified approval receipt"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_hash_mismatch_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        (project_dir / bible["characters"][0]["hero"]["path"]).write_bytes(b"swapped bytes")
        with pytest.raises(CheckpointValidationError, match="content hash mismatch"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_missing_generation_receipt_is_rejected_synthetic_only(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        # An "imported" image: bytes on disk, asset_id correct, but no tool ever
        # produced it, so it has no generation receipt.
        imported = fake_image(project_dir, "imported-photo", receipt=False, role="establishing")
        loc = bible["locations"][0]
        loc["establishing"] = imported
        loc["status"] = "draft"
        loc.pop("approval_receipt_id")
        bible["locations"].append(copy.deepcopy(loc))
        bible["locations"].pop(0)
        with pytest.raises(CheckpointValidationError, match="no generation receipt"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    @pytest.mark.parametrize("bad_path", ["../outside.png", "/etc/hosts"])
    def test_escaping_paths_are_rejected(self, project, bad_path):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        bible["characters"][0]["hero"]["path"] = bad_path
        with pytest.raises(CheckpointValidationError):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_symlink_path_is_rejected(self, project, tmp_path):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        hero = bible["characters"][0]["hero"]
        real = project_dir / hero["path"]
        outside = tmp_path / "elsewhere.png"
        outside.write_bytes(real.read_bytes())
        real.unlink()
        os.symlink(outside, real)
        with pytest.raises(CheckpointValidationError, match="not a safe project-local file"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_cast_coverage_requires_every_cast_entity(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir, cast_characters=(CHAR, CHAR2))
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        with pytest.raises(CheckpointValidationError, match=CHAR2):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_cast_cap_from_project_config_is_binding(self, project):
        pipeline_dir, project_dir = project
        assert PROJECT_CONFIG["cast_cap"]["characters"] == 2
        setup_through_proposal(pipeline_dir, cast_characters=(CHAR, CHAR2, CHAR3))
        bible = approved_visual_bible(
            project_dir, characters=[CHAR, CHAR2, CHAR3], locations=[LOC]
        )
        with pytest.raises(CheckpointValidationError, match="cast cap exceeded"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_supersede_without_ruling_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR, CHAR2], locations=[LOC])
        old = bible["characters"][1]
        old["status"] = "superseded"
        old["superseded_by"] = CHAR
        old.pop("approval_receipt_id")
        with pytest.raises(CheckpointValidationError, match=f"visual:{CHAR2}"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

        ruling = plain_decision_log()
        ruling["decisions"] = [{
            "decision_id": "d-visual-1", "stage": "visual_bible", "category": "canon_ruling",
            "subject": "Replace the second sheet", "question_id": f"visual:{CHAR2}",
            "options_considered": [{"option_id": "replace", "label": "Replace", "score": 1.0,
                                    "reason": "Writer ruled"}],
            "selected": "replace", "reason": "Writer ruled at the gate.", "user_approved": True,
        }]
        write(pipeline_dir, "visual_bible", {"visual_bible": bible, "decision_log": ruling})

    def test_egress_missing_blocks_visual_bible(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        no_egress = {k: v for k, v in PROJECT_CONFIG.items() if k != "provider_egress"}
        digest = write_project_config(project_dir, no_egress)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        log = plain_decision_log()
        log["decisions"] = [approval_policy_decision(digest)]
        with pytest.raises(CheckpointValidationError, match="provider_egress"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible, "decision_log": log})


# ---------------------------------------------------------------------------
# Project config binding and paid-call resume safety
# ---------------------------------------------------------------------------

class TestConfigBinding:
    def test_config_digest_change_without_approval_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        raised = dict(PROJECT_CONFIG, budget_usd_cap=5000.0)
        write_project_config(project_dir, raised, bind=False)
        with pytest.raises(CheckpointValidationError, match="has no approval"):
            write(pipeline_dir, "scene_plan", {"scene_plan": scene_plan_v11()})

    def test_decision_without_receipt_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        raised = dict(PROJECT_CONFIG, budget_usd_cap=5000.0)
        digest = write_project_config(project_dir, raised, bind=False)
        log = plain_decision_log()
        log["decisions"] = [approval_policy_decision(digest)]
        with pytest.raises(CheckpointValidationError, match="no verified approval receipt"):
            write(pipeline_dir, "scene_plan", {"scene_plan": scene_plan_v11(), "decision_log": log})

    def test_indeterminate_paid_call_halts_checkpoint_write(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        append_jsonl(project_dir / "cost-reservations.jsonl", {
            "reservation_id": "res-1", "idempotency_key": "res-1", "tool": "seedream_image",
            "endpoint": "fake/text-to-image", "normalized_inputs_hash": "a" * 64,
            "reserved_usd": 0.05, "state": "submitting", "provider_request_id": None,
            "at": "2026-08-25T00:00:00+00:00",
        })
        with pytest.raises(CheckpointValidationError, match="PAID CALL INDETERMINATE.*res-1"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        assert not (pipeline_dir / "p" / "checkpoint_visual_bible.json").exists()


# ---------------------------------------------------------------------------
# scene_plan 1.1
# ---------------------------------------------------------------------------

class TestScenePlanV11:
    def test_needs_review_scene_plan_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        plan = scene_plan_v11()
        plan["migration_status"] = "needs_review"
        with pytest.raises(CheckpointValidationError, match="needs_review"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})

    def test_valid_scene_plan_completes(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        write(pipeline_dir, "scene_plan", {"scene_plan": scene_plan_v11()})

    def test_override_without_reason_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        plan = scene_plan_v11()
        other = copy.deepcopy(plan["scenes"][0])
        other.update({"id": "scene-2", "model_endpoint": "fake/other-video",
                      "shots": [{"shot_id": "shot-2", "description": "Insert."}]})
        plan["scenes"].append(other)
        with pytest.raises(CheckpointValidationError, match="model_override"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        other["model_override"] = {"endpoint": "fake/other-video", "reason": "   "}
        with pytest.raises(CheckpointValidationError, match="no reason"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        other["model_override"] = {"endpoint": "fake/other-video",
                                   "reason": "Needs a longer clip than the default supports."}
        write(pipeline_dir, "scene_plan", {"scene_plan": plan})

    def test_unapproved_entity_ref_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        plan = scene_plan_v11()
        plan["scenes"][0]["character_refs"] = [CHAR, CHAR2]
        with pytest.raises(CheckpointValidationError, match=CHAR2):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})


# ---------------------------------------------------------------------------
# assets 1.1
# ---------------------------------------------------------------------------

class TestAssetsV11:
    def test_entity_free_shot_passes_without_references(self, project):
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=[])
        approve_storyboards(project_dir, board)
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=[])
        write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board, take]}})

    def test_entity_shot_without_references_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir)
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=[])
        with pytest.raises(CheckpointValidationError, match="missing approved sheets"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board]}})

    def test_entity_shot_with_references_completes(self, project):
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        refs = [hero_ref(bible), establishing_ref(bible)]
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        approve_storyboards(project_dir, board)
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs)
        write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board, take]}})

    def test_reference_must_be_an_approved_bible_image(self, project):
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        stray = fake_image(project_dir, "stray", role="hero")
        refs = [_ref(stray, CHAR), establishing_ref(bible)]
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        with pytest.raises(CheckpointValidationError, match="not an approved visual_bible ImageRef"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board]}})

    def test_mixed_selected_takes_rejected_but_rejected_candidate_ignored(self, project):
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        refs = [hero_ref(bible), establishing_ref(bible)]
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        approve_storyboards(project_dir, board)
        good = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs)
        other = shot_asset(project_dir, "take-2", asset_class="shot_visual", references=refs,
                           endpoint="fake/other-video", usage_status="rejected")
        write(pipeline_dir, "assets",
              {"asset_manifest": {"version": "1.1", "assets": [board, good, other]}})
        other["usage_status"] = "selected"
        with pytest.raises(CheckpointValidationError, match="strategies never mix"):
            write(pipeline_dir, "assets",
                  {"asset_manifest": {"version": "1.1", "assets": [board, good, other]}})

    def test_missing_storyboard_approval_blocks_selected_take(self, project):
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        refs = [hero_ref(bible), establishing_ref(bible)]
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs)
        with pytest.raises(CheckpointValidationError, match="no storyboard_frame"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [take]}})
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        with pytest.raises(CheckpointValidationError, match="storyboard_batch approval receipt"):
            write(pipeline_dir, "assets",
                  {"asset_manifest": {"version": "1.1", "assets": [board, take]}})

    def test_needs_review_manifest_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        manifest = {"version": "1.1", "assets": [], "migration_status": "needs_review"}
        with pytest.raises(CheckpointValidationError, match="needs_review"):
            write(pipeline_dir, "assets", {"asset_manifest": manifest})

    def test_asset_without_generation_receipt_is_rejected(self, project):
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame",
                           references=[], receipt=False)
        with pytest.raises(CheckpointValidationError, match="no generation receipt"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board]}})


# ---------------------------------------------------------------------------
# Codex cross-inspection fixes (#6–#14)
# ---------------------------------------------------------------------------

def _board_ref(project_dir, board: dict) -> dict:
    from lib.pathsafe import sha256_file

    return {"asset_id": sha256_file(project_dir / board["path"]), "path": board["path"],
            "role": "storyboard", "shot_id": board["shot_id"]}


def _two_shot_plan() -> dict:
    plan = scene_plan_v11()
    plan["scenes"][0]["shots"].append({"shot_id": "shot-2", "description": "Reverse."})
    return plan


class TestStoryboardApprovalPrecedesSpend:
    def test_candidate_take_without_storyboard_receipt_is_rejected(self, project):
        """#6: approval must precede spend — a candidate (not just a selected take)
        with no storyboard receipt is a violation."""
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        refs = [hero_ref(bible), establishing_ref(bible)]
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        cand = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs,
                          usage_status="candidate")
        with pytest.raises(CheckpointValidationError, match="storyboard_batch approval receipt"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board, cand]}})
        approve_storyboards(project_dir, board)
        write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board, cand]}})

    def test_frames_swapped_between_shots_are_rejected(self, project):
        """#6: the receipt binds shot_id -> hash, not an unordered set of hashes."""
        pipeline_dir, project_dir = project
        bible = setup_through_visual_bible(pipeline_dir, project_dir)
        write(pipeline_dir, "scene_plan", {"scene_plan": _two_shot_plan()})
        refs = [hero_ref(bible), establishing_ref(bible)]
        b1 = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs, shot_id="shot-1")
        b2 = shot_asset(project_dir, "board-2", asset_class="storyboard_frame", references=refs, shot_id="shot-2")
        approve_storyboards(project_dir, b1, b2)
        t1 = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs, shot_id="shot-1")
        t2 = shot_asset(project_dir, "take-2", asset_class="shot_visual", references=refs, shot_id="shot-2")
        write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [b1, b2, t1, t2]}})
        b1["shot_id"], b2["shot_id"] = "shot-2", "shot-1"
        with pytest.raises(CheckpointValidationError, match="storyboard_batch approval receipt"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [b1, b2, t1, t2]}})

    def test_storyboard_record_rejects_malformed_entries(self):
        with pytest.raises(ValueError):
            storyboard_batch_record({"shot-1": "short"})
        assert storyboard_batch_record({"s": "a" * 64}) == {"storyboard_frames": {"s": "a" * 64}}


class TestStoryboardReference:
    def test_take_may_cite_its_own_storyboard_frame(self, project):
        """#7: the storyboard reference variant validates and passes enforcement."""
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        refs = [hero_ref(bible), establishing_ref(bible)]
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        approve_storyboards(project_dir, board)
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual",
                          references=refs + [_board_ref(project_dir, board)])
        manifest = {"version": "1.1", "assets": [board, take]}
        validate_artifact("asset_manifest", manifest)
        write(pipeline_dir, "assets", {"asset_manifest": manifest})

    def test_storyboard_reference_schema_variants(self, project):
        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=[hero_ref(bible)])
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=[hero_ref(bible)])
        bad = _board_ref(project_dir, board)
        bad["visual_bible_entity_id"] = CHAR  # both discriminators at once
        take["continuity"]["references_applied"].append(bad)
        with pytest.raises(Exception):
            validate_artifact("asset_manifest", {"version": "1.1", "assets": [board, take]})
        entity_as_storyboard = dict(hero_ref(bible), role="storyboard")  # entity ref may not use the reserved role
        take["continuity"]["references_applied"] = [entity_as_storyboard]
        with pytest.raises(Exception):
            validate_artifact("asset_manifest", {"version": "1.1", "assets": [board, take]})

    def test_storyboard_reference_must_match_the_shots_frame(self, project):
        pipeline_dir, project_dir = project
        bible = setup_through_visual_bible(pipeline_dir, project_dir)
        write(pipeline_dir, "scene_plan", {"scene_plan": _two_shot_plan()})
        refs = [hero_ref(bible), establishing_ref(bible)]
        b1 = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs, shot_id="shot-1")
        b2 = shot_asset(project_dir, "board-2", asset_class="storyboard_frame", references=refs, shot_id="shot-2")
        approve_storyboards(project_dir, b1, b2)
        # cites the other shot's frame
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual",
                          references=refs + [_board_ref(project_dir, b2)], shot_id="shot-1")
        with pytest.raises(CheckpointValidationError, match="only on its own shot's frame"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [b1, b2, take]}})
        # right shot_id, wrong content hash
        wrong = dict(_board_ref(project_dir, b1), asset_id="f" * 64)
        take["continuity"]["references_applied"] = refs + [wrong]
        with pytest.raises(CheckpointValidationError, match="not the storyboard_frame recorded"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [b1, b2, take]}})
        # storyboard reference does not count as entity coverage
        take["continuity"]["references_applied"] = [_board_ref(project_dir, b1)]
        with pytest.raises(CheckpointValidationError, match="missing approved sheets"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [b1, b2, take]}})


class TestGenerationReceiptForgery:
    def test_fabricated_generation_row_does_not_admit_an_import(self, project):
        """#8: a hand-written generation-receipts.jsonl row is invisible."""
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame",
                           references=[], receipt=False)
        from lib.pathsafe import sha256_file

        sha = sha256_file(project_dir / board["path"])
        genuine = receipts.verified_generation_receipts(project_dir)[0]
        forged = dict(genuine, receipt_id="forged-gen", output_sha256=sha)
        forged["signature"] = gates_sign(forged)
        append_jsonl(receipts.generation_receipts_path(project_dir), forged)
        assert receipts.find_generation(project_dir, sha) is None
        with pytest.raises(CheckpointValidationError, match="no generation receipt"):
            write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board]}})

    def test_fabricated_row_does_not_make_an_imported_hero_canon(self, project):
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, locations=[LOC])
        entry = character_entry(project_dir, CHAR, approve_entry=False)
        imported = fake_image(project_dir, "imported-portrait", receipt=False, role="hero")
        genuine = receipts.verified_generation_receipts(project_dir)[0]
        forged = dict(genuine, receipt_id="forged-hero", output_sha256=imported["asset_id"])
        append_jsonl(receipts.generation_receipts_path(project_dir), forged)  # signature of another row
        imported["provenance"]["generation_receipt_id"] = "forged-hero"
        entry["hero"] = imported
        bible["characters"].append(entry)
        with pytest.raises(CheckpointValidationError, match="no generation receipt"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})


def gates_sign(receipt: dict) -> str:
    from lib import gates

    return gates.sign_receipt(receipt)


class TestMigratedArtifactReview:
    def _review(self, project_dir, name: str, artifact: dict) -> dict:
        from lib.canon_enforcement import artifact_review_digest
        from tests.lib.test_authored_film_contract import mint

        payload = {"artifact_type": name, "artifact_version": artifact["version"],
                   "artifact_digest": artifact_review_digest(artifact), "migration_status": "reviewed"}
        token = mint("p", "scene_plan", f"artifact_review:{name}", payload)
        return receipts.record_human_approval(project_dir, "p", "scene_plan", f"artifact_review:{name}",
                                              payload, token, "artifact_review", artifact=payload)

    def test_migrated_scene_plan_needs_review_receipt_then_flip(self, project):
        """#10: migration_status alone is self-attested; a receipt bound to the
        artifact digest is required, then the director flips to ok."""
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        plan = scene_plan_v11()
        plan["migration_status"] = "ok"  # flipped by hand, never reviewed
        with pytest.raises(CheckpointValidationError, match="no verified artifact_review receipt"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        plan["migration_status"] = "needs_review"
        with pytest.raises(CheckpointValidationError, match="no verified artifact_review receipt"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        self._review(project_dir, "scene_plan", plan)
        with pytest.raises(CheckpointValidationError, match="flips it to 'ok'"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        plan["migration_status"] = "ok"
        write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        # the receipt binds the content: an edit after review invalidates it
        plan["scenes"][0]["description"] += " — edited after review"
        with pytest.raises(CheckpointValidationError, match="no verified artifact_review receipt"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})

    def test_awaiting_human_is_still_writable_for_needs_review(self, project):
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        plan = scene_plan_v11()
        plan["migration_status"] = "needs_review"
        write(pipeline_dir, "scene_plan", {"scene_plan": plan}, status="awaiting_human")


class TestVisualBibleCompletionTightening:
    def test_extra_approved_entity_outside_cast_is_rejected(self, project):
        """#12: exact set equality with proposal_packet.cast."""
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir, cast_characters=(CHAR,))
        bible = approved_visual_bible(project_dir, characters=[CHAR, CHAR2], locations=[LOC])
        with pytest.raises(CheckpointValidationError, match=f"approved entries \\['{CHAR2}'\\] are not"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_cast_id_missing_from_canon_is_rejected(self, project):
        pipeline_dir, project_dir = project
        ghost = "char-09-00000000"
        setup_through_proposal(pipeline_dir, cast_characters=(ghost,))
        bible = approved_visual_bible(project_dir, characters=[ghost], locations=[LOC])
        with pytest.raises(CheckpointValidationError, match=f"{ghost}.*neither canon_packet"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_poster_is_required(self, project):
        """#11: no poster, no completed visual bible."""
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC], poster=False)
        with pytest.raises(Exception, match="poster"):
            validate_artifact("visual_bible", bible)
        with pytest.raises(CheckpointValidationError, match="poster"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        bible["poster"]["status"] = "draft"
        bible["poster"].pop("approval_receipt_id")
        with pytest.raises(CheckpointValidationError, match="approved poster"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_manifest_declares_poster_tools(self):
        import yaml

        manifest = yaml.safe_load(open("pipeline_defs/authored-film.yaml"))
        stage = next(s for s in manifest["stages"] if s["name"] == "visual_bible")
        assert {"seedream_image", "title_card", "poster_composite"} <= set(stage["tools_available"])

    def test_egress_must_cover_reference_images(self, project):
        """#14: consenting to prompts only is not consent to upload reference images."""
        pipeline_dir, project_dir = project
        setup_through_proposal(pipeline_dir)
        prompts_only = dict(PROJECT_CONFIG, provider_egress={"provider": "fal", "content_classes": ["prompts"]})
        digest = write_project_config(project_dir, prompts_only)
        bible = approved_visual_bible(project_dir, characters=[CHAR], locations=[LOC])
        log = plain_decision_log()
        log["decisions"] = [approval_policy_decision(digest)]
        with pytest.raises(CheckpointValidationError, match="reference_images"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible, "decision_log": log})


class TestDefaultEndpointFromConfig:
    def test_scene_off_default_needs_override_even_when_all_scenes_agree(self, project):
        """#13: the default is project.yaml default_video_endpoint, never inferred."""
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        plan = scene_plan_v11(endpoint="fake/other-video")
        with pytest.raises(CheckpointValidationError, match="default_video_endpoint"):
            write(pipeline_dir, "scene_plan", {"scene_plan": plan})
        plan["scenes"][0]["model_override"] = {"endpoint": "fake/other-video",
                                              "reason": "Still-frame insert; default cannot hold a static plate."}
        write(pipeline_dir, "scene_plan", {"scene_plan": plan})

    def test_config_binding_uses_verified_loader(self, project, monkeypatch):
        """One verification path: enforcement goes through lib.project_config."""
        pipeline_dir, project_dir = project
        setup_through_visual_bible(pipeline_dir, project_dir)
        import lib.project_config as pc
        from lib.canon_enforcement import _require_config_binding

        calls = []
        real = pc.load_verified_project_config
        monkeypatch.setattr(pc, "load_verified_project_config", lambda root: calls.append(root) or real(root))
        cfg = _require_config_binding(project_dir, plain_decision_log()["decisions"], "scene_plan")
        assert calls == [project_dir]
        assert cfg.default_video_endpoint == VIDEO_ENDPOINT

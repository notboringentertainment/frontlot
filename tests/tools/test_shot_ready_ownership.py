"""C3 / T7 — pre-upload reference ownership under a shot-ready pin.

Until 1.6 a reference with a receipted generation lineage could be uploaded
and paid for even though the visual-bible entry that owns it was never
approved; only the asset_manifest write refused it, long after the spend.
Under a shot-ready pin (authored-film 1.6+) ``bind_reference_manifest``
refuses it first — before any upload and before any reservation. Older pins
and calls without a ``reference_manifest`` are untouched.

All network is mocked. Real signed receipts (migration, look_lock,
storyboard_batch, generation) against a per-test gates dir. Invented names
only.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.pipeline_loader import manifest_digest
from lib.pipeline_pin import checkpoint_digests_on_disk, migration_record
from lib.look_spec import look_hash as _look_hash
from tools.graphics.seedream_image import SeedreamImage
from tools.video import _shared
from tools.video.kling_reference_video import KlingReferenceVideo

from tests.lib.look_lock_helpers import character_look
from tests.tools._authored_film_helpers import write_receipted_png, write_scene_plan_checkpoint
from tests.tools.test_kling_reference_video import (  # noqa: F401  (env fixture)
    _happy_path,
    _inputs,
    _shot_setup,
    env,
)

ENTITY = "char-01-aaaaaaaa"  # the id tests.tools.test_kling_reference_video packs


def _sign(project: Path, stage: str, scope: str, record: dict, kind: str, entity_id: str, envelope=None) -> dict:
    with gates.handler_context():
        token = gates.mint_gate_token(project.name, stage, scope, record_sha256(record), user_response="approve")
    return receipts.record_human_approval(
        project, project.name, stage, scope, record, token, kind, entity_id=entity_id, envelope=envelope,
    )


def pin(project: Path, version: str) -> None:
    record = migration_record(
        "authored-film", version, manifest_digest(f"authored-film@{version}"), checkpoint_digests_on_disk(project),
    )
    _sign(project, "pipeline", "pipeline:authored-film", record, "pipeline_migration", "authored-film",
          {"supersedes_receipt_id": None})


def activate_look(project: Path, payload: dict) -> dict:
    return _sign(
        project, "look_lock", f"{payload['entity_kind']}:{payload['entity_id']}", payload, "look_lock",
        payload["entity_id"],
        {"action": "activate", "entity_kind": payload["entity_kind"], "look_hash": _look_hash(payload),
         "supersedes_look_hash": None, "promotion_refs": [], "source_ticket_ref": None},
    )


def write_bible(project: Path, *, entity_id: str, hero: dict, status: str) -> None:
    """The on-disk visual_bible checkpoint — PARTIAL, as it is while shots are
    made — carrying one entry that owns ``hero`` with the given status."""
    (project / "checkpoint_visual_bible.json").write_text(
        json.dumps({
            "version": "1.0", "project_id": project.name, "stage": "visual_bible",
            "status": "in_progress", "pipeline_type": "authored-film",
            "artifacts": {"visual_bible": {
                "version": "1.1", "project_slug": project.name,
                "characters": [{
                    "id": entity_id, "status": status,
                    "hero": {"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png", "role": "hero"},
                }],
                "locations": [],
            }},
        }, indent=2),
        encoding="utf-8",
    )


@pytest.fixture
def shot(env):
    """A 1.6-pinned project with an active signed look and one shot take whose
    reference is a receipted hero image. The bible entry's status is what each
    test varies.

    The scene plan grants the shot room for this take: under 1.6 the per-shot
    allowance (C2) is checked before the ownership check, so a shot with no
    approved allowance never reaches the reference binding at all.
    """
    inputs, expected, hero, frame = _shot_setup(env)
    look = character_look(ENTITY)
    activate_look(env, look)
    pin(env, "1.6")
    write_scene_plan_checkpoint(
        env,
        [{"shot_id": "sh-1", "description": "Hall, slow push in.",
          "spend_allowance_usd": 10.0, "max_video_takes": 3}],
        character_refs=(ENTITY,),
    )
    inputs["look_refs"] = [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}]
    return {"project": env, "inputs": inputs, "expected": expected, "hero": hero, "frame": frame}


def _run(project, inputs):
    """Execute the take with the upload and reservation boundaries watched."""
    with patch.object(_shared, "upload_image_fal") as up, \
         patch("tools.cost_tracker.reserve_paid_call") as reserve, \
         patch.object(_shared, "fal_queue_submit") as submit:
        result = KlingReferenceVideo().execute(_inputs(project, **inputs))
    return result, up, reserve, submit


class TestOwnershipAtTheUploadBoundary:
    def test_a_draft_entry_cannot_own_a_shot_reference(self, shot):
        write_bible(shot["project"], entity_id=ENTITY, hero=shot["hero"], status="draft")
        result, up, reserve, submit = _run(shot["project"], shot["inputs"])
        assert not result.success
        assert "approved visual-bible entry" in result.error and ENTITY in result.error
        up.assert_not_called()
        reserve.assert_not_called()
        submit.assert_not_called()
        assert not (shot["project"] / "cost-reservations.jsonl").exists()

    def test_a_missing_bible_entry_cannot_own_a_shot_reference(self, shot):
        result, up, reserve, submit = _run(shot["project"], shot["inputs"])
        assert not result.success and "approved visual-bible entry" in result.error
        up.assert_not_called()
        reserve.assert_not_called()

    def test_an_entry_owning_another_asset_does_not_cover_this_one(self, shot):
        other = write_receipted_png(shot["project"], "canon/visual/objects/other.png")
        write_bible(shot["project"], entity_id=ENTITY, hero=other, status="approved")
        result, up, reserve, _ = _run(shot["project"], shot["inputs"])
        assert not result.success and "approved visual-bible entry" in result.error
        up.assert_not_called()
        reserve.assert_not_called()

    def test_the_same_reference_proceeds_once_the_entry_is_approved(self, shot):
        write_bible(shot["project"], entity_id=ENTITY, hero=shot["hero"], status="approved")
        uploads = []
        wait, dl, verify = _happy_path()
        with patch.object(_shared, "upload_image_fal",
                          side_effect=lambda p: uploads.append(Path(p)) or f"https://v3.fal.media/u{len(uploads)}.png"), \
             patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-ok"}), wait, dl, verify:
            result = KlingReferenceVideo().execute(_inputs(shot["project"], **shot["inputs"]))
        assert result.success, result.error
        assert uploads == [shot["hero"]["path"].resolve(), shot["frame"]["path"].resolve()]
        assert result.metadata["references_applied"] == shot["expected"]

    def test_the_tool_packed_storyboard_frame_is_the_only_exemption(self, shot):
        """The frame is re-hashed and packed by the tool AFTER binding, so it
        never needs a bible entry; a caller-supplied storyboard entry is still
        rejected, exactly as before 1.6."""
        write_bible(shot["project"], entity_id=ENTITY, hero=shot["hero"], status="approved")
        smuggled = dict(shot["inputs"])
        smuggled["reference_manifest"] = list(smuggled["reference_manifest"]) + [{
            "asset_id": shot["frame"]["sha256"], "path": "assets/storyboards/sh-1.png",
            "role": "storyboard", "shot_id": "sh-1",
        }]
        smuggled["reference_image_paths"] = list(smuggled["reference_image_paths"]) + [str(shot["frame"]["path"])]
        result, up, reserve, _ = _run(shot["project"], smuggled)
        assert not result.success and "storyboard reference" in result.error
        up.assert_not_called()
        reserve.assert_not_called()


class TestUntouchedBoundaries:
    def test_a_1_5_pinned_project_is_unchanged(self, env):
        inputs, expected, hero, frame = _shot_setup(env)
        look = character_look(ENTITY)
        activate_look(env, look)
        pin(env, "1.5")
        inputs["look_refs"] = [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}]
        write_bible(env, entity_id=ENTITY, hero=hero, status="draft")  # would refuse under 1.6
        wait, dl, verify = _happy_path()
        with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
             patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-15"}), wait, dl, verify:
            result = KlingReferenceVideo().execute(_inputs(env, **inputs))
        assert result.success, result.error

    def test_a_manifest_less_call_is_unchanged(self, env):
        """No reference_image_paths and no reference_manifest: nothing to own."""
        pin(env, "1.6")
        assert _shared.bind_reference_manifest({}, env, []) == []

    def test_binding_without_a_pin_never_consults_the_bible(self, env, tmp_path):
        hero = write_receipted_png(env, "canon/visual/objects/solo.png")
        manifest = [{"asset_id": hero["sha256"], "path": "canon/visual/objects/solo.png", "role": "hero",
                     "visual_bible_entity_id": ENTITY}]
        applied = _shared.bind_reference_manifest(
            {"reference_manifest": manifest}, env, [hero["path"]],
        )
        assert applied == manifest


class TestSeedreamSharesTheCheck:
    def test_seedream_refuses_an_unowned_reference_before_upload(self, env):
        hero = write_receipted_png(env, "canon/visual/objects/hero.png")
        look = character_look(ENTITY)
        activate_look(env, look)
        pin(env, "1.6")
        write_bible(env, entity_id=ENTITY, hero=hero, status="draft")
        from tools import prompt_builder as pb

        built = pb.build_prompt(look, role="hero", palette=["moss", "brass"])
        inputs = {
            "prompt": built["prompt"],
            "prompt_recipe": built["prompt_recipe"],
            "project_dir": str(env),
            "operation": "edit",
            "asset_role": "hero",
            "palette": ["moss", "brass"],
            "look_refs": [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}],
            "reference_image_paths": [str(hero["path"])],
            "reference_manifest": [{"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png",
                                    "role": "hero", "visual_bible_entity_id": ENTITY}],
        }
        with patch.object(_shared, "upload_image_fal") as up, \
             patch("tools.cost_tracker.reserve_paid_call") as reserve, \
             patch.object(_shared, "fal_queue_submit") as submit:
            result = SeedreamImage().execute(inputs)
        assert not result.success and "approved visual-bible entry" in result.error
        up.assert_not_called()
        reserve.assert_not_called()
        submit.assert_not_called()

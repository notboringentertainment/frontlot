"""Schema contracts added by plan D10: look_packet, headshot_packet, visual_bible 1.1,
checkpoint tuple/predecessors, look_spec strictness."""

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from lib.checkpoint import CheckpointValidationError, validate_checkpoint
from lib.pipeline_loader import manifest_digest
from schemas.artifacts import ARTIFACT_NAMES, ArtifactValidationError, validate_artifact

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
IMG = {"asset_id": SHA, "path": "canon/visual/objects/x.png", "role": "hero",
       "provenance": {"generator_kind": "model", "model_endpoint": "m", "prompt": "p", "generation_receipt_id": "g"}}
IMPORTED = {"asset_id": SHA, "path": "canon/visual/objects/x.png", "role": "hero",
            "provenance": {"generator_kind": "imported", "origin_tool": "t", "attestation_receipt_id": "a",
                           "import_receipt_id": "g", "normalized_pixel_hash": SHA, "generation_receipt_id": "g"}}
LOOK_REF = {"entity_kind": "character", "entity_id": "c1", "look_hash": SHA, "receipt_id": "r"}
RECIPE = {"look_hash": SHA, "builder_version": "b/1", "fields_used": ["hair"], "rendered_sha256": SHA}


def test_new_artifacts_are_registered():
    assert {"look_packet", "headshot_packet"} <= set(ARTIFACT_NAMES)


def test_look_spec_schema_has_no_status_hash_or_supersedes():
    s = json.loads((ROOT / "schemas/look_spec.schema.json").read_text())
    for kind in ("character", "location"):
        props = s["$defs"][kind]["properties"]
        assert not {"status", "source_hash", "look_hash", "supersedes"} & set(props)
        assert s["$defs"][kind]["additionalProperties"] is False


def test_look_packet_unique_keys():
    entry = {"entity_kind": "character", "entity_id": "c1", "look_spec": {}, "look_hash": SHA,
             "receipt_id": "r", "source_ticket_ref": {"id": "wf-0badc0de"}}
    validate_artifact("look_packet", {"version": "1.0", "looks": [entry, dict(entry, entity_kind="location")]})
    with pytest.raises(ArtifactValidationError, match="duplicate look key"):
        validate_artifact("look_packet", {"version": "1.0", "looks": [entry, dict(entry)]})
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("look_packet", {"version": "1.0", "looks": [dict(entry, source_ticket_ref={"path": "x"})]})


class TestHeadshotPacket:
    def test_pending_and_approved_variants(self):
        pending = {"version": "1.0", "state": "pending", "characters": [
            {"entity_kind": "character", "entity_id": "c1", "look_ref": LOOK_REF, "prompt_recipe": RECIPE, "candidates": [IMG, IMPORTED]}]}
        validate_artifact("headshot_packet", pending)
        approved = {"version": "1.0", "state": "approved", "characters": [
            {"entity_kind": "character", "entity_id": "c1", "look_ref": LOOK_REF, "hero": IMPORTED, "origin": "imported_synthetic",
             "import_receipt_id": "g", "normalized_pixel_hash": SHA, "approval_receipt_id": "h", "candidates_rejected": []}]}
        validate_artifact("headshot_packet", approved)

    def test_state_discriminates(self):
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("headshot_packet", {"version": "1.0", "state": "approved", "characters": [
                {"entity_kind": "character", "entity_id": "c1", "look_ref": LOOK_REF, "candidates": [IMG]}]})
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("headshot_packet", {"version": "1.0", "state": "pending", "characters": [
                {"entity_kind": "character", "entity_id": "c1", "look_ref": LOOK_REF, "candidates": [IMG] * 5}]})
        with pytest.raises(jsonschema.ValidationError):  # imported origin needs its receipt
            validate_artifact("headshot_packet", {"version": "1.0", "state": "approved", "characters": [
                {"entity_kind": "character", "entity_id": "c1", "look_ref": LOOK_REF, "hero": IMG, "origin": "imported_synthetic",
                 "normalized_pixel_hash": SHA, "approval_receipt_id": "h", "candidates_rejected": []}]})
        with pytest.raises(ArtifactValidationError, match="duplicate entity_id"):
            e = {"entity_kind": "character", "entity_id": "c1", "look_ref": LOOK_REF, "candidates": [IMG]}
            validate_artifact("headshot_packet", {"version": "1.0", "state": "pending", "characters": [e, dict(e)]})


class TestVisualBible11:
    def _bible(self, version):
        roles = ("turnaround", "expressions", "wardrobe") if version == "1.1" else (
            "front", "three_quarter", "profile", "full_body", "expressions", "wardrobe")
        ch = {"id": "c1", "hero": IMG, "sheet": {r: IMG for r in roles},
              "wardrobe_negative": "", "status": "draft"}
        if version == "1.1":
            ch.update({"prompt_recipe": RECIPE, "look_ref": LOOK_REF, "sheet_revision": 1})
            loc = {"id": "l1", "establishing": IMG, "angles": [IMG, IMG], "status": "draft",
                   "look_ref": dict(LOOK_REF, entity_kind="location", entity_id="l1"), "sheet_revision": 1}
        else:
            ch["approved_prompt_block"] = "verbatim"
            loc = {"id": "l1", "establishing": IMG, "angles": [IMG, IMG], "status": "draft"}
        return {"version": version, "project_slug": "s", "palette": {"hues": ["a", "b", "c"]},
                "generator_defaults": {"image_model": "m", "edit_model": "e"}, "characters": [ch], "locations": [loc],
                "poster": {"key_art": IMG, "title_card": IMG, "poster_final": IMG, "status": "draft"}}

    def test_both_versions_validate(self):
        validate_artifact("visual_bible", self._bible("1.0"))
        validate_artifact("visual_bible", self._bible("1.1"))

    def test_1_1_replaces_prompt_block_with_recipe(self):
        b = self._bible("1.1")
        b["characters"][0]["approved_prompt_block"] = "verbatim"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", b)
        b = self._bible("1.1")
        del b["characters"][0]["look_ref"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", b)
        b = self._bible("1.0")
        b["characters"][0]["look_ref"] = LOOK_REF
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", b)

    def test_imported_provenance_branch(self):
        b = self._bible("1.1")
        b["characters"][0]["hero"] = IMPORTED
        validate_artifact("visual_bible", b)
        bad = copy.deepcopy(IMPORTED)
        del bad["provenance"]["attestation_receipt_id"]
        b["characters"][0]["hero"] = bad
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", b)


class TestCheckpointSchema:
    def _cp(self, **extra):
        cp = {"version": "1.0", "project_id": "p", "pipeline_type": "authored-film", "stage": "proposal",
              "status": "in_progress", "timestamp": "2026-08-26T00:00:00+00:00", "artifacts": {}}
        cp.update(extra)
        return cp

    def test_legacy_checkpoint_without_tuple_validates(self):
        validate_checkpoint(self._cp())

    def test_1_2_tuple_requires_predecessors(self):
        tuple_ = {"name": "authored-film", "version": "1.2", "manifest_digest": manifest_digest("authored-film@1.2")}
        with pytest.raises(CheckpointValidationError, match="predecessors"):
            validate_checkpoint(self._cp(pipeline=tuple_))
        validate_checkpoint(self._cp(pipeline=tuple_, predecessors=[]))
        validate_checkpoint(self._cp(pipeline=tuple_, predecessors=[], stage="look_lock"))
        with pytest.raises(CheckpointValidationError, match="Invalid stage"):
            validate_checkpoint(self._cp(pipeline=dict(tuple_, version="1.1"), stage="look_lock"))

    def test_1_1_tuple_predecessors_optional(self):
        validate_checkpoint(self._cp(pipeline={"name": "authored-film", "version": "1.1", "manifest_digest": SHA}))

"""Slice 1: artifact schema versioning (1.0 / 1.1), visual_bible, project config,
and 1.0 -> 1.1 migrations. Invented names only."""

from __future__ import annotations

import copy
import json

import jsonschema
import pytest

from lib.migrations.artifacts import (
    MigrationError,
    upgrade_asset_manifest_1_0_to_1_1,
    upgrade_canon_packet_1_0_to_1_1,
    upgrade_proposal_packet_1_0_to_1_1,
    upgrade_scene_plan_1_0_to_1_1,
)
from schemas.artifacts import (
    ARTIFACT_NAMES,
    SCHEMA_DIR,
    ArtifactValidationError,
    ArtifactVersionError,
    artifact_version,
    load_project_config_schema,
    load_schema,
    validate_artifact,
    validate_project_config,
)

SHA = "a" * 64
SHA_B = "b" * 64
VERSIONED = ["canon_packet", "proposal_packet", "scene_plan", "asset_manifest"]


# --------------------------------------------------------------------------
# 1.0 fixtures
# --------------------------------------------------------------------------

def canon_1_0() -> dict:
    return {
        "version": "1.0",
        "project_title": "The Lantern Ferry",
        "format": "short",
        "source_documents": [
            {"path": "docs/synopsis.md", "doc_type": "synopsis", "authority": "writing_document", "sha256": SHA}
        ],
        "locks": [],
        "characters": [
            {"name": "Orla Penhallow", "one_line_identity": "A ferry keeper who refuses to sell the crossing"},
            {"name": "Tobin Reyes", "one_line_identity": "A surveyor sent to price the river"},
        ],
        "locations": [
            {"name": "The Ferry Landing", "visual_identity": "Weathered planks, brass lanterns"},
        ],
        "tone": {"tone_words": ["quiet"]},
        "structure": {"beats": [{"id": "b1", "summary": "Orla meets Tobin at the landing"}]},
        "open_questions": [],
    }


def proposal_1_0() -> dict:
    concept = {
        "id": "c1",
        "title": "The crossing",
        "hook": "Nobody sells a river.",
        "narrative_structure": "story",
        "visual_approach": "Lantern-lit river at dusk",
        "target_duration_seconds": 60,
        "why_this_works": "Canon beats map to it",
    }
    return {
        "version": "1.0",
        "concept_options": [concept, {**concept, "id": "c2"}, {**concept, "id": "c3"}],
        "selected_concept": {"concept_id": "c1", "rationale": "writer choice"},
        "production_plan": {
            "pipeline": "authored-film",
            "stages": [],
            "render_runtime": "ffmpeg",
        },
        "cost_estimate": {"total_estimated_usd": 1.0, "line_items": [], "budget_verdict": "within_budget"},
        "approval": {"status": "pending"},
    }


def scene_plan_1_0() -> dict:
    return {
        "version": "1.0",
        "scenes": [
            {"id": "s1", "type": "generated", "description": "Orla lights the lanterns", "start_seconds": 0, "end_seconds": 5, "canon_refs": ["b1"]},
        ],
    }


def asset_manifest_1_0() -> dict:
    return {
        "version": "1.0",
        "assets": [
            {
                "id": "a1", "type": "image", "path": "assets/a1.png", "source_tool": "img", "scene_id": "s1",
                "model": "some/endpoint",
                "continuity": {"canon_refs": ["b1"], "references_applied": ["refs/orla.png"], "risk_notes_applied": []},
            },
            {"id": "a2", "type": "music", "path": "assets/a2.mp3", "source_tool": "music", "scene_id": "s1"},
        ],
    }


FIXTURES_1_0 = {
    "canon_packet": canon_1_0,
    "proposal_packet": proposal_1_0,
    "scene_plan": scene_plan_1_0,
    "asset_manifest": asset_manifest_1_0,
}


# --------------------------------------------------------------------------
# 1.1 fixtures
# --------------------------------------------------------------------------

def canon_1_1() -> dict:
    p = canon_1_0()
    p["version"] = "1.1"
    p["characters"][0]["id"] = "orla"
    p["characters"][1]["id"] = "tobin"
    p["locations"][0]["id"] = "ferry-landing"
    return p


def proposal_1_1() -> dict:
    p = proposal_1_0()
    p["version"] = "1.1"
    p["runtime_shape"] = {"format": "trailer"}
    p["cast"] = {"character_ids": ["orla"], "location_ids": ["ferry-landing"]}
    return p


def scene_plan_1_1() -> dict:
    p = scene_plan_1_0()
    p["version"] = "1.1"
    p["scenes"][0].update({
        "character_refs": ["orla"],
        "location_ref": "ferry-landing",
        "entity_free": False,
        "model_endpoint": "vendor/model-a",
        "shots": [{"shot_id": "s1-sh1", "description": "Match strikes"}, {"shot_id": "s1-sh2", "description": "Lantern catches"}],
    })
    return p


def ref_obj(entity: str = "orla") -> dict:
    return {"asset_id": SHA, "path": "objects/aa/hero.png", "role": "hero", "visual_bible_entity_id": entity}


def asset_manifest_1_1() -> dict:
    return {
        "version": "1.1",
        "assets": [
            {
                "id": "a1", "type": "video", "path": "assets/a1.mp4", "source_tool": "vid", "scene_id": "s1",
                "asset_class": "shot_visual", "shot_id": "s1-sh1", "take_id": "t1", "usage_status": "candidate",
                "model_endpoint": "vendor/model-a",
                "continuity": {"canon_refs": ["b1"], "references_applied": [ref_obj()], "risk_notes_applied": []},
            },
            {
                "id": "a2", "type": "image", "path": "assets/a2.png", "source_tool": "img", "scene_id": "s1",
                "asset_class": "storyboard_frame", "shot_id": "s1-sh1",
                "continuity": {"canon_refs": ["b1"], "references_applied": [ref_obj()], "risk_notes_applied": []},
            },
            {"id": "a3", "type": "music", "path": "assets/a3.mp3", "source_tool": "music", "scene_id": "s1", "asset_class": "non_shot"},
        ],
    }


FIXTURES_1_1 = {
    "canon_packet": canon_1_1,
    "proposal_packet": proposal_1_1,
    "scene_plan": scene_plan_1_1,
    "asset_manifest": asset_manifest_1_1,
}


def image_ref(kind: str = "model") -> dict:
    prov = (
        {"generator_kind": "model", "model_endpoint": "vendor/image-a", "prompt": "hero portrait", "seed": 7, "generation_receipt_id": "gr-1"}
        if kind == "model"
        else {"generator_kind": "local", "tool": "compose_title", "tool_version": "1.0", "parameters_hash": SHA_B, "input_asset_ids": [SHA], "generation_receipt_id": "gr-2"}
    )
    return {"asset_id": SHA, "path": "objects/aa/x.png", "role": "hero", "provenance": prov}


def visual_bible() -> dict:
    sheet = {k: image_ref() for k in ["front", "three_quarter", "profile", "full_body", "expressions", "wardrobe"]}
    return {
        "version": "1.0",
        "project_slug": "lantern-ferry",
        "palette": {"hues": ["#0a1a2f", "#d9a441", "#f2efe6"], "notes": "dusk brass"},
        "generator_defaults": {"image_model": "vendor/image-a", "edit_model": "vendor/edit-a"},
        "characters": [
            {
                "id": "orla", "hero": image_ref(), "sheet": sheet,
                "wardrobe_negative": "no modern fabrics",
                "approved_prompt_block": "Orla, weathered ferry keeper, oilskin coat",
                "status": "approved", "approval_receipt_id": "ar-1",
            }
        ],
        "locations": [
            {"id": "ferry-landing", "establishing": image_ref(), "angles": [image_ref(), image_ref()], "status": "draft"}
        ],
        "poster": {
            "key_art": image_ref(), "title_card": image_ref("local"), "poster_final": image_ref("local"),
            "status": "draft",
        },
    }


def project_config() -> dict:
    return {
        "version": "1.0",
        "budget_usd_cap": 25.0,
        "wall_time_minutes": 90,
        "cast_cap": {"characters": 4, "locations": 3},
        "provider_egress": {"provider": "fal", "content_classes": ["prompts", "reference_images"]},
        "default_video_endpoint": "fake/reference-to-video",
    }


# --------------------------------------------------------------------------
# Versioned schemas: acceptance and dispatch
# --------------------------------------------------------------------------

class TestVersionDispatch:
    @pytest.mark.parametrize("name", VERSIONED)
    def test_1_0_still_validates(self, name):
        validate_artifact(name, FIXTURES_1_0[name]())
        assert artifact_version(FIXTURES_1_0[name]()) == "1.0"

    @pytest.mark.parametrize("name", VERSIONED)
    def test_1_1_validates(self, name):
        validate_artifact(name, FIXTURES_1_1[name]())
        assert artifact_version(FIXTURES_1_1[name]()) == "1.1"

    @pytest.mark.parametrize("name", VERSIONED)
    def test_unknown_version_names_version_and_artifact(self, name):
        data = FIXTURES_1_0[name]()
        data["version"] = "2.0"
        with pytest.raises(ArtifactVersionError) as exc:
            validate_artifact(name, data)
        msg = str(exc.value)
        assert "2.0" in msg and name in msg and "1.0" in msg and "1.1" in msg

    def test_missing_version_is_a_version_error(self):
        data = canon_1_0()
        del data["version"]
        with pytest.raises(ArtifactVersionError):
            validate_artifact("canon_packet", data)
        with pytest.raises(ArtifactVersionError):
            artifact_version(data)

    def test_version_errors_are_jsonschema_validation_errors(self):
        assert issubclass(ArtifactVersionError, jsonschema.ValidationError)
        assert issubclass(ArtifactValidationError, jsonschema.ValidationError)

    @pytest.mark.parametrize("name", VERSIONED)
    def test_1_0_body_rejects_1_1_fields(self, name):
        """The 1.0 branch is untouched: 1.1-only fields are still unknown to it."""
        data = FIXTURES_1_1[name]()
        data["version"] = "1.0"
        with pytest.raises(jsonschema.ValidationError) as exc:
            validate_artifact(name, data)
        assert "1.0" in str(exc.value)

    @pytest.mark.parametrize("name", VERSIONED)
    def test_1_1_body_rejects_bare_1_0_payload(self, name):
        data = FIXTURES_1_0[name]()
        data["version"] = "1.1"
        with pytest.raises(jsonschema.ValidationError) as exc:
            validate_artifact(name, data)
        assert "1.1" in str(exc.value)

    @pytest.mark.parametrize("name", VERSIONED)
    def test_schema_file_is_oneof_over_versions(self, name):
        schema = load_schema(name)
        branches = [b["$ref"].rsplit("/", 1)[-1] for b in schema["oneOf"]]
        assert branches == ["v1_0", "v1_1"]
        assert schema["$defs"]["v1_0"]["properties"]["version"]["const"] == "1.0"
        assert schema["$defs"]["v1_1"]["properties"]["version"]["const"] == "1.1"

    @pytest.mark.parametrize("name", VERSIONED)
    def test_hoisted_introspection_properties_do_not_drift(self, name):
        """Version-invariant subtrees hoisted to top-level `properties` (for
        consumers that introspect the file) must equal both branches."""
        schema = load_schema(name)
        # The root must be validation-neutral: no required / additionalProperties.
        assert "required" not in schema and "additionalProperties" not in schema
        hoisted = {
            "proposal_packet": [("production_plan",)],
            "asset_manifest": [("assets", "items", "properties", "voice_performance")],
        }.get(name, [])
        assert set(schema["properties"]) == {"version"} | {p[0] for p in hoisted}

        def walk(node, path):
            for part in path:
                node = node[part]
            return node

        for path in hoisted:
            root_sub = walk(schema["properties"], path)
            for branch in ("v1_0", "v1_1"):
                assert walk(schema["$defs"][branch]["properties"], path) == root_sub, (name, path, branch)

    def test_unversioned_schemas_still_validate_directly(self):
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("brief", {"version": "1.0"})


# --------------------------------------------------------------------------
# canon_packet 1.1
# --------------------------------------------------------------------------

class TestCanonPacket11:
    def test_missing_id_rejected(self):
        p = canon_1_1()
        del p["characters"][0]["id"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("canon_packet", p)

    @pytest.mark.parametrize("bad", ["Orla", "orla_p", "orla p", ""])
    def test_id_pattern(self, bad):
        p = canon_1_1()
        p["characters"][0]["id"] = bad
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("canon_packet", p)

    def test_duplicate_character_ids_rejected_in_python(self):
        p = canon_1_1()
        p["characters"][1]["id"] = "orla"
        with pytest.raises(ArtifactValidationError, match="orla"):
            validate_artifact("canon_packet", p)

    def test_duplicate_location_ids_rejected(self):
        p = canon_1_1()
        p["locations"].append({"id": "ferry-landing", "name": "Other"})
        with pytest.raises(ArtifactValidationError, match="ferry-landing"):
            validate_artifact("canon_packet", p)

    def test_same_id_across_arrays_is_allowed(self):
        p = canon_1_1()
        p["locations"][0]["id"] = "orla"
        validate_artifact("canon_packet", p)


# --------------------------------------------------------------------------
# proposal_packet 1.1
# --------------------------------------------------------------------------

class TestProposalPacket11:
    @pytest.mark.parametrize("missing", ["runtime_shape", "cast"])
    def test_required_fields(self, missing):
        p = proposal_1_1()
        del p[missing]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("proposal_packet", p)

    def test_format_enum(self):
        p = proposal_1_1()
        p["runtime_shape"]["format"] = "feature"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("proposal_packet", p)
        for ok in ("trailer", "teaser", "short"):
            p["runtime_shape"]["format"] = ok
            validate_artifact("proposal_packet", p)

    def test_cast_requires_both_lists(self):
        p = proposal_1_1()
        del p["cast"]["location_ids"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("proposal_packet", p)

    def test_migration_status_enum(self):
        p = proposal_1_1()
        p["migration_status"] = "needs_review"
        validate_artifact("proposal_packet", p)
        p["migration_status"] = "whatever"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("proposal_packet", p)


# --------------------------------------------------------------------------
# scene_plan 1.1
# --------------------------------------------------------------------------

class TestScenePlan11:
    @pytest.mark.parametrize("missing", ["character_refs", "location_ref", "model_endpoint", "shots"])
    def test_required_scene_fields(self, missing):
        p = scene_plan_1_1()
        del p["scenes"][0][missing]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("scene_plan", p)

    def test_entity_free_defaults_false_and_is_optional(self):
        p = scene_plan_1_1()
        del p["scenes"][0]["entity_free"]
        validate_artifact("scene_plan", p)
        schema = load_schema("scene_plan")
        assert schema["$defs"]["v1_1"]["properties"]["scenes"]["items"]["properties"]["entity_free"]["default"] is False

    def test_location_ref_may_be_null(self):
        p = scene_plan_1_1()
        p["scenes"][0]["location_ref"] = None
        validate_artifact("scene_plan", p)

    def test_model_override_requires_reason(self):
        p = scene_plan_1_1()
        p["scenes"][0]["model_override"] = {"endpoint": "vendor/model-b"}
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("scene_plan", p)
        p["scenes"][0]["model_override"]["reason"] = "needs longer takes"
        validate_artifact("scene_plan", p)

    def test_shot_requires_id_and_description(self):
        p = scene_plan_1_1()
        p["scenes"][0]["shots"] = [{"shot_id": "x"}]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("scene_plan", p)

    def test_duplicate_shot_ids_across_scenes_rejected(self):
        p = scene_plan_1_1()
        second = copy.deepcopy(p["scenes"][0])
        second["id"] = "s2"
        second["shots"] = [{"shot_id": "s1-sh1", "description": "dup"}]
        p["scenes"].append(second)
        with pytest.raises(ArtifactValidationError, match="s1-sh1"):
            validate_artifact("scene_plan", p)

    def test_migration_status_enum(self):
        p = scene_plan_1_1()
        p["migration_status"] = "ok"
        validate_artifact("scene_plan", p)
        p["migration_status"] = "later"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("scene_plan", p)


# --------------------------------------------------------------------------
# asset_manifest 1.1
# --------------------------------------------------------------------------

class TestAssetManifest11:
    def test_asset_class_required(self):
        m = asset_manifest_1_1()
        del m["assets"][2]["asset_class"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    def test_type_enum_kept(self):
        v10 = load_schema("asset_manifest")["$defs"]["v1_0"]["properties"]["assets"]["items"]["properties"]["type"]["enum"]
        v11 = load_schema("asset_manifest")["$defs"]["v1_1"]["properties"]["assets"]["items"]["properties"]["type"]["enum"]
        assert v10 == v11

    def test_storyboard_frame_must_be_image(self):
        m = asset_manifest_1_1()
        m["assets"][1]["type"] = "video"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    @pytest.mark.parametrize("missing", ["shot_id", "continuity"])
    def test_storyboard_frame_requirements(self, missing):
        m = asset_manifest_1_1()
        del m["assets"][1][missing]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    @pytest.mark.parametrize("missing", ["shot_id", "take_id", "usage_status", "model_endpoint", "continuity"])
    def test_shot_visual_requirements(self, missing):
        m = asset_manifest_1_1()
        del m["assets"][0][missing]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    def test_shot_visual_type_image_or_video_only(self):
        m = asset_manifest_1_1()
        m["assets"][0]["type"] = "audio"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)
        m["assets"][0]["type"] = "image"
        validate_artifact("asset_manifest", m)

    def test_usage_status_enum(self):
        m = asset_manifest_1_1()
        m["assets"][0]["usage_status"] = "final"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    @pytest.mark.parametrize("field,value", [("shot_id", "x"), ("take_id", "t"), ("model_endpoint", "vendor/m")])
    def test_non_shot_forbids_shot_fields(self, field, value):
        m = asset_manifest_1_1()
        m["assets"][2][field] = value
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    def test_references_applied_must_be_objects_in_1_1(self):
        m = asset_manifest_1_1()
        m["assets"][0]["continuity"]["references_applied"] = ["refs/orla.png"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    def test_reference_asset_id_must_be_64_hex(self):
        m = asset_manifest_1_1()
        m["assets"][0]["continuity"]["references_applied"][0]["asset_id"] = "abc"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)

    def test_continuity_reference_requires_all_fields(self):
        m = asset_manifest_1_1()
        del m["assets"][0]["continuity"]["references_applied"][0]["visual_bible_entity_id"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("asset_manifest", m)


# --------------------------------------------------------------------------
# visual_bible
# --------------------------------------------------------------------------

class TestVisualBible:
    def test_registered(self):
        assert "visual_bible" in ARTIFACT_NAMES
        assert (SCHEMA_DIR / "visual_bible.schema.json").exists()

    def test_valid(self):
        validate_artifact("visual_bible", visual_bible())
        assert artifact_version(visual_bible()) == "1.0"

    def test_version_const(self):
        vb = visual_bible()
        vb["version"] = "1.1"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_model_provenance_requires_receipt(self):
        vb = visual_bible()
        del vb["characters"][0]["hero"]["provenance"]["generation_receipt_id"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_local_provenance_requires_tool_fields(self):
        vb = visual_bible()
        del vb["poster"]["title_card"]["provenance"]["parameters_hash"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_provenance_kinds_do_not_mix(self):
        vb = visual_bible()
        vb["characters"][0]["hero"]["provenance"]["tool"] = "sneaky"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_asset_id_is_sha256(self):
        vb = visual_bible()
        vb["characters"][0]["hero"]["asset_id"] = "not-a-hash"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    @pytest.mark.parametrize("missing", ["approved_prompt_block", "wardrobe_negative", "sheet", "hero", "status"])
    def test_character_required_fields(self, missing):
        vb = visual_bible()
        del vb["characters"][0][missing]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_sheet_requires_every_view(self):
        vb = visual_bible()
        del vb["characters"][0]["sheet"]["profile"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_approved_requires_receipt_id(self):
        vb = visual_bible()
        del vb["characters"][0]["approval_receipt_id"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_superseded_requires_superseded_by(self):
        vb = visual_bible()
        vb["characters"][0]["status"] = "superseded"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)
        vb["characters"][0]["superseded_by"] = "orla-v2"
        validate_artifact("visual_bible", vb)

    def test_status_enum(self):
        vb = visual_bible()
        vb["locations"][0]["status"] = "pending"
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_location_angles_bounds(self):
        vb = visual_bible()
        vb["locations"][0]["angles"] = [image_ref()]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)
        vb["locations"][0]["angles"] = [image_ref()] * 4
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_palette_hue_bounds(self):
        vb = visual_bible()
        vb["palette"]["hues"] = ["#000"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)

    def test_duplicate_entity_ids_rejected(self):
        vb = visual_bible()
        vb["characters"].append(copy.deepcopy(vb["characters"][0]))
        with pytest.raises(ArtifactValidationError, match="orla"):
            validate_artifact("visual_bible", vb)

    def test_poster_required_parts(self):
        vb = visual_bible()
        del vb["poster"]["poster_final"]
        with pytest.raises(jsonschema.ValidationError):
            validate_artifact("visual_bible", vb)


# --------------------------------------------------------------------------
# project config
# --------------------------------------------------------------------------

class TestProjectConfig:
    def test_loads_and_validates(self):
        schema = load_project_config_schema()
        assert schema["$id"].endswith("project_config")
        validate_project_config(project_config())

    @pytest.mark.parametrize("missing", ["version", "budget_usd_cap", "wall_time_minutes", "cast_cap", "provider_egress"])
    def test_required(self, missing):
        cfg = project_config()
        del cfg[missing]
        with pytest.raises(jsonschema.ValidationError):
            validate_project_config(cfg)

    def test_egress_content_classes_enum_and_nonempty(self):
        cfg = project_config()
        cfg["provider_egress"]["content_classes"] = []
        with pytest.raises(jsonschema.ValidationError):
            validate_project_config(cfg)
        cfg["provider_egress"]["content_classes"] = ["source_footage"]
        with pytest.raises(jsonschema.ValidationError):
            validate_project_config(cfg)

    def test_caps_are_positive(self):
        cfg = project_config()
        cfg["budget_usd_cap"] = -1
        with pytest.raises(jsonschema.ValidationError):
            validate_project_config(cfg)
        cfg = project_config()
        cfg["cast_cap"]["characters"] = 0
        with pytest.raises(jsonschema.ValidationError):
            validate_project_config(cfg)

    def test_no_extra_keys(self):
        cfg = project_config()
        cfg["budget_default_usd"] = 1
        with pytest.raises(jsonschema.ValidationError):
            validate_project_config(cfg)


# --------------------------------------------------------------------------
# migrations
# --------------------------------------------------------------------------

class TestCanonMigration:
    def test_upgraded_packet_validates_and_is_deterministic(self):
        a = upgrade_canon_packet_1_0_to_1_1(canon_1_0())
        b = upgrade_canon_packet_1_0_to_1_1(canon_1_0())
        assert a == b
        assert a["version"] == "1.1"
        validate_artifact("canon_packet", a)

    def test_id_format(self):
        p = upgrade_canon_packet_1_0_to_1_1(canon_1_0())
        import hashlib, re
        expected = "character-0-" + hashlib.sha256(
            ("Orla Penhallow" + "A ferry keeper who refuses to sell the crossing").encode("utf-8")
        ).hexdigest()[:8]
        assert p["characters"][0]["id"] == expected
        assert re.fullmatch(r"location-0-[0-9a-f]{8}", p["locations"][0]["id"])

    def test_input_not_mutated(self):
        src = canon_1_0()
        snapshot = json.dumps(src, sort_keys=True)
        upgrade_canon_packet_1_0_to_1_1(src)
        assert json.dumps(src, sort_keys=True) == snapshot

    def test_source_text_changes_id(self):
        p = canon_1_0()
        p["characters"][0]["one_line_identity"] = "different"
        assert upgrade_canon_packet_1_0_to_1_1(p)["characters"][0]["id"] != upgrade_canon_packet_1_0_to_1_1(canon_1_0())["characters"][0]["id"]

    def test_collision_fails(self, monkeypatch):
        import lib.migrations.artifacts as mig
        monkeypatch.setattr(mig, "_digest", lambda *_: "deadbeef")
        p = canon_1_0()
        p["characters"][1] = dict(p["characters"][0])
        # index differs so ids still differ; force identical index-free collision
        monkeypatch.setattr(mig, "_entity_id", lambda etype, idx, name, text: f"{etype}-x-{mig._digest(name, text)}")
        with pytest.raises(MigrationError, match="collision"):
            upgrade_canon_packet_1_0_to_1_1(p)

    def test_rejects_non_1_0_input(self):
        with pytest.raises(MigrationError):
            upgrade_canon_packet_1_0_to_1_1(canon_1_1())

    def test_entity_without_name_fails(self):
        p = canon_1_0()
        p["characters"][0] = {"one_line_identity": "nameless"}
        with pytest.raises(MigrationError):
            upgrade_canon_packet_1_0_to_1_1(p)


class TestScenePlanMigration:
    def test_marks_needs_review_and_invents_nothing(self):
        plan = upgrade_scene_plan_1_0_to_1_1(scene_plan_1_0())
        assert plan["version"] == "1.1"
        assert plan["migration_status"] == "needs_review"
        s = plan["scenes"][0]
        assert s["character_refs"] == [] and s["location_ref"] is None
        assert s["entity_free"] is False and s["model_endpoint"] == "" and s["shots"] == []
        assert "model_override" not in s
        validate_artifact("scene_plan", plan)

    def test_rejects_non_1_0(self):
        with pytest.raises(MigrationError):
            upgrade_scene_plan_1_0_to_1_1(scene_plan_1_1())


class TestProposalMigration:
    def test_defaults_and_needs_review(self):
        p = upgrade_proposal_packet_1_0_to_1_1(proposal_1_0())
        assert p["version"] == "1.1"
        assert p["runtime_shape"] == {"format": "trailer"}
        assert p["cast"] == {"character_ids": [], "location_ids": []}
        assert p["migration_status"] == "needs_review"
        validate_artifact("proposal_packet", p)

    def test_rejects_non_1_0(self):
        with pytest.raises(MigrationError):
            upgrade_proposal_packet_1_0_to_1_1(proposal_1_1())


class TestAssetManifestMigration:
    def test_visual_assets_force_needs_review(self):
        m = upgrade_asset_manifest_1_0_to_1_1(asset_manifest_1_0())
        assert m["version"] == "1.1"
        assert m["migration_status"] == "needs_review"
        visual, music = m["assets"]
        assert music["asset_class"] == "non_shot"
        assert visual["asset_class"] == "shot_visual"
        assert visual["usage_status"] == "candidate"
        assert visual["shot_id"] == "s1" and visual["take_id"] == "a1"
        assert visual["model_endpoint"] == "some/endpoint"
        assert visual["continuity"]["references_applied"] == []
        assert "refs/orla.png" in visual["continuity"]["notes"]
        validate_artifact("asset_manifest", m)

    def test_manifest_without_visuals_is_ok(self):
        src = asset_manifest_1_0()
        src["assets"] = [src["assets"][1]]
        m = upgrade_asset_manifest_1_0_to_1_1(src)
        assert m["migration_status"] == "ok"
        validate_artifact("asset_manifest", m)

    def test_legacy_string_references_on_non_visual_force_needs_review(self):
        src = asset_manifest_1_0()
        src["assets"] = [src["assets"][1]]
        src["assets"][0]["continuity"] = {"canon_refs": ["b1"], "references_applied": ["x.png"], "risk_notes_applied": []}
        m = upgrade_asset_manifest_1_0_to_1_1(src)
        assert m["migration_status"] == "needs_review"
        validate_artifact("asset_manifest", m)

    def test_rejects_non_1_0(self):
        with pytest.raises(MigrationError):
            upgrade_asset_manifest_1_0_to_1_1(asset_manifest_1_1())


class TestAssetManifest11Uniqueness:
    def test_duplicate_asset_ids_rejected(self):
        m = asset_manifest_1_1()
        m["assets"][2]["id"] = "a1"
        with pytest.raises(ArtifactValidationError, match="duplicate id 'a1'"):
            validate_artifact("asset_manifest", m)

    def test_duplicate_take_id_within_shot_rejected(self):
        m = asset_manifest_1_1()
        dup = json.loads(json.dumps(m["assets"][0]))
        dup["id"] = "a9"
        m["assets"].append(dup)
        with pytest.raises(ArtifactValidationError, match=r"duplicate \(shot_id, take_id\)"):
            validate_artifact("asset_manifest", m)

    def test_same_take_id_in_different_shots_allowed(self):
        m = asset_manifest_1_1()
        other = json.loads(json.dumps(m["assets"][0]))
        other["id"] = "a9"
        other["shot_id"] = "s1-sh2"
        m["assets"].append(other)
        validate_artifact("asset_manifest", m)

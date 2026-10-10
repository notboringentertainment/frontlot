"""D19 step 1: project_config 1.1 (multi-provider egress + qc block), dual-version
normalisation, authored-film 1.3 manifest, visual_bible qc_receipts. Invented names only."""
import json
from pathlib import Path

import pytest
import yaml

from lib.pipeline_loader import load_pipeline, manifest_digest
from lib.project_config import ProjectConfigError, normalize_egress, read_project_config
from schemas.artifacts import validate_artifact, validate_project_config

SHA = "a" * 64
V10 = {"version": "1.0", "budget_usd_cap": 5, "wall_time_minutes": 10, "cast_cap": {"characters": 2, "locations": 1},
       "default_video_endpoint": "vendor/video", "provider_egress": {"provider": "fal", "content_classes": ["prompts"]}}
V11 = {"version": "1.1", "budget_usd_cap": 5, "wall_time_minutes": 10, "cast_cap": {"characters": 2, "locations": 1},
       "default_video_endpoint": "vendor/video",
       "provider_egress": [{"provider": "fal", "content_classes": ["prompts", "reference_images"]},
                           {"provider": "openai", "content_classes": ["prompts", "generated_sheet_images"]}],
       "qc": {"judge_provider": "openai", "judge_model": "judge-x", "policy_bundle_sha256": SHA, "max_attempts_per_series": 3}}


def test_both_versions_validate_and_normalise():
    validate_project_config(V10)
    validate_project_config(V11)
    assert normalize_egress(V10) == {"fal": frozenset({"prompts"})}
    assert normalize_egress(V11)["openai"] == frozenset({"prompts", "generated_sheet_images"})


def test_1_1_requires_qc_and_known_providers():
    bad = dict(V11); bad.pop("qc")
    with pytest.raises(Exception):
        validate_project_config(bad)
    bad = json.loads(json.dumps(V11)); bad["provider_egress"][1]["provider"] = "google"
    with pytest.raises(Exception):
        validate_project_config(bad)
    bad = json.loads(json.dumps(V11)); bad["qc"]["judge_provider"] = "google"
    with pytest.raises(Exception):
        validate_project_config(bad)


def test_duplicate_provider_rejected_by_loader(tmp_path):
    dup = json.loads(json.dumps(V11)); dup["provider_egress"].append({"provider": "fal", "content_classes": ["prompts"]})
    with pytest.raises(ProjectConfigError, match="more than once"):
        normalize_egress(dup)
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(dup))
    with pytest.raises(ProjectConfigError):
        read_project_config(tmp_path)


def test_verified_config_accessors(tmp_path):
    from lib.project_config import VerifiedProjectConfig
    c10 = VerifiedProjectConfig(tmp_path, SHA, V10)
    c10.require_egress("fal", "prompts")
    with pytest.raises(ProjectConfigError):
        c10.require_egress("openai", "prompts")
    assert c10.qc is None
    with pytest.raises(ProjectConfigError, match="no qc block"):
        c10.require_qc()
    c11 = VerifiedProjectConfig(tmp_path, SHA, V11)
    c11.require_egress("openai", "prompts", "generated_sheet_images")
    with pytest.raises(ProjectConfigError, match="does not cover"):
        c11.require_egress("openai", "reference_images")
    assert c11.require_qc().max_attempts_per_series == 3


def test_manifest_1_3_adds_judge_and_keeps_1_2_intact():
    m12, m13 = load_pipeline("authored-film@1.2"), load_pipeline("authored-film@1.3")
    assert manifest_digest("authored-film@1.2") != manifest_digest("authored-film@1.3")
    s12 = {s["name"]: s for s in m12["stages"]}; s13 = {s["name"]: s for s in m13["stages"]}
    assert list(s12) == list(s13)
    assert "sheet_judge" not in s12["visual_bible"]["tools_available"]
    assert "sheet_judge" in s13["visual_bible"]["tools_available"]
    for name in s12:
        if name != "visual_bible":
            assert s12[name] == s13[name]


def test_qc_receipts_optional_and_role_bound():
    img = {"asset_id": SHA, "path": f"canon/visual/objects/{SHA}.png", "role": "x",
           "provenance": {"generator_kind": "model", "model_endpoint": "m", "prompt": "p", "generation_receipt_id": "g"}}
    recipe = {"look_hash": SHA, "builder_version": "1.3", "fields_used": ["role"], "rendered_sha256": SHA}
    look_ref = {"entity_kind": "character", "entity_id": "c1", "look_hash": SHA, "receipt_id": "r"}
    ch = {"id": "c1", "hero": img, "sheet": {"turnaround": img, "expressions": img}, "wardrobe_negative": "",
          "prompt_recipe": recipe, "look_ref": look_ref, "sheet_revision": 1, "status": "draft"}
    bible = {"version": "1.1", "project_slug": "s", "palette": {"hues": ["a", "b", "c"]},
             "generator_defaults": {"image_model": "m", "edit_model": "e"}, "characters": [ch], "locations": []}
    validate_artifact("visual_bible", bible)
    ch["qc_receipts"] = {"turnaround": "q1", "expressions": "q2"}
    validate_artifact("visual_bible", bible)
    ch["qc_receipts"] = {"front": "q1"}
    with pytest.raises(Exception):
        validate_artifact("visual_bible", bible)

"""Lib-side contract the tools boundary resolves by name (integration pass).

``lib.look_ingest.project_look_governed`` answers from the signed pipeline
pin; ``lib.receipts`` carries ``import_receipt_id`` / ``normalized_pixel_hash``
/ ``prompt_recipe`` in the signed generation record. Invented names only.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from lib import receipts
from lib.look_ingest import project_look_governed
from tests.lib.look_lock_helpers import PROJECT, pin_project


@pytest.fixture
def project(tmp_path):
    root = tmp_path / PROJECT
    root.mkdir()
    return root


def _common(**over):
    base = dict(
        execution_id="e", tool="seedream_image", model_endpoint="fake/text-to-image",
        normalized_inputs_hash="a" * 64, output_sha256="b" * 64, cost_usd=0.01,
        started_at="t0", finished_at="t1",
    )
    base.update(over)
    return base


class TestProjectLookGoverned:
    def test_unpinned_project_falls_back_to_1_1_and_is_not_governed(self, project):
        assert project_look_governed(project) is False
        (project / "project.json").write_text(json.dumps({"pipeline_type": "authored-film"}))
        assert project_look_governed(project) is False

    def test_pinned_1_2_project_is_governed(self, project):
        pin_project(project, "1.2")
        assert project_look_governed(project) is True

    def test_migrating_back_to_1_1_ungoverns(self, project):
        first = pin_project(project, "1.2")
        pin_project(project, "1.1", supersedes=first["receipt_id"])
        assert project_look_governed(project) is False

    def test_marker_disagreeing_with_signed_pin_fails_closed(self, project):
        pin_project(project, "1.2")
        (project / "project.json").write_text(
            json.dumps({"pipeline_type": "authored-film", "pipeline_manifest_version": "1.1"})
        )
        with pytest.raises(Exception, match="disagrees with the signed pin"):
            project_look_governed(project)


class TestGenerationReceiptPassthrough:
    def test_prompt_recipe_is_normalized_and_signed(self, project):
        prompt = "a wiry engineer. single character."
        recipe = {
            "look_hash": "c" * 64, "builder_version": "1.0", "fields_used": ["prompt_safe_description"],
            "rendered_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "extra": "dropped",
        }
        row = receipts.record_generation(project, prompt=prompt, prompt_recipe=recipe, **_common())
        assert row["prompt_recipe"] == {k: recipe[k] for k in ("look_hash", "builder_version", "fields_used", "rendered_sha256")}
        assert receipts.find_generation(project, "b" * 64)["prompt_recipe"] == row["prompt_recipe"]
        # The recipe is inside the signed record: editing it after the fact invalidates the row.
        path = project / "generation-receipts.jsonl"
        tampered = json.loads(path.read_text())
        tampered["prompt_recipe"]["rendered_sha256"] = "d" * 64
        path.write_text(json.dumps(tampered) + "\n")
        with pytest.raises(receipts.ReceiptChainError, match="altered"):
            receipts.find_generation(project, "b" * 64)

    @pytest.mark.parametrize("bad", [
        "not a dict",
        {"look_hash": "zz", "builder_version": "1.0", "fields_used": [], "rendered_sha256": "e" * 64},
        {"look_hash": "c" * 64, "builder_version": "", "fields_used": [], "rendered_sha256": "e" * 64},
        {"look_hash": "c" * 64, "builder_version": "1.0", "fields_used": "prompt", "rendered_sha256": "e" * 64},
        {"look_hash": "c" * 64, "builder_version": "1.0", "fields_used": [1], "rendered_sha256": "e" * 64},
        {"look_hash": "c" * 64, "builder_version": "1.0", "fields_used": []},
    ])
    def test_prompt_recipe_is_validated(self, project, bad):
        with pytest.raises(ValueError, match="prompt_recipe"):
            receipts.record_generation(project, prompt_recipe=bad, **_common())

    def test_import_fields_are_validated_and_carried(self, project):
        row = receipts.record_generation(
            project, import_receipt_id="imp-1", normalized_pixel_hash="b" * 64, **_common(),
        )
        assert row["import_receipt_id"] == "imp-1" and row["normalized_pixel_hash"] == "b" * 64
        with pytest.raises(ValueError, match="normalized_pixel_hash"):
            receipts.record_generation(project, normalized_pixel_hash="short", **_common(output_sha256="c" * 64))
        with pytest.raises(ValueError, match="normalized_pixel_hash"):
            receipts.record_generation(
                project, generator_kind="imported", origin_tool="t", attestation_receipt_id="att",
                normalized_pixel_hash="f" * 64, **_common(model_endpoint=None, output_sha256="c" * 64),
            )
        with pytest.raises(ValueError, match="import_receipt_id"):
            receipts.record_generation(project, import_receipt_id="", **_common(output_sha256="c" * 64))

    def test_legacy_receipts_without_the_fields_still_verify(self, project):
        row = receipts.record_generation(project, **_common())
        assert row["prompt_recipe"] is None and row["import_receipt_id"] is None
        assert receipts.find_generation(project, "b" * 64) == row

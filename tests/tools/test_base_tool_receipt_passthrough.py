"""base_tool receipt writer passes look governance and imported provenance through to the receipt."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lib import receipts
from tools.base_tool import BaseTool, ToolResult, _receipt_passthrough_fields

from tests.tools._authored_film_helpers import make_project, tiny_png_bytes

CHAR_REF = {"entity_kind": "character", "entity_id": "quill-marrow", "look_hash": "a" * 64}
HEADSHOT = {"entity_id": "quill-marrow", "asset_id": "d" * 64, "approval_receipt_id": "hs-1"}


class _LocalTool(BaseTool):
    name = "local_probe"
    version = "0.0.1"
    tier = "process"
    capability = "probe"
    input_schema = {"type": "object"}
    emits_generation_receipt = True
    metadata_to_emit: dict = {}

    def execute(self, inputs):
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tiny_png_bytes())
        return ToolResult(success=True, data={"output_path": str(out)}, metadata=dict(self.metadata_to_emit))


def test_passthrough_selects_only_set_keys():
    assert _receipt_passthrough_fields({"look_refs": None, "seed": 1}) == {}
    assert _receipt_passthrough_fields({"look_refs": [CHAR_REF], "headshot_ref": HEADSHOT}) == {
        "look_refs": [CHAR_REF], "headshot_ref": HEADSHOT,
    }
    meta = {"generator_kind": "imported", "origin_tool": "elsewhere-gen", "attestation_receipt_id": "att-1",
            "import_receipt_id": "imp-1", "normalized_pixel_hash": "f" * 64}
    assert _receipt_passthrough_fields(meta) == {k: v for k, v in meta.items() if k != "generator_kind"}
    with pytest.raises(ValueError, match="origin_tool"):
        _receipt_passthrough_fields({"generator_kind": "imported", "normalized_pixel_hash": "f" * 64})


def test_look_refs_and_headshot_ref_land_in_the_signed_receipt(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch, "proj-probe")
    tool = _LocalTool()
    tool.metadata_to_emit = {"generator_kind": "local", "local_tool": "local_probe", "local_tool_version": "1",
                             "look_refs": [CHAR_REF], "headshot_ref": HEADSHOT}
    r = tool.execute({"output_path": str(project / "canon" / "visual" / "objects" / "p.png")})
    assert r.success
    from lib.pathsafe import sha256_file

    row = receipts.find_generation(project, sha256_file(project / "canon" / "visual" / "objects" / "p.png"))
    assert row["look_refs"] == [CHAR_REF] and row["headshot_ref"] == HEADSHOT
    assert row["generator_kind"] == "local"


def test_imported_provenance_is_passed_to_record_generation(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch, "proj-import")
    tool = _LocalTool()
    tool.metadata_to_emit = {"generator_kind": "imported", "origin_tool": "elsewhere-gen",
                             "attestation_receipt_id": "att-1", "import_receipt_id": "imp-1",
                             "normalized_pixel_hash": "f" * 64}
    seen = {}

    def fake_record(project_root, **fields):
        seen.update(fields)
        return {"receipt_id": "r"}

    with patch.object(receipts, "record_generation", side_effect=fake_record):
        r = tool.execute({"output_path": str(project / "canon" / "visual" / "objects" / "i.png")})
    assert r.success
    assert seen["generator_kind"] == "imported"
    assert seen["origin_tool"] == "elsewhere-gen" and seen["attestation_receipt_id"] == "att-1"
    assert seen["import_receipt_id"] == "imp-1" and seen["normalized_pixel_hash"] == "f" * 64
    # The import itself is lib-side: the writer only carries the fields.
    tool.metadata_to_emit = {"generator_kind": "imported"}
    with pytest.raises(ValueError, match="imported provenance receipt is missing"):
        tool.execute({"output_path": str(project / "canon" / "visual" / "objects" / "j.png")})

"""asset_manifest 1.1 enforcement: an asset's scene_id must own its shot_id."""
from __future__ import annotations

import pytest

from lib import canon_enforcement as ce


def _scene_plan():
    return {
        "version": "1.1",
        "scenes": [
            {"id": "sc-1", "entity_free": True, "model_endpoint": "vendor/model-a",
             "shots": [{"shot_id": "sc-1-sh-1", "description": "wide"}]},
            {"id": "sc-2", "entity_free": True, "model_endpoint": "vendor/model-a",
             "shots": [{"shot_id": "sc-2-sh-1", "description": "close"}]},
        ],
    }


def _manifest(scene_id: str):
    return {
        "version": "1.1",
        "assets": [
            {"id": "frame-1", "type": "image", "path": "assets/f.png", "source_tool": "img",
             "scene_id": scene_id, "asset_class": "storyboard_frame", "shot_id": "sc-1-sh-1",
             "continuity": {"canon_refs": [], "references_applied": [], "risk_notes_applied": []}},
        ],
    }


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    import lib.receipts as receipts_mod

    monkeypatch.setattr(ce, "_safe_file_sha256", lambda *a: "f" * 64)
    monkeypatch.setattr(ce, "approved_image_owners", lambda bible: {})
    monkeypatch.setattr(receipts_mod, "find_generation", lambda *a: {"ok": True})
    return tmp_path


def test_asset_in_owning_scene_passes(quiet):
    ce._check_assets_v11(quiet, _manifest("sc-1"), _scene_plan(), {})


def test_asset_filed_under_wrong_scene_is_rejected(quiet):
    with pytest.raises(Exception, match="scene_id 'sc-2'.*'sc-1-sh-1'.*'sc-1'"):
        ce._check_assets_v11(quiet, _manifest("sc-2"), _scene_plan(), {})

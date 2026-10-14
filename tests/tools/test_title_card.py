"""Local title-card renderer: deterministic content-addressed PNG, font path safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.receipts import find_generation
from lib.state_io import read_jsonl
from lib.receipts import generation_receipts_path
from tools.graphics.title_card import TitleCard

from tests.tools._authored_film_helpers import make_project


@pytest.fixture
def env(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch, "proj-lantern")
    from PIL import ImageFont

    font_bytes = getattr(ImageFont.load_default(size=12), "font_bytes", None)
    if not font_bytes:
        pytest.skip("Pillow build has no bundled TrueType font")
    font = project / "assets" / "fonts" / "test-font.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(font_bytes)
    return project, font


def _inputs(project, font, **extra):
    base = {"text": "THE FIRST LIGHT\nA FILM", "font_path": str(font), "font_size": 48,
            "color": "#F4E7C3", "width": 640, "height": 360, "alignment": "center",
            "project_dir": str(project)}
    base.update(extra)
    return base


def test_render_is_deterministic_and_content_addressed(env):
    project, font = env
    tool = TitleCard()
    r1 = tool.execute(_inputs(project, font))
    r2 = tool.execute(_inputs(project, font))
    assert r1.success and r2.success, (r1.error, r2.error)
    assert r1.data["asset_id"] == r2.data["asset_id"]
    out = Path(r1.data["output_path"])
    assert out == project / "canon" / "visual" / "objects" / f"{r1.data['asset_id']}.png"
    assert out.exists()
    from PIL import Image

    with Image.open(out) as im:
        assert im.mode == "RGBA" and im.size == (640, 360)
        assert im.getpixel((0, 0))[3] == 0  # transparent background
        bbox = im.getchannel("A").getbbox()
        assert bbox is not None and bbox[1] < 180 < bbox[3]  # text present, vertically centred
    assert r1.cost_usd == 0.0
    assert r1.metadata["generator_kind"] == "local"
    assert r1.metadata["local_tool"] == "title_card"
    assert r1.metadata["local_tool_version"] == "1.0"
    assert r1.metadata["input_asset_ids"] == []
    assert r1.metadata["parameters_hash"] == r2.metadata["parameters_hash"]
    receipt = find_generation(project, r1.data["asset_id"])
    assert receipt["generator_kind"] == "local" and receipt["parameters_hash"] == r1.metadata["parameters_hash"]
    assert len(read_jsonl(generation_receipts_path(project))) == 2
    assert not any((project / ".staging").iterdir())


def test_different_text_changes_asset_and_parameters_hash(env):
    project, font = env
    r1 = TitleCard().execute(_inputs(project, font))
    r2 = TitleCard().execute(_inputs(project, font, text="SECOND TITLE"))
    assert r1.data["asset_id"] != r2.data["asset_id"]
    assert r1.metadata["parameters_hash"] != r2.metadata["parameters_hash"]


def test_font_outside_project_and_repo_fonts_rejected(env, tmp_path):
    project, font = env
    stray = tmp_path / "stray.ttf"
    stray.write_bytes(font.read_bytes())
    r = TitleCard().execute(_inputs(project, stray))
    assert not r.success and "font_path" in r.error
    assert not (project / "canon").exists()


def test_bad_alignment_and_empty_text_rejected(env):
    project, font = env
    assert not TitleCard().execute(_inputs(project, font, alignment="justify")).success
    assert not TitleCard().execute(_inputs(project, font, text="")).success

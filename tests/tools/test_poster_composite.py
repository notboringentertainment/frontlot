"""Local poster compositor: deterministic content-addressed PNG, receipted derivation, path safety."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from lib.pathsafe import sha256_file
from lib.receipts import find_generation
from tools.graphics.poster_composite import PosterComposite

from tests.tools._authored_film_helpers import make_project


def _png(size, color):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _receipt(project, path, *, local=False):
    from lib import receipts

    sha = sha256_file(path)
    kwargs = {"generator_kind": "local", "local_tool": "title_card", "local_tool_version": "1.0"} if local \
        else {"model_endpoint": "vendor/image-model", "prompt": f"key art {path.name}"}
    receipts.record_generation(
        project, execution_id=f"exec-{path.name}", tool="title_card" if local else "seedream_image",
        normalized_inputs_hash="a" * 64, output_sha256=sha, cost_usd=0.0,
        started_at="2026-08-25T00:00:00+00:00", finished_at="2026-08-25T00:00:01+00:00", **kwargs,
    )
    return sha


@pytest.fixture
def env(tmp_path, monkeypatch):
    project = make_project(tmp_path, monkeypatch, "proj-marquee")
    objects = project / "canon" / "visual" / "objects"
    objects.mkdir(parents=True)
    key_art = objects / "key.png"
    key_art.write_bytes(_png((400, 600), (20, 40, 80, 255)))
    title = objects / "title.png"
    # transparent card with an opaque white band in the middle
    card = Image.new("RGBA", (200, 50), (0, 0, 0, 0))
    for x in range(200):
        for y in range(20, 30):
            card.putpixel((x, y), (255, 255, 255, 255))
    buf = io.BytesIO()
    card.save(buf, format="PNG")
    title.write_bytes(buf.getvalue())
    # both inputs are receipted pipeline outputs (the compositor requires it)
    _receipt(project, key_art)
    _receipt(project, title, local=True)
    return project, key_art, title


def _inputs(project, key_art, title, **extra):
    base = {"key_art_path": str(key_art), "title_card_path": str(title), "project_dir": str(project),
            "title_x": 0.5, "title_y": 0.8, "title_scale": 0.5}
    base.update(extra)
    return base


def test_composite_is_deterministic_and_receipted(env):
    project, key_art, title = env
    tool = PosterComposite()
    r1 = tool.execute(_inputs(project, key_art, title))
    r2 = tool.execute(_inputs(project, key_art, title))
    assert r1.success and r2.success, (r1.error, r2.error)
    assert r1.data["asset_id"] == r2.data["asset_id"]
    out = Path(r1.data["output_path"])
    assert out == project / "canon" / "visual" / "objects" / f"{r1.data['asset_id']}.png"
    assert sha256_file(out) == r1.data["asset_id"]
    with Image.open(out) as im:
        assert im.size == (400, 600)
        assert im.getpixel((10, 10))[:3] == (20, 40, 80)  # key art untouched outside the title
        assert im.getpixel((200, 480))[:3] == (255, 255, 255)  # title band at y=0.8, centred
    assert r1.cost_usd == 0.0
    meta = r1.metadata
    assert meta["generator_kind"] == "local" and meta["local_tool"] == "poster_composite"
    assert meta["local_tool_version"] == "1.0"
    assert meta["input_asset_ids"] == [sha256_file(key_art), sha256_file(title)]
    assert meta["parameters_hash"] == r2.metadata["parameters_hash"]
    receipt = find_generation(project, r1.data["asset_id"])
    assert receipt["generator_kind"] == "local"
    assert receipt["input_asset_ids"] == meta["input_asset_ids"]
    assert receipt["parameters_hash"] == meta["parameters_hash"]
    assert not any((project / ".staging").iterdir())


def test_layout_and_inputs_change_asset_and_hash(env):
    project, key_art, title = env
    base = PosterComposite().execute(_inputs(project, key_art, title))
    moved = PosterComposite().execute(_inputs(project, key_art, title, title_y=0.2))
    assert moved.data["asset_id"] != base.data["asset_id"]
    assert moved.metadata["parameters_hash"] != base.metadata["parameters_hash"]
    other = key_art.with_name("key2.png")
    other.write_bytes(_png((400, 600), (90, 10, 10, 255)))
    _receipt(project, other)
    swapped = PosterComposite().execute(_inputs(project, other, title))
    assert swapped.data["asset_id"] != base.data["asset_id"]
    assert swapped.metadata["input_asset_ids"][0] == sha256_file(other)


def test_inputs_outside_project_and_bad_params_rejected(env, tmp_path):
    project, key_art, title = env
    stray = tmp_path / "stray.png"
    stray.write_bytes(key_art.read_bytes())
    r = PosterComposite().execute(_inputs(project, stray, title))
    assert not r.success and "poster_composite failed" in r.error
    r = PosterComposite().execute(_inputs(project, key_art, stray))
    assert not r.success
    for bad in ({"title_scale": 0}, {"title_scale": 1.5}, {"title_x": -0.1}, {"title_y": 2}):
        assert not PosterComposite().execute(_inputs(project, key_art, title, **bad)).success
    assert len(list((project / "canon" / "visual" / "objects").iterdir())) == 2  # only the two inputs


def test_unreceipted_input_is_rejected(env):
    """Codex R2 #7: local derivation cannot launder an imported image."""
    from lib import gates
    from lib.receipts import generation_receipts_path
    from lib.state_io import append_jsonl, read_jsonl

    project, key_art, title = env
    imported = key_art.with_name("imported.png")
    imported.write_bytes(_png((400, 600), (1, 2, 3, 255)))
    r = PosterComposite().execute(_inputs(project, imported, title))
    assert not r.success and "no verified generation receipt" in r.error and "imported.png" in r.error
    r = PosterComposite().execute(_inputs(project, key_art, imported))
    assert not r.success and "title_card" in r.error
    # a forged receipt row (signature of another row) does not admit it either
    genuine = read_jsonl(generation_receipts_path(project))[0]
    forged = dict(genuine, receipt_id="forged", output_sha256=sha256_file(imported))
    append_jsonl(generation_receipts_path(project), forged)
    r = PosterComposite().execute(_inputs(project, imported, title))
    # the signed per-project chain rejects the extra row before any receipt lookup succeeds
    assert not r.success and ("no verified generation receipt" in r.error or "not in the signed chain" in r.error)
    assert len(list((project / "canon" / "visual" / "objects").iterdir())) == 3  # nothing composited
    assert not any((project / ".staging").iterdir()) if (project / ".staging").exists() else True

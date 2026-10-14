import hashlib
import io
import os

import pytest
from PIL import Image

from lib import pathsafe
from lib.pathsafe import PathSafetyError


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "inputs").mkdir(parents=True)
    (root / "inputs" / "a.txt").write_text("hello")
    return root


def test_resolve_input_inside_root(project):
    resolved = pathsafe.resolve_input("inputs/a.txt", project)
    assert resolved == (project / "inputs" / "a.txt").resolve()
    assert pathsafe.resolve_input(project / "inputs" / "a.txt", project) == resolved


def test_resolve_input_rejects_dotdot_traversal(project, tmp_path):
    (tmp_path / "secret.txt").write_text("x")
    with pytest.raises(PathSafetyError):
        pathsafe.resolve_input("inputs/../../secret.txt", project)


def test_resolve_input_rejects_absolute_escape(project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(PathSafetyError):
        pathsafe.resolve_input(outside, project)


def test_resolve_input_rejects_symlink_file_and_dir(project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (project / "inputs" / "link.txt").symlink_to(outside)
    with pytest.raises(PathSafetyError, match="symlink"):
        pathsafe.resolve_input("inputs/link.txt", project)

    outside_dir = tmp_path / "odir"
    outside_dir.mkdir()
    (outside_dir / "f.txt").write_text("y")
    (project / "linkdir").symlink_to(outside_dir)
    with pytest.raises(PathSafetyError, match="symlink"):
        pathsafe.resolve_input("linkdir/f.txt", project)


def test_resolve_input_missing_raises(project):
    with pytest.raises(PathSafetyError):
        pathsafe.resolve_input("inputs/missing.txt", project)


def test_validate_output_parent(project, tmp_path):
    out = pathsafe.validate_output_parent("inputs/new.png", project)
    assert out.parent == (project / "inputs").resolve()
    with pytest.raises(PathSafetyError):
        pathsafe.validate_output_parent("nope/new.png", project)
    with pytest.raises(PathSafetyError):
        pathsafe.validate_output_parent(tmp_path / "new.png", project)
    outside_dir = tmp_path / "odir"
    outside_dir.mkdir()
    (project / "linkdir").symlink_to(outside_dir)
    with pytest.raises(PathSafetyError, match="symlink"):
        pathsafe.validate_output_parent("linkdir/new.png", project)


def test_staging_file_is_unique_under_staging_dir(project):
    a = pathsafe.staging_file(project, ".png")
    b = pathsafe.staging_file(project, ".png")
    assert a != b
    assert a.parent == (project / ".staging").resolve()
    assert a.suffix == ".png" and a.exists()


def test_sha256_file(project):
    assert pathsafe.sha256_file(project / "inputs" / "a.txt") == hashlib.sha256(b"hello").hexdigest()


def _pixels():
    im = Image.new("RGB", (8, 6))
    px = im.load()
    for y in range(6):
        for x in range(8):
            px[x, y] = (x * 30, y * 40, (x + y) * 10)
    return im


def _stage_image(project, fmt, **save_kw):
    im = _pixels()
    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
    path = pathsafe.staging_file(project, suffix)
    im.save(path, format=fmt, **save_kw)
    return path


def test_store_png_is_deterministic_and_metadata_free(project):
    objects = project / "canon" / "visual" / "objects"
    staged = pathsafe.staging_file(project, ".png")
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "noise that must be stripped")
    _pixels().save(staged, format="PNG", pnginfo=info, dpi=(300, 300))
    asset_id, final = pathsafe.store_content_addressed(staged, objects, ".png")
    assert final == objects / f"{asset_id}.png"
    assert pathsafe.sha256_file(final) == asset_id
    assert not staged.exists()
    with Image.open(final) as im:
        assert "Comment" not in im.info and "dpi" not in im.info
        assert list(im.getdata()) == list(_pixels().getdata())


def test_jpeg_webp_png_with_same_pixels_share_asset_id(project):
    objects = project / "objects"
    png_id, _ = pathsafe.store_content_addressed(_stage_image(project, "PNG"), objects, ".png")
    # Lossless variants of the same pixels
    webp_id, _ = pathsafe.store_content_addressed(
        _stage_image(project, "WEBP", lossless=True), objects, ".png"
    )
    assert webp_id == png_id
    # JPEG is lossy; emulate a provider JPEG whose decoded pixels equal the PNG's
    # by decoding the JPEG first and re-staging its pixels as the reference.
    jpg_path = _stage_image(project, "JPEG", quality=100, subsampling=0)
    with Image.open(jpg_path) as jpg:
        decoded = jpg.convert("RGB").copy()
    ref = pathsafe.staging_file(project, ".png")
    decoded.save(ref, format="PNG")
    jpg_id, _ = pathsafe.store_content_addressed(jpg_path, objects, ".png")
    ref_id, _ = pathsafe.store_content_addressed(ref, objects, ".png")
    assert jpg_id == ref_id
    assert len(list(objects.iterdir())) == len({png_id, jpg_id})


def test_second_store_is_idempotent(project):
    objects = project / "objects"
    id1, p1 = pathsafe.store_content_addressed(_stage_image(project, "PNG"), objects, ".png")
    mtime = p1.stat().st_mtime_ns
    id2, p2 = pathsafe.store_content_addressed(_stage_image(project, "PNG"), objects, ".png")
    assert (id1, p1) == (id2, p2)
    assert p1.stat().st_mtime_ns == mtime
    assert len(list(objects.iterdir())) == 1
    assert list((project / ".staging").iterdir()) == []


def test_store_non_image_uses_raw_hash_and_extension(project):
    objects = project / "objects"
    staged = pathsafe.staging_file(project, ".mp4")
    staged.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    asset_id, final = pathsafe.store_content_addressed(staged, objects, "mp4")
    assert asset_id == hashlib.sha256(b"\x00\x00\x00\x18ftypmp42fake").hexdigest()
    assert final == objects / f"{asset_id}.mp4"
    assert final.exists() and not staged.exists()


def test_store_detects_corrupted_existing_object(project):
    objects = project / "objects"
    objects.mkdir()
    staged = pathsafe.staging_file(project, ".bin")
    staged.write_bytes(b"abc")
    asset_id = hashlib.sha256(b"abc").hexdigest()
    (objects / f"{asset_id}.bin").write_bytes(b"tampered")
    with pytest.raises(PathSafetyError):
        pathsafe.store_content_addressed(staged, objects, ".bin")

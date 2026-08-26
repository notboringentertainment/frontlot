"""Reference import: normalization, taint, origin conflicts, imported lineage (Slice A′ 1, 5, 6, 7)."""

import hashlib
import io
import json

import pytest
from PIL import Image

from lib import receipts
from lib.reference_import import (
    ORIGIN_CASTING,
    ORIGIN_IMPORTED_SYNTHETIC,
    ReferenceImportError,
    TaintError,
    assert_untainted,
    finalize_reference_import,
    imported_image_ref,
    normalize_image_bytes,
    normalize_staged_file,
    prepare_reference_import,
    record_imported_generation,
    tainted_hashes,
    verify_lineage,
)
from lib.canonical_json import record_sha256
from lib import gates
from schemas.artifacts import validate_artifact

pillow_heif = pytest.importorskip("pillow_heif")


def _pixels(size=(12, 9)):
    im = Image.new("RGB", size)
    px = im.load()
    for x in range(size[0]):
        for y in range(size[1]):
            px[x, y] = ((x * 20) % 256, (y * 25) % 256, 90)
    return im


def _png(im, **kw):
    buf = io.BytesIO()
    im.save(buf, format="PNG", **kw)
    return buf.getvalue()


def _heic(im, **kw):
    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    im.save(buf, format="HEIF", quality=-1, **kw)  # lossless
    return buf.getvalue()


def _jpeg(im, **kw):
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=100, subsampling=0, **kw)
    return buf.getvalue()


def _decoded_png(data: bytes) -> bytes:
    """The decoded pixels of any container, re-saved as a plain PNG."""
    pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        clean = Image.frombytes(im.mode, im.size, im.tobytes())
    return _png(clean)


class TestNormalization:
    @pytest.mark.parametrize("encode", [_png, _jpeg, _heic], ids=["png", "jpeg", "heic"])
    def test_identical_pixels_hash_identically_regardless_of_container(self, encode):
        # Lossy containers (JPEG, HEIC's YCbCr path) do not round-trip bit-
        # exactly, so the invariant is stated on DECODED pixels: whatever a
        # container decodes to, the same pixels in a PNG give the same hash.
        data = encode(_pixels())
        a = normalize_image_bytes(data)
        b = normalize_image_bytes(_decoded_png(data))
        assert a.sha256 == b.sha256 and b.source_format == "png"
        assert a.source_format == {_png: "png", _jpeg: "jpeg", _heic: "heif"}[encode]

    def test_png_metadata_and_icc_do_not_change_the_hash(self):
        from PIL import PngImagePlugin

        im = _pixels()
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "shot on a phone at 51.5N 0.1W")
        noisy = _png(im, pnginfo=info)
        assert normalize_image_bytes(noisy).sha256 == normalize_image_bytes(_png(im)).sha256
        with Image.open(io.BytesIO(normalize_image_bytes(noisy).png_bytes)) as back:
            assert not back.text

    def test_exif_orientation_is_applied_and_metadata_dropped(self):
        im = _pixels()
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 CW on display
        exif[0x010E] = "taken at 51.5N 0.1W"  # description — must not survive
        rotated = _jpeg(im, exif=exif.tobytes())
        out = normalize_image_bytes(rotated)
        assert (out.width, out.height) == (im.height, im.width)
        with Image.open(io.BytesIO(out.png_bytes)) as back:
            assert back.getexif() == {} or not dict(back.getexif())
            assert "icc_profile" not in back.info and not back.text

    def test_png_alpha_preserved_jpeg_source_flattened(self):
        rgba = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
        out = normalize_image_bytes(_png(rgba))
        with Image.open(io.BytesIO(out.png_bytes)) as back:
            assert back.mode == "RGBA"
        # the same pixels through a non-alpha path flatten on white
        flat = normalize_image_bytes(_heic(rgba))
        with Image.open(io.BytesIO(flat.png_bytes)) as back:
            assert back.mode == "RGB" and back.getpixel((0, 0)) == (255, 255, 255)

    def test_multi_frame_rejected(self):
        frames = [Image.new("RGB", (4, 4), c) for c in ((1, 2, 3), (4, 5, 6))]
        buf = io.BytesIO()
        frames[0].save(buf, format="PNG", save_all=True, append_images=frames[1:], duration=50)
        with pytest.raises(ReferenceImportError, match="multi-frame"):
            normalize_image_bytes(buf.getvalue())

    def test_oversize_rejected(self, monkeypatch):
        import lib.reference_import as ri

        monkeypatch.setattr(ri, "MAX_PIXELS", 50)
        with pytest.raises(ReferenceImportError, match="pixel limit|limit is"):
            normalize_image_bytes(_png(_pixels()))
        monkeypatch.setattr(ri, "MAX_PIXELS", 40_000_000)
        monkeypatch.setattr(ri, "MAX_SOURCE_BYTES", 10)
        with pytest.raises(ReferenceImportError, match="bytes"):
            normalize_image_bytes(_png(_pixels()))

    def test_unknown_format_rejected(self):
        with pytest.raises(ReferenceImportError, match="not a PNG"):
            normalize_image_bytes(b"GIF89a" + b"\0" * 20)

    def test_missing_heif_decoder_is_explicit(self, monkeypatch):
        import builtins
        import lib.reference_import as ri

        real = builtins.__import__

        def fake(name, *a, **k):
            if name == "pillow_heif":
                raise ImportError("no")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        with pytest.raises(ReferenceImportError, match="pillow-heif"):
            ri._ensure_heif_decoder()

    def test_staged_source_deleted_on_success_and_failure(self, tmp_path):
        ok = tmp_path / "ok.png"
        ok.write_bytes(_png(_pixels()))
        normalize_staged_file(ok)
        assert not ok.exists()
        bad = tmp_path / "bad.bin"
        bad.write_bytes(b"not an image")
        with pytest.raises(ReferenceImportError):
            normalize_staged_file(bad)
        assert not bad.exists()


# ---------------------------------------------------------------------------

def _approve_import(project_dir, prepared):
    req = json.loads(prepared.request_path.read_text())
    token = gates.mint_gate_token("p", req["stage"], req["scope"], record_sha256(req["approval_record"]))
    return receipts.record_human_approval(
        project_dir, "p", req["stage"], req["scope"], req["approval_record"], token, "reference_import",
        entity_id=req["entity_id"], envelope=req["envelope"],
    )


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    return d


def _stage(project_dir, name, data):
    p = project_dir / ".staging" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class TestImportGate:
    def test_synthetic_import_becomes_lineage_root(self, project_dir):
        src = _stage(project_dir, "in.png", _png(_pixels()))
        prepared = prepare_reference_import(project_dir, "p", src, origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="elsewhere-gen")
        assert not src.exists() and prepared.staged_path.exists()
        req = json.loads(prepared.request_path.read_text())
        assert req["approval_record"]["attestation_text"] == "machine-generated, depicts no real person"
        receipt = _approve_import(project_dir, prepared)
        imported = finalize_reference_import(project_dir, receipt["receipt_id"])
        assert imported.path == project_dir / "canon/visual/objects" / f"{prepared.normalized_pixel_hash}.png"
        assert not prepared.staged_path.exists()
        gen = receipts.find_generation(project_dir, prepared.normalized_pixel_hash)
        assert gen["generator_kind"] == "imported" and gen["attestation_receipt_id"] == receipt["receipt_id"]
        assert gen["receipt_id"] == imported.import_receipt_id
        ref = imported_image_ref(project_dir, imported)
        validate_artifact("headshot_packet", {"version": "1.0", "state": "pending", "characters": [{
            "entity_kind": "character", "entity_id": "x",
            "look_ref": {"entity_kind": "character", "entity_id": "x", "look_hash": "a" * 64, "receipt_id": "r"},
            "candidates": [ref]}]})
        assert verify_lineage(project_dir, prepared.normalized_pixel_hash) == [prepared.normalized_pixel_hash]
        assert tainted_hashes(project_dir) == set()

    def test_casting_import_is_tainted_and_never_a_root(self, project_dir):
        src = _stage(project_dir, "face.jpg", _jpeg(_pixels()))
        prepared = prepare_reference_import(project_dir, "p", src, origin_class=ORIGIN_CASTING)
        receipt = _approve_import(project_dir, prepared)
        imported = finalize_reference_import(project_dir, receipt["receipt_id"])
        assert imported.path.parent == project_dir / "canon/visual/casting-inspiration"
        assert imported.import_receipt_id is None
        assert tainted_hashes(project_dir) == {prepared.normalized_pixel_hash}
        with pytest.raises(TaintError):
            assert_untainted(project_dir, [prepared.normalized_pixel_hash])
        with pytest.raises(TaintError):
            record_imported_generation(project_dir, output_sha256=prepared.normalized_pixel_hash,
                                       origin_tool="x", attestation_receipt_id=receipt["receipt_id"])
        with pytest.raises(ReferenceImportError):
            imported_image_ref(project_dir, imported)

    def test_identical_pixels_under_another_class_are_refused(self, project_dir):
        data = _png(_pixels())
        prepared = prepare_reference_import(project_dir, "p", _stage(project_dir, "a.png", data), origin_class=ORIGIN_CASTING)
        _approve_import(project_dir, prepared)
        with pytest.raises(TaintError, match="already imported as casting_inspiration"):
            prepare_reference_import(project_dir, "p", _stage(project_dir, "b.png", data),
                                     origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="x")
        # and the reverse direction, from a HEIC of the same pixels
        heic = _heic(_pixels())
        other = prepare_reference_import(project_dir, "p", _stage(project_dir, "c.png", _png(Image.new("RGB", (3, 3), (9, 9, 9)))),
                                         origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="x")
        _approve_import(project_dir, other)
        with pytest.raises(TaintError, match="already imported as imported_synthetic"):
            prepare_reference_import(project_dir, "p", _stage(project_dir, "d.png", _png(Image.new("RGB", (3, 3), (9, 9, 9)))),
                                     origin_class=ORIGIN_CASTING)
        assert normalize_image_bytes(heic).source_format == "heif"

    def test_deleting_local_files_does_not_clear_taint(self, project_dir):
        prepared = prepare_reference_import(project_dir, "p", _stage(project_dir, "a.png", _png(_pixels())), origin_class=ORIGIN_CASTING)
        receipt = _approve_import(project_dir, prepared)
        finalize_reference_import(project_dir, receipt["receipt_id"])
        import shutil

        shutil.rmtree(project_dir / "canon")
        assert tainted_hashes(project_dir) == {prepared.normalized_pixel_hash}

    def test_lineage_walks_inputs_and_references(self, project_dir):
        from tests.lib.look_lock_helpers import image, pin_project

        (project_dir / "project.json").write_text(json.dumps({"project_id": "p", "pipeline_type": "authored-film"}))
        pin_project(project_dir, "1.2")  # governed: parents must exist at creation
        prepared = prepare_reference_import(project_dir, "p", _stage(project_dir, "a.png", _png(_pixels())), origin_class=ORIGIN_CASTING)
        _approve_import(project_dir, prepared)
        root = image(project_dir, "root")
        child = image(project_dir, "child", references_applied=[{"asset_id": root["asset_id"], "path": root["path"], "role": "hero"}])
        assert set(verify_lineage(project_dir, child["asset_id"])) == {root["asset_id"], child["asset_id"]}
        # #5: a casting hash is never a receipted asset, so citing it is refused at creation
        with pytest.raises(ValueError, match="no verified generation receipt"):
            image(project_dir, "poisoned", input_asset_ids=[prepared.normalized_pixel_hash])
        # pixels receipted first and imported as casting_inspiration later are tainted in lineage
        from lib.reference_import import import_record

        record = import_record(ORIGIN_CASTING, root["asset_id"], origin_tool=None)
        token = gates.mint_gate_token("p", "look_lock", f"reference:{root['asset_id']}", record_sha256(record))
        receipts.record_human_approval(project_dir, "p", "look_lock", f"reference:{root['asset_id']}", record, token,
                                       "reference_import", entity_id="ref-x",
                                       envelope={"origin_class": ORIGIN_CASTING, "normalized_pixel_hash": root["asset_id"]})
        with pytest.raises(TaintError):
            verify_lineage(project_dir, child["asset_id"])
        # #5: an unreceipted parent is refused when the receipt is CREATED
        with pytest.raises(ValueError, match="no verified generation receipt"):
            image(project_dir, "orphan", input_asset_ids=["b" * 64])
        with pytest.raises(ValueError, match="64-hex"):
            image(project_dir, "orphan2", input_asset_ids=["not-a-hash"])

    def test_imported_receipt_requires_attestation(self, project_dir):
        with pytest.raises(ValueError, match="origin_tool and attestation_receipt_id"):
            receipts.record_generation(
                project_dir, execution_id="e", tool="t", normalized_inputs_hash="a" * 64, output_sha256="b" * 64,
                cost_usd=0, started_at="x", finished_at="y", generator_kind="imported",
            )
        with pytest.raises(ReferenceImportError, match="no verified imported_synthetic"):
            record_imported_generation(project_dir, output_sha256="b" * 64, origin_tool="t", attestation_receipt_id="nope")

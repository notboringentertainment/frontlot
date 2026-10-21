"""Look governance end to end: the tools boundary calling the REAL lib verifiers.

No lib monkeypatching. Every receipt is minted through the gates WAL in a
per-test gates dir (tests/lib/look_lock_helpers); only the FAL network calls
are mocked. Invented names only.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.pathsafe import sha256_file
from lib.reference_import import (
    ORIGIN_CASTING,
    ORIGIN_IMPORTED_SYNTHETIC,
    finalize_reference_import,
    prepare_reference_import,
)
from tools.graphics.seedream_image import SeedreamImage
from tools.video import _shared

from tests.lib.look_lock_helpers import (
    CHAR,
    PROJECT,
    activate_look,
    approve_headshot,
    character_look,
    image,
    look_refs_for,
    pin_project,
    png_bytes,
)
from tests.tools._authored_film_helpers import make_verified_project, tiny_png_bytes



@pytest.fixture
def legacy(monkeypatch, tmp_path):
    """A registered, config-approved project with no pipeline pin (authored-film 1.1)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    return make_verified_project(tmp_path, monkeypatch, PROJECT)


@pytest.fixture
def governed(legacy):
    """The same project pinned to authored-film 1.2 (declares look_lock)."""
    pin_project(legacy, "1.2")
    return legacy


def _fake_download(url, dest, **kw):
    Path(dest).write_bytes(tiny_png_bytes())
    return {"bytes": 1, "content_type": "image/png"}


class Fal:
    """Mocked FAL surface that records whether anything was uploaded/submitted."""

    def __init__(self):
        self.uploads: list[str] = []
        self.submits: list[dict] = []

    def upload(self, path):
        self.uploads.append(path)
        return "https://v3.fal.media/up.png"

    def submit(self, model_id, payload, *, api_key, timeout_s=30.0):
        self.submits.append(payload)
        return {"request_id": "req-e2e"}

    def __enter__(self):
        self._patches = [
            patch.object(_shared, "upload_image_fal", side_effect=self.upload),
            patch.object(_shared, "fal_queue_submit", side_effect=self.submit),
            patch.object(_shared, "fal_queue_wait", return_value={"images": [{"url": "https://v3.fal.media/o.png"}], "seed": 7}),
            patch.object(_shared, "fal_download", side_effect=_fake_download),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def _run(project: Path, **inputs):
    base = {"prompt": "a wiry station engineer at the rail", "project_dir": str(project)}
    if inputs.get("look_refs") and "prompt_recipe" not in inputs and "prompt" not in inputs:
        # Round 2 #6: every generated image rendering of look_refs is a builder rendering
        # of the active look, carrying the rebuilt prompt_recipe.
        from tools.prompt_builder import build_prompt

        role = inputs.get("asset_role") or "hero"
        built = build_prompt(character_look(), role=role)
        base.update(prompt=built["prompt"], prompt_recipe=built["prompt_recipe"], asset_role=role)
    with Fal() as fal:
        result = SeedreamImage().execute({**base, **inputs})
    return result, fal


def _approve_import(project_dir: Path, prepared) -> dict:
    req = json.loads(prepared.request_path.read_text())
    with gates.handler_context():
        token = gates.mint_gate_token(PROJECT, req["stage"], req["scope"], record_sha256(req["approval_record"]))
    return receipts.record_human_approval(
        project_dir, PROJECT, req["stage"], req["scope"], req["approval_record"], token, "reference_import",
        entity_id=req["entity_id"], envelope=req["envelope"],
    )


def _import(project_dir: Path, seed: str, origin_class: str):
    staging = project_dir / ".staging"
    staging.mkdir(exist_ok=True)
    src = staging / f"{seed}.png"
    src.write_bytes(png_bytes(seed, size=(6, 6)))
    prepared = prepare_reference_import(
        project_dir, PROJECT, src, origin_class=origin_class,
        origin_tool="elsewhere-gen" if origin_class == ORIGIN_IMPORTED_SYNTHETIC else None,
    )
    receipt = _approve_import(project_dir, prepared)
    return finalize_reference_import(project_dir, receipt["receipt_id"])


# ---- (a) / (b): look_refs against the real look_lock chain ----------------------


def test_governed_call_with_active_look_passes_and_binds_look_refs(governed):
    c = character_look()
    activate_look(governed, c)
    assert _shared.project_look_governed(governed) is True
    result, fal = _run(governed, look_refs=look_refs_for(c))
    assert result.success, result.error
    assert fal.submits and fal.submits[0]["prompt"].lower().startswith("a wiry")
    row = receipts.find_generation(governed, result.data["asset_ids"][0])
    assert row is not None and row["look_refs"] == look_refs_for(c)
    # The rendering's rebuilt recipe is sealed into the receipt (round 2 #6).
    assert row["headshot_ref"] is None and row["prompt_recipe"]["look_hash"] == look_refs_for(c)[0]["look_hash"]
    assert result.metadata["look_refs"] == look_refs_for(c)


def test_governed_call_without_look_refs_is_refused(governed):
    result, fal = _run(governed)
    assert not result.success and "look_refs" in result.error
    assert fal.submits == [] and fal.uploads == []


def test_invalid_look_hash_is_refused_before_any_upload(governed):
    c = character_look()
    activate_look(governed, c)
    hero = image(governed, "hero-a", look_refs=look_refs_for(c))
    forged = [dict(look_refs_for(c)[0], look_hash="f" * 64)]
    result, fal = _run(
        governed, operation="edit", reference_image_paths=[str(governed / hero["path"])], look_refs=forged,
    )
    assert not result.success and "active look" in result.error, result.error
    assert fal.uploads == [] and fal.submits == []
    # An entity with no look_lock receipt at all is refused the same way.
    result, fal = _run(governed, look_refs=[{"entity_kind": "character", "entity_id": "char-09-nolook", "look_hash": "a" * 64}])
    assert not result.success and "no active look_lock receipt" in result.error
    assert fal.submits == []


# ---- (c): sheet roles against the real headshot chain ----------------------------


def test_sheet_call_with_active_headshot_passes_and_forged_refs_are_refused(governed):
    c = character_look()
    activate_look(governed, c)
    hero = image(governed, "hero-b", look_refs=look_refs_for(c))
    _, hs = approve_headshot(governed, c, hero["asset_id"], "d" * 64)
    headshot_ref = {"entity_id": CHAR, "asset_id": hero["asset_id"], "approval_receipt_id": hs["receipt_id"]}
    sheet = dict(operation="edit", reference_image_paths=[str(governed / hero["path"])],
                 look_refs=look_refs_for(c), asset_role="front")

    result, fal = _run(governed, **sheet, headshot_ref=headshot_ref)
    assert result.success, result.error
    assert fal.uploads == [str((governed / hero["path"]).resolve())]
    row = receipts.find_generation(governed, result.data["asset_ids"][0])
    assert row["headshot_ref"] == headshot_ref and row["look_refs"] == look_refs_for(c)
    assert row["references_applied"] == [{"asset_id": hero["asset_id"], "path": hero["path"], "role": "reference"}]

    result, fal = _run(governed, **sheet)  # missing headshot_ref
    assert not result.success and "headshot_ref" in result.error
    assert fal.uploads == []

    result, fal = _run(governed, **sheet, headshot_ref=dict(headshot_ref, approval_receipt_id="forged-receipt"))
    assert not result.success and "rejected or superseded" in result.error, result.error
    assert fal.uploads == []

    other = image(governed, "not-the-hero", look_refs=look_refs_for(c))
    result, fal = _run(governed, **sheet, headshot_ref=dict(headshot_ref, asset_id=other["asset_id"]))
    assert not result.success and "active headshot is asset" in result.error, result.error
    assert fal.uploads == []


# ---- (d) / (e): reference lineage through the real import gate ------------------


def test_casting_inspiration_reference_is_refused_before_upload(governed):
    c = character_look()
    activate_look(governed, c)
    tainted = _import(governed, "real-person-photo", ORIGIN_CASTING)
    assert tainted.path.is_file() and sha256_file(tainted.path) == tainted.normalized_pixel_hash
    result, fal = _run(
        governed, operation="edit", reference_image_paths=[str(tainted.path)], look_refs=look_refs_for(c),
    )
    assert not result.success and "real person" in result.error
    assert fal.uploads == [] and fal.submits == []
    assert not (governed / "cost-reservations.jsonl").exists()


def test_unreceipted_reference_is_refused_on_governed_call(governed):
    c = character_look()
    activate_look(governed, c)
    stray = governed / "assets" / "stray.png"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(png_bytes("stray"))
    result, fal = _run(governed, operation="edit", reference_image_paths=[str(stray)], look_refs=look_refs_for(c))
    assert not result.success and "no verified generation receipt" in result.error, result.error
    assert fal.uploads == []


def test_imported_synthetic_reference_passes_lineage(governed):
    c = character_look()
    activate_look(governed, c)
    imported = _import(governed, "made-elsewhere", ORIGIN_IMPORTED_SYNTHETIC)
    root = receipts.find_generation(governed, imported.normalized_pixel_hash)
    assert root["generator_kind"] == "imported" and root["receipt_id"] == imported.import_receipt_id
    result, fal = _run(
        governed, operation="edit", reference_image_paths=[str(imported.path)], look_refs=look_refs_for(c),
    )
    assert result.success, result.error
    assert fal.uploads == [str(imported.path.resolve())]
    row = receipts.find_generation(governed, result.data["asset_ids"][0])
    assert row["references_applied"][0]["asset_id"] == imported.normalized_pixel_hash
    # The new image's lineage now roots at the attested import.
    from lib.reference_import import verify_lineage

    assert verify_lineage(governed, row["output_sha256"]) == [row["output_sha256"], imported.normalized_pixel_hash]


# ---- (f): legacy projects are untouched ------------------------------------------


def test_unpinned_project_plain_call_is_untouched(legacy):
    assert _shared.project_look_governed(legacy) is False
    stray = legacy / "assets" / "ref.png"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(png_bytes("legacy-ref"))
    result, fal = _run(legacy, operation="edit", reference_image_paths=[str(stray)])
    assert result.success, result.error
    assert fal.uploads == [str(stray.resolve())]
    row = receipts.find_generation(legacy, result.data["asset_ids"][0])
    assert row["look_refs"] is None and row["headshot_ref"] is None and row["prompt_recipe"] is None
    assert result.data["look_refs"] is None

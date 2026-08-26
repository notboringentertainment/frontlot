"""Look governance at the generation boundary (plan D10 Slice A 5(b)/7, A′ 1–3).

All network mocked; lib-side verifiers (lib.look_ingest.verify_look_refs,
lib.headshots.verify_headshot_ref, lib.reference_import.tainted_hashes /
verify_lineage) are stand-ins installed per test. The un-stubbed path is
covered by tests/integration/test_look_governance_e2e.py.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import receipts
from lib.pathsafe import sha256_file
from tools.graphics.poster_composite import PosterComposite
from tools.graphics.seedream_image import SeedreamImage
from tools.graphics.title_card import TitleCard
from tools.video import _shared
from tools.video.kling_reference_video import KlingReferenceVideo

from tests.tools._authored_film_helpers import make_project, make_verified_project, tiny_png_bytes, write_receipted_png

LOOK_HASH = "a" * 64
CHAR_REF = {"entity_kind": "character", "entity_id": "quill-marrow", "look_hash": LOOK_HASH}
LOC_REF = {"entity_kind": "location", "entity_id": "fennick-light", "look_hash": "b" * 64}


class Verifiers:
    """Records every lib-side verification call; each can be told to refuse."""

    def __init__(self):
        self.look_calls: list[tuple] = []
        self.headshot_calls: list[tuple] = []
        self.lineage_calls: list[str] = []
        self.refuse_look = False
        self.refuse_headshot = False
        self.refuse_lineage = False
        self.tainted: set[str] = set()

    def verify_look_refs(self, project_root, look_refs, *, project_id=None):
        for r in look_refs:
            self.look_calls.append((Path(project_root).name, r["entity_kind"], r["entity_id"], r["look_hash"]))
            if self.refuse_look:
                raise ValueError(f"no active look_lock receipt for {r['entity_kind']}/{r['entity_id']}")
        return list(look_refs)

    def verify_headshot_ref(self, project_root, headshot_ref, *, project_id=None):
        self.headshot_calls.append((headshot_ref["entity_id"], headshot_ref["asset_id"], headshot_ref["approval_receipt_id"]))
        if self.refuse_headshot:
            raise ValueError("headshot receipt is not the ledger tip")
        return dict(headshot_ref)

    def tainted_hashes(self, project_root, *, project_id=None):
        return set(self.tainted)

    def verify_lineage(self, project_root, asset_id, *, receipts_by_sha=None, project_id=None, label="asset"):
        self.lineage_calls.append(asset_id)
        if self.refuse_lineage:
            raise ValueError("unrooted lineage")
        return [asset_id]


@pytest.fixture
def verifiers(monkeypatch):
    v = Verifiers()
    look_mod = types.ModuleType("lib.look_ingest")
    look_mod.verify_look_refs = v.verify_look_refs
    look_mod.project_look_governed = lambda root, **k: False  # stubbed projects are legacy unless keys say otherwise
    headshot_mod = types.ModuleType("lib.headshots")
    headshot_mod.verify_headshot_ref = v.verify_headshot_ref
    import_mod = types.ModuleType("lib.reference_import")
    import_mod.tainted_hashes = v.tainted_hashes
    import_mod.verify_lineage = v.verify_lineage
    monkeypatch.setitem(sys.modules, "lib.look_ingest", look_mod)
    monkeypatch.setitem(sys.modules, "lib.headshots", headshot_mod)
    monkeypatch.setitem(sys.modules, "lib.reference_import", import_mod)
    return v


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    return make_verified_project(tmp_path, monkeypatch, "proj-quill")


def _fake_download(url, dest, **kw):
    Path(dest).write_bytes(tiny_png_bytes())
    return {"bytes": 1, "content_type": "image/png"}


def _seedream_happy(captured):
    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["payload"] = payload
        return {"request_id": "req-g"}

    return (
        patch.object(_shared, "fal_queue_submit", side_effect=fake_submit),
        patch.object(_shared, "fal_queue_wait", return_value={"images": [{"url": "https://v3.fal.media/o.png"}]}),
        patch.object(_shared, "fal_download", side_effect=_fake_download),
    )


# ---- project governance detection ------------------------------------------


def test_legacy_project_without_marker_is_not_governed(env, verifiers):
    assert _shared.project_look_governed(env) is False
    assert _shared.verify_look_governance({"prompt": "p"}, env) == {
        "governed": False, "look_refs": None, "headshot_ref": None, "prompt_recipe": None,
    }
    assert verifiers.look_calls == []


def test_governance_comes_from_the_signed_pin_not_the_marker(env):
    import json

    from lib.pipeline_pin import PipelinePinError

    # Real lib: unpinned projects fall back to authored-film 1.1 (no look_lock).
    assert _shared.project_look_governed(env) is False
    (env / "project.json").write_text(json.dumps({"pipeline_type": "authored-film"}))
    assert _shared.project_look_governed(env) is False
    # A marker claiming 1.2 with no signed migration receipt fails closed.
    (env / "project.json").write_text(json.dumps({"pipeline_type": "authored-film", "pipeline_manifest_version": "1.2"}))
    with pytest.raises(PipelinePinError, match="disagrees with the signed pin"):
        _shared.project_look_governed(env)


def test_lib_side_detector_is_preferred(env, monkeypatch):
    mod = types.ModuleType("lib.look_ingest")
    mod.project_look_governed = lambda root: True
    mod.verify_look_refs = lambda *a, **k: list(a[1])
    monkeypatch.setitem(sys.modules, "lib.look_ingest", mod)
    assert _shared.project_look_governed(env) is True
    with pytest.raises(_shared.LookGovernanceError, match="look_refs"):
        _shared.verify_look_governance({"prompt": "p"}, env)


# ---- look_refs at the boundary ---------------------------------------------


def test_governed_call_requires_look_refs_and_verifies_each(env, verifiers):
    out = _shared.verify_look_governance({"look_refs": [CHAR_REF, LOC_REF]}, env)
    assert out["governed"] and out["look_refs"] == [CHAR_REF, LOC_REF]
    assert verifiers.look_calls == [
        ("proj-quill", "character", "quill-marrow", LOOK_HASH),
        ("proj-quill", "location", "fennick-light", "b" * 64),
    ]
    with pytest.raises(_shared.LookGovernanceError, match="look_refs"):
        _shared.verify_look_governance({"stage": "assets"}, env)
    with pytest.raises(_shared.LookGovernanceError, match="entity_kind"):
        _shared.verify_look_governance({"look_refs": [{"entity_kind": "prop", "entity_id": "x", "look_hash": LOOK_HASH}]}, env)
    with pytest.raises(_shared.LookGovernanceError, match="sha256"):
        _shared.verify_look_governance({"look_refs": [dict(CHAR_REF, look_hash="nope")]}, env)
    with pytest.raises(_shared.LookGovernanceError, match="twice"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF, dict(CHAR_REF, look_hash="c" * 64)]}, env)
    verifiers.refuse_look = True
    with pytest.raises(ValueError, match="no active look_lock"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF]}, env)


def test_missing_lib_verifier_fails_closed(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "lib.look_ingest", types.ModuleType("lib.look_ingest"))
    with pytest.raises(_shared.LookGovernanceError, match="verify_look_refs is unavailable"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF]}, env)
    monkeypatch.setitem(sys.modules, "lib.look_ingest", None)  # import fails
    with pytest.raises(_shared.LookGovernanceError, match="unavailable"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF]}, env)


def _scene_plan_checkpoint(status="completed", approved=True, entity_free=True):
    return {
        "status": status,
        "human_approved": approved,
        "artifacts": {"scene_plan": {"scenes": [
            {"scene_id": "sc-1", "entity_free": entity_free, "shots": [{"shot_id": "sh-title"}]},
            {"scene_id": "sc-2", "entity_free": False, "character_refs": ["quill-marrow"], "shots": [{"shot_id": "sh-2"}]},
        ]}},
    }


def test_entity_free_is_read_from_approved_scene_plan_only(env, verifiers, monkeypatch):
    import lib.checkpoint as cp

    calls = []

    def fake_read(pipeline_dir, project_id, stage):
        calls.append((Path(pipeline_dir), project_id, stage))
        return _scene_plan_checkpoint()

    monkeypatch.setattr(cp, "read_checkpoint", fake_read)
    out = _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)
    assert out["look_refs"] == [] and out["governed"]
    assert calls == [(env.parent, "proj-quill", "scene_plan")]
    # A shot in a scene with entities is not entity-free.
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-2"}, env)
    # Unknown shot id.
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-nope"}, env)
    # The tool-call boolean is never trusted — it is refused outright.
    with pytest.raises(_shared.LookGovernanceError, match="entity_free is not a tool-call input"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title", "entity_free": True}, env)
    # Not approved / not completed / missing checkpoint → not entity-free.
    for cpt in (_scene_plan_checkpoint(approved=False), _scene_plan_checkpoint(status="awaiting_human"), None):
        monkeypatch.setattr(cp, "read_checkpoint", lambda *a, _c=cpt: _c)
        with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
            _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)


def test_visual_bible_calls_can_never_be_entity_free(env, verifiers, monkeypatch):
    import lib.checkpoint as cp

    monkeypatch.setattr(cp, "read_checkpoint", lambda *a: _scene_plan_checkpoint())
    with pytest.raises(_shared.LookGovernanceError, match="never be entity-free"):
        _shared.verify_look_governance({"stage": "visual_bible", "shot_id": "sh-title"}, env)


# ---- headshot_ref for sheet roles --------------------------------------------


HEADSHOT = {"entity_id": "quill-marrow", "asset_id": "d" * 64, "approval_receipt_id": "hs-1"}


@pytest.mark.parametrize("role", sorted(_shared.SHEET_ASSET_ROLES))
def test_sheet_roles_require_headshot_ref(env, verifiers, role):
    with pytest.raises(_shared.LookGovernanceError, match="headshot_ref"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": role}, env)
    assert verifiers.headshot_calls == []
    out = _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": role, "headshot_ref": HEADSHOT}, env)
    assert out["headshot_ref"] == HEADSHOT
    assert verifiers.headshot_calls == [("quill-marrow", "d" * 64, "hs-1")]


def test_headshot_ref_must_match_a_character_look_and_verify(env, verifiers):
    assert _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": "hero"}, env)["headshot_ref"] is None
    with pytest.raises(_shared.LookGovernanceError, match="not a character named in look_refs"):
        _shared.verify_look_governance({"look_refs": [LOC_REF], "asset_role": "front", "headshot_ref": HEADSHOT}, env)
    with pytest.raises(_shared.LookGovernanceError, match="sha256"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": "front", "headshot_ref": dict(HEADSHOT, asset_id="x")}, env)
    verifiers.refuse_headshot = True
    with pytest.raises(ValueError, match="ledger tip"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": "front", "headshot_ref": HEADSHOT}, env)


def test_missing_verify_headshot_ref_fails_closed(env, verifiers, monkeypatch):
    monkeypatch.setitem(sys.modules, "lib.headshots", types.ModuleType("lib.headshots"))
    with pytest.raises(_shared.LookGovernanceError, match="verify_headshot_ref is unavailable"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": "front", "headshot_ref": HEADSHOT}, env)


# ---- prompt recipe on visual_bible ---------------------------------------------


def test_visual_bible_requires_matching_prompt_recipe(env, verifiers):
    from tools.prompt_builder import rendered_prompt_sha256

    prompt = "a weathered keeper. hair salt-grey braid. single character."
    recipe = {"look_hash": LOOK_HASH, "builder_version": "1.0", "fields_used": ["prompt_safe_description"],
              "rendered_sha256": rendered_prompt_sha256(prompt)}
    base = {"stage": "visual_bible", "look_refs": [CHAR_REF], "asset_role": "hero", "prompt": prompt}
    with pytest.raises(_shared.LookGovernanceError, match="prompt_recipe"):
        _shared.verify_look_governance(base, env)
    out = _shared.verify_look_governance({**base, "prompt_recipe": recipe}, env)
    assert out["prompt_recipe"] == recipe
    with pytest.raises(_shared.LookGovernanceError, match="rendered_sha256"):
        _shared.verify_look_governance({**base, "prompt": prompt + " extra words", "prompt_recipe": recipe}, env)
    with pytest.raises(_shared.LookGovernanceError, match="not one of the verified look_refs"):
        _shared.verify_look_governance({**base, "prompt_recipe": dict(recipe, look_hash="e" * 64)}, env)


# ---- lineage at the upload boundary --------------------------------------------


def test_reference_lineage_rejects_remote_urls_and_tainted_or_unrooted_refs(env, verifiers):
    good = write_receipted_png(env, "canon/visual/objects/good.png")
    _shared.verify_reference_lineage({}, env, [good["path"]], governed=True)
    assert verifiers.lineage_calls == [good["sha256"]]
    for key in _shared.REMOTE_REFERENCE_KEYS:
        with pytest.raises(_shared.LookGovernanceError, match=key):
            _shared.verify_reference_lineage({key: ["https://v3.fal.media/x.png"]}, env, [], governed=True)
    verifiers.tainted.add(good["sha256"])
    with pytest.raises(_shared.LookGovernanceError, match="real person"):
        _shared.verify_reference_lineage({}, env, [good["path"]], governed=True)
    verifiers.tainted.clear()
    verifiers.refuse_lineage = True
    with pytest.raises(_shared.LookGovernanceError, match="unrooted lineage"):
        _shared.verify_reference_lineage({}, env, [good["path"]], governed=True)
    # Legacy (non-governed) calls are untouched.
    _shared.verify_reference_lineage({"reference_image_urls": ["https://x/y.png"]}, env, [good["path"]], governed=False)


def test_missing_lineage_verifiers_fail_closed(env, monkeypatch):
    good = write_receipted_png(env, "canon/visual/objects/good.png")
    monkeypatch.setitem(sys.modules, "lib.reference_import", types.ModuleType("lib.reference_import"))
    with pytest.raises(_shared.LookGovernanceError, match="tainted_hashes is unavailable"):
        _shared.verify_reference_lineage({}, env, [good["path"]], governed=True)


# ---- tools end to end (mocked FAL) ---------------------------------------------


def test_seedream_governed_call_binds_refs_and_lineage_before_upload(env, verifiers):
    hero = write_receipted_png(env, "canon/visual/objects/hero.png")
    captured: dict = {}
    staged: dict = {}
    real_stage = receipts.stage_generation_wal

    def spy_stage(project_root, **kw):
        staged.update(kw["receipt"])
        return real_stage(project_root, **kw)

    headshot = dict(HEADSHOT, asset_id=hero["sha256"])
    inputs = {
        "prompt": "front sheet", "operation": "edit", "project_dir": str(env),
        "reference_image_paths": [str(hero["path"])],
        "look_refs": [CHAR_REF], "asset_role": "front", "headshot_ref": headshot, "stage": "assets",
    }
    order = []
    submit, wait, download = _seedream_happy(captured)
    with patch.object(_shared, "upload_image_fal", side_effect=lambda p: order.append("upload") or "https://v3.fal.media/up.png"), \
         submit, wait, download, patch.object(receipts, "stage_generation_wal", side_effect=spy_stage):
        r = SeedreamImage().execute(inputs)
    assert r.success, r.error
    assert verifiers.look_calls and verifiers.headshot_calls and verifiers.lineage_calls == [hero["sha256"]]
    assert order == ["upload"]
    expected_refs = [{"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png", "role": "reference"}]
    assert staged["references_applied"] == expected_refs
    assert staged["look_refs"] == [CHAR_REF] and staged["headshot_ref"] == headshot
    assert r.metadata["look_refs"] == [CHAR_REF] and r.metadata["references_applied"] == expected_refs
    row = receipts.find_generation(env, r.data["asset_ids"][0])
    assert row["references_applied"] == expected_refs
    assert row["look_refs"] == [CHAR_REF] and row["headshot_ref"] == headshot


def test_seedream_refuses_before_upload_on_governance_failure(env, verifiers):
    hero = write_receipted_png(env, "canon/visual/objects/hero.png")
    cases = [
        ({"look_refs": [CHAR_REF], "asset_role": "front"}, "headshot_ref"),
        ({"stage": "visual_bible"}, "never be entity-free"),
        ({"look_refs": [CHAR_REF], "reference_image_urls": ["https://v3.fal.media/x.png"]}, "reference_image_urls refused"),
    ]
    for extra, msg in cases:
        with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
            r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(env),
                                         "reference_image_paths": [str(hero["path"])], **extra})
        assert not r.success and msg in r.error, (extra, r.error)
        up.assert_not_called()
        submit.assert_not_called()
    verifiers.tainted.add(hero["sha256"])
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(env),
                                     "reference_image_paths": [str(hero["path"])], "look_refs": [CHAR_REF]})
    assert not r.success and "real person" in r.error
    up.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_seedream_legacy_receipt_records_references_applied(env):
    ref = env / "assets" / "images" / "ref.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(tiny_png_bytes())
    captured: dict = {}
    submit, wait, download = _seedream_happy(captured)
    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/up.png"), submit, wait, download:
        r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(env),
                                     "reference_image_paths": [str(ref)],
                                     "reference_image_urls": ["https://v3.fal.media/other.png"]})
    assert r.success, r.error
    import hashlib

    row = receipts.find_generation(env, r.data["asset_ids"][0])
    assert row["references_applied"] == [
        {"asset_id": hashlib.sha256(b"https://v3.fal.media/other.png").hexdigest(), "role": "remote_url"},
        {"asset_id": sha256_file(ref), "path": "assets/images/ref.png", "role": "reference"},
    ]
    assert row.get("look_refs") is None


def test_kling_governed_call_rejects_remote_urls_and_binds_look_refs(env, verifiers):
    hero = write_receipted_png(env, "canon/visual/objects/hero.png")
    manifest = [{"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png", "role": "hero",
                 "visual_bible_entity_id": "quill-marrow"}]
    (env / "renders").mkdir()
    base = {"prompt": "@Image1 walks", "project_dir": str(env), "output_path": str(env / "renders" / "t.mp4"),
            "reference_image_paths": [str(hero["path"])], "reference_manifest": manifest, "look_refs": [CHAR_REF]}
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute({**base, "reference_image_urls": ["https://v3.fal.media/x.png"]})
    assert not r.success and "reference_image_urls refused" in r.error
    up.assert_not_called()
    submit.assert_not_called()

    staged: dict = {}
    real_stage = receipts.stage_generation_wal

    def spy_stage(project_root, **kw):
        staged.update(kw["receipt"])
        return real_stage(project_root, **kw)

    def fake_download(url, dest, **kw):
        Path(dest).write_bytes(b"video")
        return {"bytes": 5, "content_type": "video/mp4"}

    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/up.png"), \
         patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-k"}), \
         patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/o.mp4"}}), \
         patch.object(_shared, "fal_download", side_effect=fake_download), \
         patch.object(_shared, "verify_video_file", return_value={"duration_seconds": 5.0, "has_audio": False}), \
         patch.object(receipts, "stage_generation_wal", side_effect=spy_stage):
        r = KlingReferenceVideo().execute(base)
    assert r.success, r.error
    assert verifiers.lineage_calls == [hero["sha256"]]
    assert staged["look_refs"] == [CHAR_REF] and r.metadata["look_refs"] == [CHAR_REF]


@pytest.fixture
def font(tmp_path, monkeypatch):
    from PIL import ImageFont

    font_bytes = getattr(ImageFont.load_default(size=12), "font_bytes", None)
    if not font_bytes:
        pytest.skip("Pillow build has no bundled TrueType font")
    project = make_project(tmp_path, monkeypatch, "proj-lantern")
    path = project / "assets" / "fonts" / "test-font.ttf"
    path.parent.mkdir(parents=True)
    path.write_bytes(font_bytes)
    return project, path


def test_title_card_and_poster_are_governed(font, verifiers, monkeypatch):
    project, font_path = font
    import lib.checkpoint as cp

    monkeypatch.setattr(cp, "read_checkpoint", lambda *a: _scene_plan_checkpoint())
    base = {"text": "THE FIRST LIGHT", "font_path": str(font_path), "font_size": 32, "width": 320, "height": 180,
            "project_dir": str(project)}
    r = TitleCard().execute({**base, "stage": "assets"})
    assert not r.success and "look_refs" in r.error
    r = TitleCard().execute({**base, "stage": "assets", "shot_id": "sh-title"})
    assert r.success, r.error
    assert r.metadata["look_refs"] == []  # entity-free by the approved plan: bound as an explicit empty list
    r = TitleCard().execute({**base, "look_refs": [LOC_REF]})
    assert r.success, r.error
    assert r.metadata["look_refs"] == [LOC_REF]
    assert receipts.find_generation(project, r.data["asset_id"])["look_refs"] == [LOC_REF]
    title_card = r.data["output_path"]

    key_art = write_receipted_png(project, "canon/visual/objects/key.png")
    poster = {"key_art_path": str(key_art["path"]), "title_card_path": title_card, "project_dir": str(project)}
    r = PosterComposite().execute({**poster, "stage": "assets"})
    assert not r.success and "look_refs" in r.error
    r = PosterComposite().execute({**poster, "look_refs": [CHAR_REF, LOC_REF]})
    assert r.success, r.error
    assert sorted(verifiers.lineage_calls)[-2:] == sorted([key_art["sha256"], sha256_file(Path(title_card))])
    assert receipts.find_generation(project, r.data["asset_id"])["look_refs"] == [CHAR_REF, LOC_REF]
    verifiers.tainted.add(key_art["sha256"])
    r = PosterComposite().execute({**poster, "look_refs": [CHAR_REF]})
    assert not r.success and "real person" in r.error

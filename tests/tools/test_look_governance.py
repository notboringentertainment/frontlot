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
from tests.tools.test_prompt_builder import _character as _character_look

LOOK_HASH = "a" * 64
CHAR_REF = {"entity_kind": "character", "entity_id": "quill-marrow", "look_hash": LOOK_HASH}


class _ActiveLook:
    def __init__(self, entity_kind, entity_id, payload):
        from lib.canonical_json import record_sha256

        self.entity_kind, self.entity_id, self.payload = entity_kind, entity_id, payload
        self.look_hash = record_sha256(payload)
        self.receipt_id = "rcpt-look"
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
        self.active: dict[tuple[str, str], _ActiveLook] = {}

    def active_look_for(self, project_root, entity_kind, entity_id, *, project_id=None):
        return self.active.get((entity_kind, entity_id))

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
    look_mod.active_look_for = v.active_look_for
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
    # Distinct from tiny_png_bytes(): provenance is immutable per output hash and
    # an output can never be its own parent, so the "generated" pixels must not
    # collide with the reference fixture.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 40, 90)).save(buf, format="PNG")
    Path(dest).write_bytes(buf.getvalue())
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
            {"id": "sc-1", "entity_free": entity_free, "shots": [{"shot_id": "sh-title"}]},
            {"id": "sc-2", "entity_free": False, "character_refs": ["quill-marrow"], "shots": [{"shot_id": "sh-2"}]},
        ]}},
    }


def _entity_free_from_lib(monkeypatch, scene_ids, *, checkpoint=None, seen=None):
    """Stub the lib-side authority (``lib.checkpoint.entity_free_scene_ids``) and the
    on-disk plan the boundary reads only for the shot -> scene mapping."""
    import lib.checkpoint as cp

    def fake_ids(project_dir):
        if seen is not None:
            seen.append(Path(project_dir))
        return set(scene_ids)

    monkeypatch.setattr(cp, "entity_free_scene_ids", fake_ids, raising=False)
    monkeypatch.setattr(cp, "read_checkpoint", lambda *a: _scene_plan_checkpoint() if checkpoint is None else checkpoint)


def test_entity_free_is_decided_only_by_the_lib_scene_id_authority(env, verifiers, monkeypatch):
    """Round 2 #7: the boundary asks ``lib.checkpoint.entity_free_scene_ids`` (completed,
    non-invalidated, pin-matching, receipt-bound scene_plan) and maps shot -> scene."""
    seen: list[Path] = []
    _entity_free_from_lib(monkeypatch, {"sc-1"}, seen=seen)
    out = _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)
    assert out["look_refs"] == [] and out["governed"]
    assert seen == [env]
    # A shot in a scene the authority does not list is not entity-free.
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-2"}, env)
    # Unknown shot id.
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-nope"}, env)
    # The tool-call boolean is never trusted — it is refused outright.
    with pytest.raises(_shared.LookGovernanceError, match="entity_free is not a tool-call input"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title", "entity_free": True}, env)


def test_edited_unsigned_checkpoint_claiming_entity_free_is_refused(env, verifiers, monkeypatch):
    """Round 2 #7: the checkpoint's own status / human_approved / entity_free fields are
    never authorization. A locally edited plan that claims entity_free for sc-1 while the
    receipt-bound authority lists nothing authorizes nothing."""
    import lib.checkpoint as cp

    edited = _scene_plan_checkpoint(status="completed", approved=True, entity_free=True)
    _entity_free_from_lib(monkeypatch, set(), checkpoint=edited)
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)
    # The lib authority listing a scene the edited plan does not contain maps nothing either.
    _entity_free_from_lib(monkeypatch, {"sc-9"}, checkpoint=edited)
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)
    # A lib-side failure is not authorization.
    def boom(project_dir):
        raise cp.CheckpointValidationError("ledger unreadable")

    monkeypatch.setattr(cp, "entity_free_scene_ids", boom, raising=False)
    with pytest.raises(_shared.LookGovernanceError, match="not entity_free"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)


def test_missing_lib_entity_free_authority_fails_closed(env, verifiers, monkeypatch):
    import lib.checkpoint as cp

    monkeypatch.setattr(cp, "read_checkpoint", lambda *a: _scene_plan_checkpoint())
    monkeypatch.delattr(cp, "entity_free_scene_ids", raising=False)
    with pytest.raises(_shared.LookGovernanceError, match="entity_free_scene_ids is unavailable"):
        _shared.verify_look_governance({"stage": "assets", "shot_id": "sh-title"}, env)


def test_visual_bible_calls_can_never_be_entity_free(env, verifiers, monkeypatch):
    _entity_free_from_lib(monkeypatch, {"sc-1"})
    with pytest.raises(_shared.LookGovernanceError, match="never be entity-free"):
        _shared.verify_look_governance({"stage": "visual_bible", "shot_id": "sh-title"}, env)


# ---- headshot_ref for sheet roles --------------------------------------------


HEADSHOT = {"entity_id": "quill-marrow", "asset_id": "d" * 64, "approval_receipt_id": "hs-1"}


def rendering_inputs(verifiers, role="hero", *, palette=None, **overrides):
    """A builder rendering of the active character look: ``look_refs`` naming the
    activated look, ``asset_role``, the rendered ``prompt`` and its ``prompt_recipe``
    (round 2 #6: every generated rendering of look_refs carries a rebuilt recipe)."""
    from tools.prompt_builder import build_prompt

    payload = _character_look()
    key = ("character", payload["entity_id"])
    look = verifiers.active.get(key) or _activate(verifiers, payload)
    built = build_prompt(look.payload, role=role, palette=palette)
    ref = {"entity_kind": "character", "entity_id": payload["entity_id"], "look_hash": look.look_hash}
    out = {"look_refs": [ref], "asset_role": role, "prompt": built["prompt"], "prompt_recipe": built["prompt_recipe"]}
    if palette is not None:
        out["palette"] = list(palette)
    out.update(overrides)
    return out


@pytest.mark.parametrize("role", sorted(_shared.SHEET_ASSET_ROLES))
def test_sheet_roles_require_headshot_ref(env, verifiers, role):
    with pytest.raises(_shared.LookGovernanceError, match="headshot_ref"):
        _shared.verify_look_governance({"look_refs": [CHAR_REF], "asset_role": role}, env)
    assert verifiers.headshot_calls == []
    out = _shared.verify_look_governance({**rendering_inputs(verifiers, role), "headshot_ref": HEADSHOT}, env)
    assert out["headshot_ref"] == HEADSHOT
    assert verifiers.headshot_calls == [("quill-marrow", "d" * 64, "hs-1")]


def test_headshot_ref_must_match_a_character_look_and_verify(env, verifiers):
    assert _shared.verify_look_governance(rendering_inputs(verifiers, "hero"), env)["headshot_ref"] is None
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


def _activate(verifiers, payload, entity_kind="character"):
    look = _ActiveLook(entity_kind, payload["entity_id"], payload)
    verifiers.active[(entity_kind, payload["entity_id"])] = look
    return look


def test_visual_bible_requires_matching_prompt_recipe(env, verifiers):
    from tools.prompt_builder import build_prompt

    look_a = _activate(verifiers, _character_look())
    built = build_prompt(look_a.payload, role="hero", palette=["granite grey"])
    ref = {"entity_kind": "character", "entity_id": "quill-marrow", "look_hash": look_a.look_hash}
    base = {"stage": "visual_bible", "look_refs": [ref], "asset_role": "hero", "palette": ["granite grey"],
            "prompt": built["prompt"]}
    with pytest.raises(_shared.LookGovernanceError, match="prompt_recipe"):
        _shared.verify_look_governance(base, env)
    out = _shared.verify_look_governance({**base, "prompt_recipe": built["prompt_recipe"]}, env)
    assert out["prompt_recipe"] == built["prompt_recipe"]
    with pytest.raises(_shared.LookGovernanceError, match="rendered_sha256"):
        _shared.verify_look_governance({**base, "prompt": built["prompt"] + " extra words",
                                        "prompt_recipe": built["prompt_recipe"]}, env)
    with pytest.raises(_shared.LookGovernanceError, match="not one of the verified look_refs"):
        _shared.verify_look_governance({**base, "prompt_recipe": dict(built["prompt_recipe"], look_hash="e" * 64)}, env)
    # The boundary rebuilds with the call's role/palette: a different palette or role does not rebuild.
    with pytest.raises(_shared.LookGovernanceError, match="does not rebuild from the active look"):
        _shared.verify_look_governance({**base, "palette": ["rust orange"], "prompt_recipe": built["prompt_recipe"]}, env)
    with pytest.raises(_shared.LookGovernanceError, match="asset_role"):
        _shared.verify_look_governance({**base, "asset_role": None, "prompt_recipe": built["prompt_recipe"]}, env)
    with pytest.raises(_shared.LookGovernanceError, match="builder_version"):
        _shared.verify_look_governance({**base, "prompt_recipe": dict(built["prompt_recipe"], builder_version="0.9")}, env)


def test_every_generated_rendering_of_look_refs_requires_a_recipe(env, verifiers, monkeypatch):
    """Round 2 #6: a headshot-style call (look_refs + prompt, not visual_bible) without a
    rebuilt recipe is refused; the same call with the builder's recipe passes."""
    rendered = rendering_inputs(verifiers, "hero", stage="headshots")
    headshot = {k: v for k, v in rendered.items() if k != "prompt_recipe"}
    with pytest.raises(_shared.LookGovernanceError, match="requires prompt_recipe"):
        _shared.verify_look_governance(headshot, env)
    # No stage at all, and no asset_role at all: still a rendering of look_refs.
    with pytest.raises(_shared.LookGovernanceError, match="requires prompt_recipe"):
        _shared.verify_look_governance({k: v for k, v in headshot.items() if k not in ("stage", "asset_role")}, env)
    # asset_role without a prompt is still a rendering call.
    with pytest.raises(_shared.LookGovernanceError, match="requires prompt_recipe"):
        _shared.verify_look_governance({"look_refs": rendered["look_refs"], "asset_role": "hero"}, env)
    out = _shared.verify_look_governance(rendered, env)
    assert out["prompt_recipe"] == rendered["prompt_recipe"]
    # A recipe that does not rebuild from the active look is refused even off visual_bible.
    with pytest.raises(_shared.LookGovernanceError, match="rendered_sha256"):
        _shared.verify_look_governance({**rendered, "prompt": rendered["prompt"] + " and a hat"}, env)
    # Video renderings are exempt (the builder has no motion roles); image is the default.
    assert _shared.verify_look_governance(headshot, env, media="video")["prompt_recipe"] is None
    with pytest.raises(_shared.LookGovernanceError, match="media"):
        _shared.verify_look_governance(headshot, env, media="audio")
    # A planned shot render (shot_id in the scene plan) is the shot's prompt, not an appearance.
    import lib.checkpoint as cp

    monkeypatch.setattr(cp, "read_checkpoint", lambda *a: _scene_plan_checkpoint())
    shot = {"look_refs": rendered["look_refs"], "prompt": "she crosses the gantry", "shot_id": "sh-2"}
    assert _shared.verify_look_governance(shot, env)["prompt_recipe"] is None
    with pytest.raises(_shared.LookGovernanceError, match="not a shot of the scene_plan"):
        _shared.verify_look_governance({**shot, "shot_id": "sh-invented"}, env)


def test_imported_synthetic_candidate_may_omit_the_recipe_only_when_attested(env, verifiers):
    ref = rendering_inputs(verifiers, "hero")["look_refs"]
    base = {"look_refs": ref, "asset_role": "hero", "origin": "imported_synthetic"}
    out = _shared.verify_look_governance({**base, "import_receipt_id": "imp-7"}, env)
    assert out["prompt_recipe"] is None and out["look_refs"] == ref
    with pytest.raises(_shared.LookGovernanceError, match="import_receipt_id"):
        _shared.verify_look_governance(base, env)
    with pytest.raises(_shared.LookGovernanceError, match="import_receipt_id"):
        _shared.verify_look_governance({**base, "import_receipt_id": ""}, env)
    # An import is not generated: a prompt on it is refused, as is any other origin.
    with pytest.raises(_shared.LookGovernanceError, match="not generated"):
        _shared.verify_look_governance({**base, "import_receipt_id": "imp-7", "prompt": "p"}, env)
    with pytest.raises(_shared.LookGovernanceError, match="origin"):
        _shared.verify_look_governance({**base, "origin": "generated", "import_receipt_id": "imp-7"}, env)


def test_prompt_rendered_from_appearance_b_under_look_a_hash_is_refused(env, verifiers):
    """Inspection #2: the recipe cannot launder appearance B under look A's hash."""
    from tools.prompt_builder import build_prompt

    look_a = _activate(verifiers, _character_look())
    appearance_b = _character_look(hair="cropped white hair", distinguishing_marks=["gold tooth"])
    built_b = build_prompt(appearance_b, role="hero")
    forged = dict(built_b["prompt_recipe"], look_hash=look_a.look_hash)  # hash claims A, text renders B
    ref = {"entity_kind": "character", "entity_id": "quill-marrow", "look_hash": look_a.look_hash}
    inputs = {"stage": "visual_bible", "look_refs": [ref], "asset_role": "hero",
              "prompt": built_b["prompt"], "prompt_recipe": forged}
    with pytest.raises(_shared.LookGovernanceError, match="does not rebuild from the active look"):
        _shared.verify_look_governance(inputs, env)
    # A recipe naming a hash that is no longer / not the active tip is refused too.
    verifiers.active.clear()
    with pytest.raises(_shared.LookGovernanceError, match="not the active look"):
        _shared.verify_look_governance(inputs, env)
    # And the missing lib-side lookup fails closed.
    import sys

    del sys.modules["lib.look_ingest"].active_look_for
    with pytest.raises(_shared.LookGovernanceError):
        _shared.verify_look_governance(inputs, env)


def test_seedream_refuses_forged_recipe_before_upload(env, verifiers):
    from tools.prompt_builder import build_prompt

    look_a = _activate(verifiers, _character_look())
    built_b = build_prompt(_character_look(hair="cropped white hair"), role="hero")
    ref = {"entity_kind": "character", "entity_id": "quill-marrow", "look_hash": look_a.look_hash}
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as upload:
        r = SeedreamImage().execute({
            "prompt": built_b["prompt"], "project_dir": str(env), "stage": "visual_bible", "asset_role": "hero",
            "look_refs": [ref], "prompt_recipe": dict(built_b["prompt_recipe"], look_hash=look_a.look_hash),
            "output_path": str(env / "canon" / "visual" / "objects" / "hero.png"),
        })
    assert not r.success and "does not rebuild from the active look" in r.error
    submit.assert_not_called()
    upload.assert_not_called()


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
    rendered = rendering_inputs(verifiers, "front")
    CHAR_REF = rendered["look_refs"][0]  # noqa: N806 — the ref of the activated look
    inputs = {
        **rendered, "operation": "edit", "project_dir": str(env),
        "reference_image_paths": [str(hero["path"])], "headshot_ref": headshot, "stage": "assets",
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
    rendered = rendering_inputs(verifiers, "hero")
    cases = [
        ({"look_refs": [CHAR_REF], "asset_role": "front"}, "headshot_ref"),
        ({"stage": "visual_bible"}, "never be entity-free"),
        ({"look_refs": [CHAR_REF]}, "requires prompt_recipe"),
        ({**rendered, "reference_image_urls": ["https://v3.fal.media/x.png"]}, "reference_image_urls refused"),
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
        r = SeedreamImage().execute({**rendered, "operation": "edit", "project_dir": str(env),
                                     "reference_image_paths": [str(hero["path"])]})
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
    _entity_free_from_lib(monkeypatch, {"sc-1"})
    base = {"text": "THE FIRST LIGHT", "font_path": str(font_path), "font_size": 32, "width": 320, "height": 180,
            "project_dir": str(project)}
    r = TitleCard().execute({**base, "stage": "assets"})
    assert not r.success and "look_refs" in r.error
    r = TitleCard().execute({**base, "stage": "assets", "shot_id": "sh-title"})
    assert r.success, r.error
    assert r.metadata["look_refs"] == []  # entity-free by the approved plan: bound as an explicit empty list
    # A different card: provenance is immutable per output hash, so the same
    # pixels cannot be re-receipted under different look_refs.
    r = TitleCard().execute({**base, "text": "THE FIRST LIGHT — PART TWO", "look_refs": [LOC_REF]})
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

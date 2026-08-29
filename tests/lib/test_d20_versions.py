"""D20 step 1 — versions: authored-film 1.4 manifest, the separate HERO policy
bundle (sheet bundle frozen), project_config 1.2, headshot_packet 1.1,
headshot record 1.1, the reference-import split with character binding.
Invented names only."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.headshots import (
    HEADSHOT_RECORD_FIELDS, HEADSHOT_RECORD_FIELDS_1_1, HeadshotError, headshot_record, record_version_of,
)
from lib.pipeline_loader import load_pipeline, manifest_digest, manifest_versions
from lib.project_config import ProjectConfigError, VerifiedProjectConfig
from lib.reference_import import (
    ORIGIN_CASTING, ORIGIN_IMPORTED_SYNTHETIC, ReferenceImportError, entity_claiming_hash, import_record,
    normalize_reference_import, prepare_reference_import, publish_import_request, record_entity_id,
    synthetic_import_receipt, validate_import_record,
)
from lib.sheet_qc import local_checks, policy
from schemas.artifacts import validate_artifact, validate_project_config

# Golden values. The sheet bundle hash is what Bloodless signed on
# 2026-08-27; the legacy director is byte-frozen (R2#1). Changing either is
# a policy change that needs a new human signature, not a silent edit.
SHEET_BUNDLE_SHA256 = "f3709bd44037b4f1410149a07d3f831681b0da43a1bd739b792d9639a8a408f3"
LEGACY_DIRECTOR_SHA256 = "89782fdf439b5e84dcf002d4efa481f3ee2170fd9458b61004391a04b8235d72"
MANIFEST_1_3_SHA256 = "ff541f0b3e7a3ce966166112f3da4df7eec6a9881d40f121e0eac7f13a609cd3"
ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64


# ---- policy bundles ----

class TestPolicyBundles:
    def test_sheet_bundle_hash_is_frozen(self):
        assert policy.bundle_sha256() == SHEET_BUNDLE_SHA256
        assert policy.sheet_bundle_sha256() == SHEET_BUNDLE_SHA256
        assert "hero" not in policy.bundle()["checklists"]
        assert "hero" not in policy.LOCAL_RULES["size"]

    def test_hero_bundle_is_separate(self):
        assert policy.hero_bundle_sha256() != SHEET_BUNDLE_SHA256
        b = policy.hero_bundle()
        assert list(b["checklists"]) == ["hero"] and list(b["response_schemas"]) == ["hero"]
        assert b["local_rules"] is policy.HERO_LOCAL_RULES
        assert policy.bundle_sha256_for_role("hero") == policy.hero_bundle_sha256()
        assert policy.bundle_sha256_for_role("turnaround") == SHEET_BUNDLE_SHA256
        ids = [i.id for i in policy.checklist("hero")]
        assert ids == ["single_subject", "bust_front", "neutral_expression", "plain_background", "no_occlusion",
                       "hair_matches", "age_matches", "build_matches", "no_text", "photoreal_or_treatment"]
        sev = {i.id: i.severity for i in policy.checklist("hero")}
        assert sev["age_matches"] == sev["build_matches"] == sev["photoreal_or_treatment"] == "warn"
        assert all(i.evidence in policy.EVIDENCE_KINDS for i in policy.checklist("hero"))

    def test_look_evidence_reads_signed_look_fields_only(self):
        ev = policy.look_evidence("hero", {"hair": "close-cropped grey", "age_band": "50s", "extra": "ignored"})
        assert ev == {"look_hair": "close-cropped grey", "look_age_band": "50s", "look_build": ""}
        prompt = policy.judge_prompt("hero", wardrobe_pieces=None, has_hero=False, look_fields=ev)
        assert "Described hair: close-cropped grey." in prompt and "Described build: (not described)." in prompt
        assert "reference face" in prompt and "hero" in prompt.splitlines()[0]
        assert policy.judge_prompt("turnaround", wardrobe_pieces=["coat"], has_hero=True).startswith("Sheet role: turnaround.")

    def test_unknown_role_still_refused(self):
        with pytest.raises(KeyError):
            policy.checklist("front")


def _png(w: int, h: int, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), (10, 20, 30) if mode == "RGB" else (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


class TestHeroLocalRules:
    def test_hero_accepts_square_and_portrait_rejects_landscape_small_alpha(self):
        assert local_checks.check_image("hero", _png(1024, 1024)) == {"width": 1024, "height": 1024}
        assert local_checks.check_image("hero", _png(1024, 1536))["height"] == 1536
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("hero", _png(1536, 1024))
        assert exc.value.item == "orientation"
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("hero", _png(800, 800))
        assert exc.value.item == "size"
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("hero", _png(1024, 1024, "RGBA"))
        assert exc.value.item == "alpha"

    def test_sheet_rules_unchanged(self):
        with pytest.raises(local_checks.LocalCheckError) as exc:
            local_checks.check_image("expressions", _png(1024, 1536))
        assert exc.value.item == "aspect"
        assert local_checks.check_image("turnaround", _png(2560, 1600))["width"] == 2560


# ---- manifest 1.4 ----

class TestManifest14:
    def test_versions_and_1_3_frozen(self):
        assert manifest_versions("authored-film") == ["1.1", "1.2", "1.3", "1.4"]
        assert manifest_digest("authored-film@1.3") == MANIFEST_1_3_SHA256
        assert hashlib.sha256((ROOT / "skills/pipelines/authored-film/headshots-director.md").read_bytes()).hexdigest() == LEGACY_DIRECTOR_SHA256
        assert (ROOT / "skills/pipelines/authored-film/headshots-director-1.4.md").is_file()

    def test_1_4_is_1_3_plus_judged_headshots(self):
        m13, m14 = load_pipeline("authored-film@1.3"), load_pipeline("authored-film@1.4")
        assert m14["version"] == "1.4"
        s13 = {s["name"]: s for s in m13["stages"]}
        s14 = {s["name"]: s for s in m14["stages"]}
        assert list(s13) == list(s14)
        for name in s13:
            if name != "headshots":
                assert s13[name] == s14[name], name
        hs = s14["headshots"]
        assert hs["skill"] == "pipelines/authored-film/headshots-director-1.4"
        assert s13["headshots"]["skill"] == "pipelines/authored-film/headshots-director"
        assert "sheet_judge" in hs["tools_available"] and "sheet_judge" not in s13["headshots"]["tools_available"]
        assert "pipelines/authored-film/headshots-director-1.4" in m14["required_skills"]
        assert "pipelines/authored-film/headshots-director" in m13["required_skills"]
        assert any("hero" in line and "qc" in line.lower() for line in hs["review_focus"])


# ---- project_config 1.2 ----

def _cfg(version: str, *, hero: bool) -> dict:
    qc = {"judge_provider": "openai", "judge_model": "judge-x", "policy_bundle_sha256": policy.bundle_sha256(),
          "max_attempts_per_series": 3}
    if hero:
        qc.update({"hero_policy_sha256": policy.hero_bundle_sha256(), "max_hero_attempts": 8})
    return {"version": version, "budget_usd_cap": 50.0, "wall_time_minutes": 30,
            "cast_cap": {"characters": 2, "locations": 2},
            "default_video_endpoint": "fal-ai/kling-video/o3/pro/reference-to-video",
            "provider_egress": [{"provider": "fal", "content_classes": ["prompts", "reference_images"]},
                                {"provider": "openai", "content_classes": ["prompts", "generated_sheet_images"]}],
            "qc": qc}


class TestConfig12:
    def test_1_2_requires_hero_fields_and_1_1_refuses_them(self):
        validate_project_config(_cfg("1.2", hero=True))
        with pytest.raises(Exception):
            validate_project_config(_cfg("1.2", hero=False))
        with pytest.raises(Exception):
            validate_project_config(_cfg("1.1", hero=True))
        validate_project_config(_cfg("1.1", hero=False))
        bad = _cfg("1.2", hero=True)
        bad["qc"]["max_hero_attempts"] = 25
        with pytest.raises(Exception):
            validate_project_config(bad)

    def test_loader_accessors(self, tmp_path):
        c12 = VerifiedProjectConfig(tmp_path, "d" * 64, _cfg("1.2", hero=True))
        qc = c12.require_hero_qc()
        assert qc.has_hero and qc.max_hero_attempts == 8 and qc.hero_policy_sha256 == policy.hero_bundle_sha256()
        assert qc.policy_bundle_sha256 == policy.bundle_sha256()
        c11 = VerifiedProjectConfig(tmp_path, "d" * 64, _cfg("1.1", hero=False))
        assert c11.require_qc().has_hero is False
        with pytest.raises(ProjectConfigError, match="hero"):
            c11.require_hero_qc()


# ---- headshot_packet 1.1 ----

def _candidate(qc: bool) -> dict:
    ref = {"asset_id": "b" * 64, "path": "canon/visual/objects/x.png", "role": "hero",
           "provenance": {"generator_kind": "model", "model_endpoint": "e", "prompt": "p", "generation_receipt_id": "g"}}
    if qc:
        ref["qc_receipt_id"] = "qc-1"
    return ref


def _pending(version: str, qc: bool) -> dict:
    return {"version": version, "state": "pending", "characters": [{
        "entity_kind": "character", "entity_id": "marlow-vex",
        "look_ref": {"entity_kind": "character", "entity_id": "marlow-vex", "look_hash": SHA, "receipt_id": "r"},
        "candidates": [_candidate(qc)]}]}


def _approved(version: str, qc: bool) -> dict:
    entry = {"entity_kind": "character", "entity_id": "marlow-vex",
             "look_ref": {"entity_kind": "character", "entity_id": "marlow-vex", "look_hash": SHA, "receipt_id": "r"},
             "hero": _candidate(qc), "origin": "generated", "normalized_pixel_hash": "b" * 64,
             "approval_receipt_id": "hs-1", "candidates_rejected": []}
    if qc:
        entry["qc_receipt_id"] = "qc-1"
    return {"version": version, "state": "approved", "characters": [entry]}


class TestHeadshotPacket11:
    def test_1_1_requires_qc_receipt_ids_1_0_does_not(self):
        validate_artifact("headshot_packet", _pending("1.1", True))
        validate_artifact("headshot_packet", _approved("1.1", True))
        validate_artifact("headshot_packet", _pending("1.0", False))
        validate_artifact("headshot_packet", _approved("1.0", False))
        validate_artifact("headshot_packet", _pending("1.0", True))  # optional under 1.0
        with pytest.raises(Exception):
            validate_artifact("headshot_packet", _pending("1.1", False))
        with pytest.raises(Exception):
            validate_artifact("headshot_packet", _approved("1.1", False))
        with pytest.raises(Exception):
            validate_artifact("headshot_packet", _pending("1.2", True))

    def test_qc_verdict_accepts_role_hero(self):
        schema = json.loads((ROOT / "schemas/artifacts/qc_verdict.schema.json").read_text())
        assert "hero" in schema["properties"]["role"]["enum"]


# ---- headshot record 1.1 ----

class TestHeadshotRecord11:
    def test_1_0_unchanged_and_1_1_seals_receipts(self):
        base = dict(entity_id="marlow-vex", look_hash=SHA, asset_id="b" * 64, origin="generated",
                    import_receipt_id=None, prompt_recipe_sha256="c" * 64, candidates_checkpoint_digest="d" * 64)
        r10 = headshot_record(**base)
        assert set(r10) == set(HEADSHOT_RECORD_FIELDS) and record_version_of(r10) == "1.0"
        r11 = headshot_record(**base, record_version="1.1", generation_receipt_id="gen-1", qc_receipt_id="qc-1")
        assert set(r11) == set(HEADSHOT_RECORD_FIELDS_1_1) and record_version_of(r11) == "1.1"
        assert r11["generation_receipt_id"] == "gen-1" and r11["qc_receipt_id"] == "qc-1"
        with pytest.raises(HeadshotError):
            headshot_record(**base, record_version="1.1", generation_receipt_id="gen-1")
        with pytest.raises(HeadshotError):
            headshot_record(**base, generation_receipt_id="gen-1", qc_receipt_id="qc-1")  # 1.0 carries none
        with pytest.raises(HeadshotError):
            headshot_record(**base, record_version="2.0", generation_receipt_id="g", qc_receipt_id="q")

    def test_imported_1_1_generation_receipt_is_the_import_receipt(self):
        base = dict(entity_id="marlow-vex", look_hash=SHA, asset_id="b" * 64, origin="imported_synthetic",
                    import_receipt_id="imp-1", prompt_recipe_sha256=None, candidates_checkpoint_digest="d" * 64)
        ok = headshot_record(**base, record_version="1.1", generation_receipt_id="imp-1", qc_receipt_id="qc-1")
        assert ok["generation_receipt_id"] == "imp-1"
        with pytest.raises(HeadshotError):
            headshot_record(**base, record_version="1.1", generation_receipt_id="other", qc_receipt_id="qc-1")


# ---- reference import: split + character binding ----

def _pixels(seed: int) -> bytes:
    im = Image.new("RGB", (12, 9))
    px = im.load()
    for x in range(12):
        for y in range(9):
            px[x, y] = ((x * 20 + seed) % 256, (y * 25) % 256, 90)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _stage(project_dir: Path, name: str, data: bytes) -> Path:
    p = project_dir / ".staging" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _approve_import(project_dir: Path, request_path: Path) -> dict:
    req = json.loads(request_path.read_text())
    with gates.handler_context():
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


class TestImportRecordBinding:
    def test_record_binds_a_slug_never_free_text(self):
        rec = import_record(ORIGIN_IMPORTED_SYNTHETIC, SHA, origin_tool="elsewhere-gen", entity_id="marlow-vex")
        assert rec["entity_id"] == "marlow-vex" and record_entity_id(rec) == "marlow-vex"
        assert validate_import_record(rec) == rec
        assert record_entity_id(import_record(ORIGIN_CASTING, SHA)) is None
        with pytest.raises(ReferenceImportError):
            import_record(ORIGIN_CASTING, SHA, entity_id="A Real Name")
        with pytest.raises(ReferenceImportError):
            validate_import_record(dict(rec, entity_id="someone-else "))

    def test_normalize_then_publish_and_no_overwrite(self, project_dir):
        n = normalize_reference_import(project_dir, _stage(project_dir, "a.png", _pixels(1)),
                                       origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="elsewhere-gen",
                                       entity_id="marlow-vex", source_name="dropped on the floor")
        assert n.staged_path.is_file() and not (project_dir / ".gate-requests").exists()
        assert "source_name" not in n.record and n.record["entity_id"] == "marlow-vex"
        path = publish_import_request(project_dir, "p", n, request_id="import-marlow-vex-1", source_checkpoint_digest="e" * 64)
        req = json.loads(path.read_text())
        assert req["entity_id"] == "marlow-vex" and req["source_checkpoint_digest"] == "e" * 64
        assert req["approval_record"] == n.record and "character 'marlow-vex'" in req["summary"]
        with pytest.raises(ReferenceImportError, match="already exists"):
            publish_import_request(project_dir, "p", n, request_id="import-marlow-vex-1")

    def test_one_face_one_character_pending_and_approved(self, project_dir):
        data = _pixels(2)
        n1 = normalize_reference_import(project_dir, _stage(project_dir, "a.png", data),
                                        origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="t", entity_id="marlow-vex")
        p1 = publish_import_request(project_dir, "p", n1, request_id="import-marlow-vex-1")
        assert entity_claiming_hash(project_dir, n1.normalized_pixel_hash) == ("marlow-vex", "pending")
        n2 = normalize_reference_import(project_dir, _stage(project_dir, "b.png", data),
                                        origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="t", entity_id="orla-quist")
        with pytest.raises(ReferenceImportError, match="already claimed by character 'marlow-vex'"):
            publish_import_request(project_dir, "p", n2, request_id="import-orla-quist-1")
        # same character re-publishing under a new id is fine
        publish_import_request(project_dir, "p", n1, request_id="import-marlow-vex-2")
        receipt = _approve_import(project_dir, p1)
        assert entity_claiming_hash(project_dir, n1.normalized_pixel_hash) == ("marlow-vex", "approved")
        assert synthetic_import_receipt(project_dir, n1.normalized_pixel_hash)["receipt_id"] == receipt["receipt_id"]
        assert synthetic_import_receipt(project_dir, n1.normalized_pixel_hash, entity_id="marlow-vex") is not None
        assert synthetic_import_receipt(project_dir, n1.normalized_pixel_hash, entity_id="orla-quist") is None
        with pytest.raises(ReferenceImportError, match="already claimed"):
            publish_import_request(project_dir, "p", n2, request_id="import-orla-quist-2")

    def test_prepare_is_the_composition(self, project_dir):
        prepared = prepare_reference_import(project_dir, "p", _stage(project_dir, "a.png", _pixels(3)),
                                            origin_class=ORIGIN_CASTING, entity_id="marlow-vex")
        req = json.loads(prepared.request_path.read_text())
        assert req["approval_record"]["entity_id"] == "marlow-vex" and "origin_tool" not in req["approval_record"]
        legacy = prepare_reference_import(project_dir, "p", _stage(project_dir, "b.png", _pixels(4)),
                                          origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="t")
        assert record_entity_id(json.loads(legacy.request_path.read_text())["approval_record"]) is None
        assert entity_claiming_hash(project_dir, legacy.normalized_pixel_hash) == (None, None)

"""authored-film 1.6 — the shot-ready manifest (plan 2026-09-08, C1).

1.6 is 1.5 plus one declared key: ``partial_predecessors`` on ``script``,
``scene_plan`` and ``assets``. This module covers the DATA side of that rule —
the manifest's shape, its load-time validation (T9), the manifest sets 1.6
must be registered in, the predicate later tasks key on, and the migration
guard (T8). The BEHAVIOUR side (what a partial predecessor lets a project
write) lives in tests/lib/test_shot_ready_rule.py. Invented names only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lib.canon_enforcement import (
    HERO_BATCH_MANIFESTS,
    HERO_QC_MANIFESTS,
    LOOK_LOCK_MANIFESTS,
    QC_MANIFESTS,
    _is_hero_batch_manifest,
    _is_hero_qc_manifest,
    _is_look_lock_manifest,
    _is_qc_manifest,
    _is_shot_ready_manifest,
)
from lib.pipeline_loader import (
    PIPELINE_DEFS_DIR,
    load_pipeline,
    manifest_digest,
    manifest_versions,
)
from lib.pipeline_pin import PinnedPipeline, PipelinePinError, require_migration_coverage

SHOT_READY_STAGES = ("script", "scene_plan", "assets")
DECLARED = ["look_lock", "headshots", "visual_bible"]


def _pin(version: str, name: str = "authored-film") -> PinnedPipeline:
    return PinnedPipeline(name, version, "d" * 64, None)


# ---------------------------------------------------------------------------
# The manifest itself
# ---------------------------------------------------------------------------

class TestManifest16:
    def test_1_6_is_1_5_plus_partial_predecessors(self):
        m15, m16 = load_pipeline("authored-film@1.5"), load_pipeline("authored-film@1.6")
        assert m16["version"] == "1.6"
        assert {k: v for k, v in m15.items() if k not in ("version", "stages")} == {
            k: v for k, v in m16.items() if k not in ("version", "stages")
        }
        s15 = {s["name"]: s for s in m15["stages"]}
        s16 = {s["name"]: s for s in m16["stages"]}
        assert list(s15) == list(s16)
        for name, spec in s16.items():
            expected = dict(s15[name])
            if name in SHOT_READY_STAGES:
                expected["partial_predecessors"] = DECLARED
            assert spec == expected, name

    def test_only_the_three_shot_stages_declare_it_edit_and_compose_do_not(self):
        stages = {s["name"]: s for s in load_pipeline("authored-film@1.6")["stages"]}
        declaring = [n for n, s in stages.items() if "partial_predecessors" in s]
        assert declaring == list(SHOT_READY_STAGES)
        assert "partial_predecessors" not in stages["edit"]
        assert "partial_predecessors" not in stages["compose"]

    def test_director_skill_pointers_are_the_1_5_ones(self):
        s15 = {s["name"]: s.get("skill") for s in load_pipeline("authored-film@1.5")["stages"]}
        s16 = {s["name"]: s.get("skill") for s in load_pipeline("authored-film@1.6")["stages"]}
        assert s15 == s16
        assert load_pipeline("authored-film@1.6")["required_skills"] == load_pipeline("authored-film@1.5")["required_skills"]

    def test_1_6_is_a_registered_version(self):
        assert manifest_versions("authored-film") == ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6"]
        assert (PIPELINE_DEFS_DIR / "authored-film@1.6.yaml").is_file()


# ---------------------------------------------------------------------------
# T9 — the declaration is validated at load
# ---------------------------------------------------------------------------

def _write_manifest(defs_dir: Path, *, declared, stage: str = "assets", name: str = "toy") -> Path:
    manifest = {
        "name": name,
        "version": "1.0",
        "stages": [
            {"name": "first", "produces": ["a"]},
            {"name": "second", "produces": ["b"]},
            {"name": "assets", "produces": ["c"]},
        ],
    }
    for spec in manifest["stages"]:
        if spec["name"] == stage:
            spec["partial_predecessors"] = declared
    defs_dir.mkdir(parents=True, exist_ok=True)
    path = defs_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return path


class TestDeclarationValidatedAtLoad:
    def test_earlier_stages_load(self, tmp_path):
        _write_manifest(tmp_path, declared=["first", "second"])
        spec = load_pipeline("toy", tmp_path)["stages"][2]
        assert spec["partial_predecessors"] == ["first", "second"]

    def test_a_later_stage_is_rejected(self, tmp_path):
        _write_manifest(tmp_path, declared=["assets"], stage="first")
        with pytest.raises(ValueError, match="does not precede"):
            load_pipeline("toy", tmp_path)

    def test_an_unknown_stage_is_rejected(self, tmp_path):
        _write_manifest(tmp_path, declared=["first", "nowhere"])
        with pytest.raises(ValueError, match="not a stage"):
            load_pipeline("toy", tmp_path)

    def test_self_reference_is_rejected(self, tmp_path):
        _write_manifest(tmp_path, declared=["assets"])
        with pytest.raises(ValueError, match="itself"):
            load_pipeline("toy", tmp_path)

    def test_a_non_list_value_is_rejected(self, tmp_path):
        _write_manifest(tmp_path, declared="first")
        with pytest.raises(Exception):
            load_pipeline("toy", tmp_path)

    def test_a_list_of_non_strings_is_rejected(self, tmp_path):
        _write_manifest(tmp_path, declared=[7])
        with pytest.raises(Exception):
            load_pipeline("toy", tmp_path)

    def test_every_shipped_authored_film_manifest_declares_only_valid_stages(self):
        for path in sorted(PIPELINE_DEFS_DIR.glob("authored-film*.yaml")):
            load_pipeline(path.stem)  # raises on an invalid declaration


# ---------------------------------------------------------------------------
# Registration: a missing entry silently loses 1.5 behaviour (D21 R1#5)
# ---------------------------------------------------------------------------

class TestRegistration:
    @pytest.mark.parametrize("registry", [LOOK_LOCK_MANIFESTS, QC_MANIFESTS, HERO_QC_MANIFESTS, HERO_BATCH_MANIFESTS])
    def test_1_6_is_registered_wherever_1_5_is(self, registry):
        assert ("authored-film", "1.5") in registry
        assert ("authored-film", "1.6") in registry

    @pytest.mark.parametrize(
        "predicate", [_is_look_lock_manifest, _is_qc_manifest, _is_hero_qc_manifest, _is_hero_batch_manifest]
    )
    def test_every_1_5_profile_predicate_holds_at_1_6(self, predicate):
        assert predicate(_pin("1.5")) is True
        assert predicate(_pin("1.6")) is True


class TestShotReadyPredicate:
    def test_true_from_1_6_and_later_minors(self):
        assert _is_shot_ready_manifest(_pin("1.6")) is True
        assert _is_shot_ready_manifest(_pin("1.7")) is True
        assert _is_shot_ready_manifest(_pin("1.10")) is True

    def test_false_for_earlier_pins_other_pipelines_and_no_pin(self):
        for version in ("1.1", "1.2", "1.3", "1.4", "1.5"):
            assert _is_shot_ready_manifest(_pin(version)) is False
        assert _is_shot_ready_manifest(_pin("1.6", name="cinematic")) is False
        assert _is_shot_ready_manifest(_pin("2.0")) is False
        assert _is_shot_ready_manifest(None) is False
        assert _is_shot_ready_manifest(_pin("not-a-version")) is False


# ---------------------------------------------------------------------------
# T1 (frozen bytes) — the golden 1.5 digest lives with the other goldens
# ---------------------------------------------------------------------------

class TestFrozenDigests:
    def test_adding_1_6_did_not_touch_the_1_5_bytes(self):
        from tests.lib.test_d20_versions import MANIFEST_1_5_SHA256

        assert manifest_digest("authored-film@1.5") == MANIFEST_1_5_SHA256
        assert manifest_digest("authored-film@1.6") != MANIFEST_1_5_SHA256


# ---------------------------------------------------------------------------
# T8 — migration to 1.6 is refused while the project is mid-flight
# ---------------------------------------------------------------------------

def _request(project_dir: Path, request_id: str, kind: str) -> Path:
    d = project_dir / ".gate-requests"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{request_id}.json"
    path.write_text(json.dumps({"request_id": request_id, "kind": kind}), encoding="utf-8")
    return path


def _bible_checkpoint(project_dir: Path, metadata: dict) -> None:
    (project_dir / "checkpoint_visual_bible.json").write_text(
        json.dumps({"stage": "visual_bible", "status": "in_progress", "metadata": metadata}), encoding="utf-8"
    )


@pytest.fixture
def quiet(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    return d


class TestMigrationCoverage16:
    def test_a_quiet_project_may_migrate(self, quiet):
        _bible_checkpoint(quiet, {"run_state": {}, "location_run_state": {}})
        _request(quiet, "pipeline-migration-1-6", "pipeline_migration")
        require_migration_coverage(quiet, "authored-film", "1.6")

    def test_no_visual_bible_checkpoint_at_all_is_quiet(self, quiet):
        require_migration_coverage(quiet, "authored-film", "1.6")

    def test_a_pending_governed_request_refuses(self, quiet):
        _request(quiet, "headshot-char-one-1", "headshot")
        with pytest.raises(PipelinePinError, match="headshot-char-one-1"):
            require_migration_coverage(quiet, "authored-film", "1.6")

    def test_a_signed_or_declined_request_does_not_refuse(self, quiet):
        for sub in ("done", "declined"):
            _request(quiet / ".gate-requests" / sub, "override-char-one-hero-1", "qc_override")
        require_migration_coverage(quiet, "authored-film", "1.6")

    def test_a_non_empty_run_state_refuses(self, quiet):
        _bible_checkpoint(quiet, {"run_state": {"char-one": {"mode": "select"}}})
        with pytest.raises(PipelinePinError, match="run_state"):
            require_migration_coverage(quiet, "authored-film", "1.6")

    def test_a_non_empty_location_run_state_refuses(self, quiet):
        _bible_checkpoint(quiet, {"location_run_state": {"loc-alpha": {"mode": "select"}}})
        with pytest.raises(PipelinePinError, match="location_run_state"):
            require_migration_coverage(quiet, "authored-film", "1.6")

    def test_other_versions_are_not_affected(self, quiet):
        _request(quiet, "headshot-char-one-1", "headshot")
        _bible_checkpoint(quiet, {"run_state": {"char-one": {"mode": "select"}}})
        for version in ("1.1", "1.2", "1.3", "1.5"):
            require_migration_coverage(quiet, "authored-film", version)
        require_migration_coverage(quiet, "cinematic", "1.6")

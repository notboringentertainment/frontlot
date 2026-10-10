"""Shared fixtures for D19 sheet-QC tests. Invented names only. Real signed
receipts against a per-test OPENMONTAGE_GATES_DIR, never stubs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.pipeline_loader import manifest_digest
from lib.pipeline_pin import checkpoint_digests_on_disk, migration_record, refresh_cache
from lib.sheet_qc import policy

JUDGE_MODEL = "judge-x"


def config_1_1(*, budget: float = 50.0, max_attempts: int = 3, openai_classes=("prompts", "generated_sheet_images")) -> dict:
    return {
        "version": "1.1", "budget_usd_cap": budget, "wall_time_minutes": 30,
        "cast_cap": {"characters": 2, "locations": 2},
        "default_video_endpoint": "fal-ai/kling-video/o3/pro/reference-to-video",
        "provider_egress": [
            {"provider": "fal", "content_classes": ["prompts", "reference_images"]},
            {"provider": "openai", "content_classes": list(openai_classes)},
        ],
        "qc": {"judge_provider": "openai", "judge_model": JUDGE_MODEL,
               "policy_bundle_sha256": policy.bundle_sha256(), "max_attempts_per_series": max_attempts},
    }


def config_1_2(*, budget: float = 50.0, max_attempts: int = 3, max_hero_attempts: int = 4,
               openai_classes=("prompts", "generated_sheet_images")) -> dict:
    """D20: a 1.2 config = 1.1 + the hero bundle hash and the hero budget."""
    cfg = config_1_1(budget=budget, max_attempts=max_attempts, openai_classes=openai_classes)
    cfg["version"] = "1.2"
    cfg["qc"].update({"hero_policy_sha256": policy.hero_bundle_sha256(), "max_hero_attempts": max_hero_attempts})
    return cfg


def approve(project: Path, slug: str, stage: str, scope: str, record: dict, kind: str, entity_id: str, envelope=None) -> dict:
    with gates.handler_context():
        token = gates.mint_gate_token(slug, stage, scope, record_sha256(record), user_response="approve")
    return receipts.record_human_approval(project, slug, stage, scope, record, token, kind, entity_id=entity_id, envelope=envelope)


def sign_config(project: Path, slug: str, config: dict) -> str:
    raw = yaml.safe_dump(config, sort_keys=True).encode("utf-8")
    (project / "project.yaml").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    approve(project, slug, "proposal", "config", {"config_sha256": digest}, "config", "project-config")
    return digest


def pin(project: Path, slug: str, version: str, *, supersedes: str | None = None) -> dict:
    record = migration_record("authored-film", version, manifest_digest(f"authored-film@{version}"), checkpoint_digests_on_disk(project))
    r = approve(project, slug, "pipeline", "pipeline:authored-film", record, "pipeline_migration", "authored-film",
                {"supersedes_receipt_id": supersedes})
    marker = project / "project.json"
    data = json.loads(marker.read_text()) if marker.exists() else {}
    data["pipeline_type"] = "authored-film"
    marker.write_text(json.dumps(data))
    refresh_cache(project, "authored-film")
    return r


def make_qc_project(tmp_path: Path, monkeypatch, slug: str = "proj-lantern", *, version: str = "1.3", config: dict | None = None) -> Path:
    """Registered project (patched PROJECTS_DIR), signed 1.1 config, pinned manifest."""
    import lib.events as events_mod
    import lib.paths as paths_mod

    monkeypatch.setattr(events_mod, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", tmp_path)
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / ".gates"))
    project = tmp_path / slug
    project.mkdir()
    (project / "canon" / "visual" / "objects").mkdir(parents=True)
    sign_config(project, slug, config or config_1_1())
    pin(project, slug, version)
    return project


def series_key(slug: str, **over) -> dict:
    key = {"project_id": slug, "entity_kind": "character", "entity_id": "marlow-vex", "role": "turnaround",
           "look_hash": "l" * 40, "headshot_receipt_id": "hs-1", "policy_bundle_sha256": policy.bundle_sha256(),
           "builder_policy_sha256": "c" * 64, "generation_endpoint": "vendor/edit", "generation_model": "vendor/edit",
           "judge_provider": "openai", "judge_model": JUDGE_MODEL}
    key.update(over)
    return key

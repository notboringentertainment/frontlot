"""Shared helpers for Slice 4 tool tests (no network, invented names only)."""

from __future__ import annotations

from pathlib import Path

from lib.config_model import BudgetMode
from tools.cost_tracker import CostTracker


def make_project(tmp_path: Path, monkeypatch, slug: str = "proj-zephyr") -> Path:
    """Create a project dir under a patched PROJECTS_DIR so events/receipts attribute to it."""
    import lib.events as events_mod

    monkeypatch.setattr(events_mod, "PROJECTS_DIR", tmp_path)
    project = tmp_path / slug
    project.mkdir()
    return project


def make_tracker(project: Path, budget: float = 50.0) -> CostTracker:
    return CostTracker(
        budget_total_usd=budget,
        reserve_pct=0.0,
        single_action_approval_usd=1000.0,
        require_approval_for_new_paid_tool=False,
        mode=BudgetMode.CAP,
        cost_log_path=project / "cost_log.json",
    )


def tiny_png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


PROJECT_CONFIG = {
    "version": "1.0",
    "budget_usd_cap": 50.0,
    "wall_time_minutes": 30,
    "cast_cap": {"characters": 2, "locations": 2},
    "default_video_endpoint": "bytedance/seedance-2.5/reference-to-video",
    "provider_egress": {"provider": "fal", "content_classes": ["prompts", "reference_images"]},
}


def make_verified_project(
    tmp_path: Path,
    monkeypatch,
    slug: str = "proj-zephyr",
    *,
    budget: float = 50.0,
    egress_classes: tuple[str, ...] = ("prompts", "reference_images"),
) -> Path:
    """A registered project (patched PROJECTS_DIR for events + paths) whose
    project.yaml is bound to a real signed ``config`` approval receipt, so
    ``load_verified_project_config`` and the paid tools accept it."""
    import yaml

    import lib.paths as paths_mod
    from lib import gates, receipts
    from lib.canonical_json import record_sha256

    project = make_project(tmp_path, monkeypatch, slug)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", tmp_path)
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / ".gates"))
    config = {**PROJECT_CONFIG, "budget_usd_cap": budget,
              "provider_egress": {"provider": "fal", "content_classes": list(egress_classes)}}
    raw = yaml.safe_dump(config, sort_keys=True).encode("utf-8")
    (project / "project.yaml").write_bytes(raw)
    import hashlib

    record = {"config_sha256": hashlib.sha256(raw).hexdigest()}
    token = gates.mint_gate_token(
        project_id=slug, stage="proposal", scope="config",
        record_sha256=record_sha256(record), user_response="approve",
    )
    receipts.record_human_approval(
        project, slug, "proposal", "config", record, token, "config", entity_id="project-config",
    )
    return project


def project_tracker(project: Path) -> CostTracker:
    """Re-open the tracker the paid tools built over <project>/cost_log.json."""
    return CostTracker(cost_log_path=project / "cost_log.json")


def approve_storyboard_batch(project: Path, frames: dict[str, str], slug: str | None = None) -> dict:
    """Mint + consume a real gate token for a ``storyboard_batch`` receipt
    covering ``frames`` (shot_id -> sha256) so ``require_storyboard_receipt`` passes."""
    from lib import gates, receipts
    from lib.canon_enforcement import storyboard_batch_record
    from lib.canonical_json import record_sha256

    slug = slug or project.name
    record = storyboard_batch_record(frames)
    token = gates.mint_gate_token(
        project_id=slug, stage="assets", scope="storyboard_batch",
        record_sha256=record_sha256(record), user_response="approve",
    )
    return receipts.record_human_approval(
        project, slug, "assets", "storyboard_batch", record, token, "storyboard_batch",
        entity_id="storyboard_batch",
    )


def write_receipted_png(project: Path, rel: str, payload: bytes | None = None, *, tool: str = "seedream_image") -> dict:
    """Write a PNG under the project and record a verified generation receipt
    for it; returns ``{"path": <abs Path>, "sha256": ..., "receipt": ...}``."""
    from lib import receipts
    from lib.pathsafe import sha256_file

    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload or tiny_png_bytes())
    sha = sha256_file(path)
    row = receipts.record_generation(
        project, execution_id=f"exec-{rel}", tool=tool, normalized_inputs_hash="a" * 64,
        output_sha256=sha, cost_usd=0.01, started_at="2026-08-25T00:00:00+00:00",
        finished_at="2026-08-25T00:00:01+00:00", model_endpoint="vendor/image-model", prompt=f"image {rel}",
    )
    return {"path": path, "sha256": sha, "receipt": row}

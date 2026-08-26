"""Checkpoint writer/reader for pipeline state persistence.

Each stage writes a checkpoint after completion. The orchestrator uses
checkpoints to resume pipelines and to present state at human checkpoints.

Durability: checkpoint and decision-log writes go through
``lib.state_io`` (unique temp file + fsync + rename), so a crash mid-write
never leaves a truncated file or a stale fixed-name temp behind.

Run lease (authored-canon pipelines, PLAN §7): ``write_checkpoint`` does NOT
acquire the per-project run lease — tests and repair tooling write
checkpoints freely. The ORCHESTRATOR acquires it once at run start via
``acquire_run_lease(pipeline_dir, project_id, wall_time_minutes)`` and holds
it (heartbeating) for the whole run; a second live session on the same
project fails with ``lib.run_lease.LeaseHeldError``.

Paid-call resume safety: for pipelines with ``validation_profile:
authored-canon``, every ``write_checkpoint`` first runs
``tools.cost_tracker.resume_check`` on the project directory. A reservation
left ``submitting`` with no provider request id means money may have been
spent with no recorded outcome; the write is refused with a
``CheckpointValidationError`` naming the reservations until a human
reconciles them (never resubmit automatically).

Human approval receipts are recorded through ``record_human_approval``
(re-exported here from ``lib.receipts``); it consumes a one-use gate token
and never changes checkpoint status.
"""

from __future__ import annotations

import json
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import jsonschema

from lib.receipts import record_human_approval  # noqa: F401  (re-export for callers)
from lib.state_io import atomic_write_bytes, atomic_write_json
from schemas.artifacts import ARTIFACT_NAMES, validate_artifact

# Stage names of every manifest version (used only for artifact-name lookup
# and the pipeline-less fallback); the pinned manifest is the real stage list.

# All known stages across all pipelines (used only for artifact name lookup).
ALL_KNOWN_STAGES = frozenset([
    "research", "proposal", "idea", "script", "scene_plan",
    "assets", "edit", "compose", "publish",
    "canon_ingest", "look_lock", "headshots", "visual_bible",
])

# Backward-compatible alias — existing code / tests that import STAGES still work.
# New code should use get_pipeline_stages(pipeline_type) instead.
STAGES = ["research", "proposal", "idea", "script", "scene_plan",
          "assets", "edit", "compose", "publish"]

CANONICAL_STAGE_ARTIFACTS = {
    "research": "research_brief",
    "proposal": "proposal_packet",
    "idea": "brief",
    "script": "script",
    "scene_plan": "scene_plan",
    "assets": "asset_manifest",
    "edit": "edit_decisions",
    "compose": "render_report",
    "publish": "publish_log",
}

# Additional artifacts that may be produced alongside canonical ones.
# These are not stage-defining but are required by governance contracts.
SUPPLEMENTARY_ARTIFACTS = {
    "source_media_review",  # Required before first planning stage when user media exists
    "final_review",         # Required by compose stage before presenting to user
    "video_analysis_brief", # Reference-video grounding artifact carried alongside stages
}


def get_pipeline_stages(pipeline_type: str | None) -> list[str]:
    """Return the ordered stage list for a specific pipeline.

    Falls back to STAGES (deterministic canonical order) when pipeline_type
    is not provided or the manifest cannot be loaded.

    Previous versions used a set intersection here, which produced
    nondeterministic ordering. The fallback now uses a stable list.
    """
    if pipeline_type is None:
        # Deterministic canonical fallback — sorted to ensure stable ordering
        import logging
        logging.getLogger(__name__).warning(
            "get_pipeline_stages called without pipeline_type — "
            "using canonical fallback order. Pass pipeline_type for correctness."
        )
        return list(STAGES)

    try:
        from lib.pipeline_loader import load_pipeline_readonly, get_stage_order
        manifest = load_pipeline_readonly(pipeline_type)
        return get_stage_order(manifest)
    except (FileNotFoundError, Exception):
        # Graceful fallback: return all known stages in canonical order
        return list(STAGES)

def _pin_for(pipeline_dir: Path, project_id: str, pipeline_type: Optional[str]):
    """The project's pinned manifest tuple for ``pipeline_type`` (bare or
    explicit ``name@version``), or None when no pipeline is named. A pin that
    cannot be resolved unambiguously is a checkpoint error (fail closed)."""
    if not pipeline_type or pipeline_type == "unknown":
        return None
    from lib.pipeline_loader import parse_pipeline_ref
    from lib.pipeline_pin import PipelinePinError, pinned_pipeline

    try:
        return pinned_pipeline(pipeline_dir / project_id, pipeline_type)
    except FileNotFoundError:
        raise CheckpointValidationError(
            f"Unknown pipeline_type {pipeline_type!r} — cannot resolve gate "
            f"policy. Check the spelling against pipeline_defs/*.yaml."
        )
    except PipelinePinError as exc:
        raise CheckpointValidationError(f"PIPELINE PIN VIOLATION: {exc}") from exc
    except Exception as exc:
        # Same fail-closed rule as _manifest_stage_spec: a manifest that
        # exists but cannot load must not silently drop its contracts.
        base, _ = parse_pipeline_ref(pipeline_type)
        raise CheckpointValidationError(
            f"Manifest for pipeline {base!r} failed to load, so its contracts "
            f"cannot be enforced. Refusing (fail-closed). Underlying error: {exc}"
        ) from exc


def _effective_ref(pipeline_dir: Path, project_id: str, pipeline_type: Optional[str]) -> Optional[str]:
    """``name@version`` for pipelines with versioned manifests, else the bare name."""
    pin = _pin_for(pipeline_dir, project_id, pipeline_type)
    if pin is None:
        return None
    from lib.pipeline_loader import manifest_versions

    return pin.ref if manifest_versions(pin.name) else pin.name


def _pin_mismatch(
    pipeline_dir: Path, project_id: str, pipeline_type: Optional[str], stage: str, path: Path, checkpoint: dict[str, Any]
) -> Optional[str]:
    """Why an on-disk checkpoint does not belong to the project's signed pin
    (None when it does). Slice A #10: under a receipted pin, a checkpoint
    either carries the pin's exact ``pipeline`` tuple {name, version,
    manifest_digest} or — a legacy checkpoint with no tuple / another tuple —
    its file digest must be in the digest set the migration receipt bound."""
    pin = _pin_for(pipeline_dir, project_id, pipeline_type)
    if pin is None or pin.receipt_id is None:
        return None
    tuple_ = checkpoint.get("pipeline")
    if isinstance(tuple_, dict) and tuple_ == pin.to_dict():
        return None
    digest = checkpoint_digest(path)
    if pin.binds_checkpoint(stage, digest):
        return None
    if isinstance(tuple_, dict):
        return (
            f"checkpoint {stage!r} was written under pipeline tuple {tuple_} but the signed pin is "
            f"{pin.to_dict()} (receipt {pin.receipt_id}) and the migration receipt did not bind this file"
        )
    return (
        f"legacy checkpoint {stage!r} (no pipeline tuple; digest {digest[:12]}…) is not in the set bound by "
        f"migration receipt {pin.receipt_id} — an edited or foreign legacy checkpoint never satisfies a 1.2 pin"
    )


def _ref_from_checkpoint(checkpoint: dict[str, Any]) -> Optional[str]:
    """Standalone validation reads the tuple from the checkpoint itself."""
    pipeline_type = checkpoint.get("pipeline_type")
    if not pipeline_type or pipeline_type == "unknown":
        return None
    tuple_ = checkpoint.get("pipeline")
    if isinstance(tuple_, dict) and tuple_.get("version"):
        from lib.pipeline_loader import manifest_versions, parse_pipeline_ref

        base, _ = parse_pipeline_ref(str(pipeline_type))
        if str(tuple_["version"]) in manifest_versions(base):
            return f"{base}@{tuple_['version']}"
        return base
    return str(pipeline_type)


def checkpoint_digest(path: Path) -> str:
    """sha256 of a checkpoint file's bytes — what predecessors[] and
    headshot approval records bind."""
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def invalidated_stages(pipeline_dir: Path, project_id: str, pipeline_type: Optional[str]) -> dict[str, Any]:
    """``stage -> lib.invalidation.Invalidation`` for this project, derived
    from the approval ledger (never from the cache)."""
    if not pipeline_type or pipeline_type == "unknown":
        return {}
    from lib.invalidation import invalidated_checkpoints

    ref = _effective_ref(pipeline_dir, project_id, pipeline_type)
    return invalidated_checkpoints(pipeline_dir, project_id, get_pipeline_stages(ref))


CHECKPOINT_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "checkpoints"
    / "checkpoint.schema.json"
)

# Canonical project root. Checkpoints, artifacts, and the project marker all
# live under PROJECTS_DIR/<project_id>/ — this is the location the Backlot
# board watches. Callers may still pass a different pipeline_dir (tests do),
# but production runs should use the default.
from lib.paths import PROJECTS_DIR  # noqa: E402  (single source of truth)

PROJECT_MARKER_FILENAME = "project.json"
HISTORY_DIRNAME = "history"


class CheckpointValidationError(ValueError):
    """Raised when a checkpoint or its canonical artifacts are invalid."""


def _validate_style_playbook(style_playbook: str | None) -> None:
    """Fail closed when a checkpoint names a visual identity that cannot load."""

    if style_playbook is None:
        return
    try:
        from styles.playbook_loader import list_playbooks, load_playbook

        load_playbook(style_playbook)
    except Exception as exc:
        try:
            available = list_playbooks()
        except Exception:
            available = []
        raise CheckpointValidationError(
            f"Unknown or invalid style_playbook {style_playbook!r}. "
            f"Available playbooks: {available}. Underlying error: {exc}"
        ) from exc


@lru_cache(maxsize=1)
def _load_checkpoint_schema() -> dict[str, Any]:
    with open(CHECKPOINT_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _validate_artifacts_for_stage(
    stage: str,
    status: str,
    artifacts: dict[str, Any],
) -> None:
    # Valid stages come from the pipeline manifest (get_pipeline_stages), which
    # can declare stages beyond the 9 canonical ones (e.g. character-animation's
    # `character_design`/`rig_plan`, screen-demo's `real_capture`). Those have no
    # canonical artifact, so look it up defensively — a missing entry means the
    # stage simply has no required artifact, not a crash.
    required_artifact = CANONICAL_STAGE_ARTIFACTS.get(stage)
    if (
        required_artifact is not None
        and status in {"completed", "awaiting_human"}
        and required_artifact not in artifacts
    ):
        raise CheckpointValidationError(
            f"Stage {stage!r} with status {status!r} must include "
            f"canonical artifact {required_artifact!r}"
        )

    for artifact_name, artifact_data in artifacts.items():
        if artifact_name not in ARTIFACT_NAMES:
            continue
        if not isinstance(artifact_data, dict):
            raise CheckpointValidationError(
                f"Artifact {artifact_name!r} must be a JSON object matching its schema"
            )
        try:
            validate_artifact(artifact_name, artifact_data)
        except Exception as exc:
            raise CheckpointValidationError(
                f"Artifact {artifact_name!r} failed schema validation: {exc}"
            ) from exc


def validate_checkpoint(checkpoint: dict[str, Any]) -> None:
    """Validate checkpoint structure and canonical artifact payloads.

    Uses pipeline_type (if present) to resolve the valid stage list.
    Falls back to ALL_KNOWN_STAGES when pipeline_type is absent.
    """
    stage = checkpoint.get("stage")
    status = checkpoint.get("status")
    artifacts = checkpoint.get("artifacts")
    pipeline_type = checkpoint.get("pipeline_type")

    ref = _ref_from_checkpoint(checkpoint) if isinstance(checkpoint, dict) else None
    valid_stages = set(get_pipeline_stages(ref)) if ref else ALL_KNOWN_STAGES

    if not isinstance(stage, str) or stage not in valid_stages:
        raise CheckpointValidationError(
            f"Invalid stage: {stage!r} for pipeline {pipeline_type!r}. "
            f"Valid stages: {sorted(valid_stages)}"
        )
    if not isinstance(status, str):
        raise CheckpointValidationError(f"Invalid status: {status!r}")
    if not isinstance(artifacts, dict):
        raise CheckpointValidationError("Checkpoint artifacts must be a dictionary")

    _validate_artifacts_for_stage(stage, status, artifacts)

    try:
        jsonschema.validate(instance=checkpoint, schema=_load_checkpoint_schema())
    except jsonschema.ValidationError as exc:
        raise CheckpointValidationError(f"Checkpoint failed schema validation: {exc.message}") from exc


def _checkpoint_path(pipeline_dir: Path, project_id: str, stage: str) -> Path:
    return pipeline_dir / project_id / f"checkpoint_{stage}.json"


def init_project(
    project_id: str,
    *,
    title: str,
    pipeline_type: str,
    pipeline_dir: Optional[Path] = None,
    style_playbook: Optional[str] = None,
) -> Path:
    """Initialize a project workspace with the canonical layout + marker file.

    Creates projects/<project_id>/ with the standard subdirectories and writes
    project.json — the marker the Backlot board uses to render a project's
    identity and stage rail before the first checkpoint exists.

    Idempotent: re-running preserves the original created_at and merges fields.
    Returns the project directory.
    """
    _validate_style_playbook(style_playbook)
    base = pipeline_dir or PROJECTS_DIR
    project_dir = base / project_id
    for sub in (
        "artifacts",
        "assets/images",
        "assets/video",
        "assets/audio",
        "assets/music",
        "renders",
    ):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)

    marker_path = project_dir / PROJECT_MARKER_FILENAME
    marker: dict[str, Any] = {}
    if marker_path.exists():
        try:
            with open(marker_path, encoding="utf-8") as f:
                marker = json.load(f)
        except (json.JSONDecodeError, OSError):
            marker = {}

    marker.setdefault("version", "1.0")
    marker.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    marker["project_id"] = project_id
    marker["title"] = title
    marker["pipeline_type"] = pipeline_type
    if style_playbook is not None:
        marker["style_playbook"] = style_playbook

    with open(marker_path, "w", encoding="utf-8") as f:
        json.dump(marker, f, indent=2)

    return project_dir


def _stage_requires_approval(pipeline_type: Optional[str], stage: str) -> Optional[bool]:
    """Read human_approval_default for a stage from its pipeline manifest.

    Returns None when the stage isn't declared in the manifest or no
    pipeline_type was given — the caller then falls back to the value the
    agent passed in.

    A *provided but unknown* pipeline_type raises: a typo must not silently
    disable gate enforcement (fail-closed, not fail-open). Other manifest
    load failures are logged and fall back — a corrupt manifest shouldn't
    strand an otherwise-valid run, but the degradation must be visible.
    """
    if not pipeline_type or pipeline_type == "unknown":
        return None
    from lib.pipeline_loader import get_stage_human_approval_default, load_pipeline_readonly
    try:
        manifest = load_pipeline_readonly(pipeline_type)
    except FileNotFoundError:
        raise CheckpointValidationError(
            f"Unknown pipeline_type {pipeline_type!r} — cannot resolve gate "
            f"policy for stage {stage!r}. Check the spelling against "
            f"pipeline_defs/*.yaml."
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Gate policy unavailable for pipeline %r (%s) — falling back to "
            "the caller's human_approval_required flag.", pipeline_type, exc,
        )
        return None
    return get_stage_human_approval_default(manifest, stage)


def _manifest_stage_spec(
    pipeline_type: Optional[str], stage: str
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Return (manifest, stage_spec) for a stage, or (None, None) when the
    manifest is unavailable. The manifest — not the hard-coded canonical map —
    is the binding source of truth for what a stage produces and requires."""
    if not pipeline_type or pipeline_type == "unknown":
        return None, None
    try:
        from lib.pipeline_loader import load_pipeline_readonly

        manifest = load_pipeline_readonly(pipeline_type)
    except FileNotFoundError as exc:
        raise CheckpointValidationError(
            f"Unknown pipeline_type {pipeline_type!r} — cannot resolve the "
            f"manifest artifact contract. Check the spelling against "
            f"pipeline_defs/*.yaml."
        ) from exc
    except Exception as exc:
        # Fail CLOSED: a manifest that exists but cannot load (YAML error,
        # schema drift) must not silently turn a binding contract into no
        # contract. Previously this returned (None, None) and every
        # manifest-driven check — including the authored-canon profile —
        # quietly skipped.
        raise CheckpointValidationError(
            f"Manifest for pipeline {pipeline_type!r} failed to load, so its "
            f"artifact contracts cannot be enforced. Refusing to write the "
            f"checkpoint (fail-closed). Underlying error: {exc}"
        ) from exc
    for spec in manifest.get("stages", []):
        if spec.get("name") == stage:
            return manifest, spec
    return manifest, None


def _enforce_manifest_artifact_contract(
    pipeline_dir: Path,
    project_id: str,
    pipeline_type: Optional[str],
    stage: str,
    status: str,
    artifacts: dict[str, Any],
    stage_spec: Optional[dict[str, Any]],
) -> None:
    """Enforce the manifest's declared stage contract at write time.

    - Every artifact in ``produces`` must be present on completed /
      awaiting_human checkpoints (the canonical-map check in
      _validate_artifacts_for_stage remains as the legacy fallback when no
      manifest is available).
    - Every artifact in ``required_artifacts_in`` must be available — carried
      in this checkpoint or produced by a completed predecessor checkpoint.
    """
    if status not in {"completed", "awaiting_human"} or stage_spec is None:
        return

    missing_outputs = [
        name for name in stage_spec.get("produces") or [] if name not in artifacts
    ]
    if missing_outputs:
        raise CheckpointValidationError(
            f"Stage {stage!r} with status {status!r} is missing manifest-"
            f"declared output artifact(s) {missing_outputs} — the "
            f"{pipeline_type!r} manifest's 'produces' list is a binding "
            f"contract, not documentation."
        )

    required_in = stage_spec.get("required_artifacts_in") or []
    if not required_in:
        return
    available = set(artifacts)
    stages = get_pipeline_stages(pipeline_type)
    invalidated = invalidated_stages(pipeline_dir, project_id, pipeline_type)
    if stage in stages:
        for predecessor in stages[: stages.index(stage)]:
            path = _checkpoint_path(pipeline_dir, project_id, predecessor)
            if not path.exists() or predecessor in invalidated:
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    checkpoint = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if _pin_mismatch(pipeline_dir, project_id, pipeline_type, predecessor, path, checkpoint):
                continue
            if checkpoint.get("status") == "completed" and isinstance(
                checkpoint.get("artifacts"), dict
            ):
                available.update(checkpoint["artifacts"])
    missing_inputs = [name for name in required_in if name not in available]
    if missing_inputs:
        raise CheckpointValidationError(
            f"Stage {stage!r} cannot advance: manifest-declared input "
            f"artifact(s) {missing_inputs} were never produced by a completed "
            f"predecessor checkpoint (and are not carried in this one)."
        )


def _enforce_stage_prerequisites(
    pipeline_dir: Path,
    project_id: str,
    pipeline_type: str | None,
    stage: str,
    status: str,
) -> None:
    """Require completed, approved predecessors before advancing a stage.

    ``in_progress`` and failure heartbeats remain writable so an operator can
    inspect or resume a broken run. Only lifecycle advancement
    (``awaiting_human``/``completed``) is gated.
    """

    if status not in {"awaiting_human", "completed"}:
        return
    if not pipeline_type or pipeline_type == "unknown":
        return

    stages = get_pipeline_stages(pipeline_type)
    if stage not in stages:
        return
    from lib.pipeline_loader import parse_pipeline_ref

    base_type, _ = parse_pipeline_ref(pipeline_type)
    invalidated = invalidated_stages(pipeline_dir, project_id, pipeline_type)

    incomplete: list[str] = []
    unapproved: list[str] = []
    stale: list[str] = []
    unpinned: list[str] = []
    for predecessor in stages[: stages.index(stage)]:
        path = _checkpoint_path(pipeline_dir, project_id, predecessor)
        if not path.exists():
            incomplete.append(predecessor)
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                checkpoint = json.load(handle)
            validate_checkpoint(checkpoint)
        except (OSError, json.JSONDecodeError, CheckpointValidationError):
            incomplete.append(predecessor)
            continue
        if (
            checkpoint.get("project_id") != project_id
            or checkpoint.get("pipeline_type") != base_type
            or checkpoint.get("stage") != predecessor
        ):
            incomplete.append(predecessor)
            continue
        if checkpoint.get("status") != "completed":
            incomplete.append(predecessor)
            continue
        mismatch = _pin_mismatch(pipeline_dir, project_id, pipeline_type, predecessor, path, checkpoint)
        if mismatch:
            unpinned.append(mismatch)
            continue
        if predecessor in invalidated:
            stale.append(f"{predecessor} (invalidated by receipt {invalidated[predecessor].receipt_id})")
            continue
        if _stage_requires_approval(pipeline_type, predecessor) and not checkpoint.get(
            "human_approved"
        ):
            unapproved.append(predecessor)

    if incomplete or unapproved or stale or unpinned:
        details = []
        if incomplete:
            details.append(f"incomplete or missing: {incomplete}")
        if unpinned:
            details.append("PIPELINE PIN VIOLATION — not bound to the signed pin: " + "; ".join(unpinned))
        if unapproved:
            details.append(f"completed without required approval: {unapproved}")
        if stale:
            details.append(
                f"invalidated by a retired/replaced look or headshot, re-approval required: {stale}"
            )
        raise CheckpointValidationError(
            f"PREREQUISITE VIOLATION: stage {stage!r} cannot advance; "
            + "; ".join(details)
            + f". Pipeline order: {stages}."
        )


def _archive_superseded_checkpoint(path: Path, stage: str) -> None:
    """Copy an existing checkpoint into history/ before it is overwritten.

    Preserves the full run record: stage re-runs (script v1 → v2) and gate
    transitions (awaiting_human → completed) remain reconstructable. Repeated
    in_progress refreshes are NOT archived — they are partial-progress
    heartbeats, not versions.

    Archiving is best-effort and must never crash a checkpoint write: the
    Backlot watcher may hold the file open (Windows denies renames of open
    files), so we copy rather than move, and swallow archival I/O failures.
    """
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        existing = {}
    if existing.get("status") == "in_progress":
        return

    try:
        import shutil
        stamp = str(existing.get("timestamp", ""))
        safe_stamp = "".join(c for c in stamp if c.isalnum()) or f"{path.stat().st_mtime_ns}"
        history_dir = path.parent / HISTORY_DIRNAME
        history_dir.mkdir(parents=True, exist_ok=True)
        target = history_dir / f"checkpoint_{stage}_{safe_stamp}.json"
        if target.exists():
            target = history_dir / f"checkpoint_{stage}_{safe_stamp}_{path.stat().st_mtime_ns}.json"
        shutil.copyfile(path, target)
    except OSError:
        import logging
        logging.getLogger(__name__).warning(
            "Could not archive superseded checkpoint %s to history/", path
        )


def _decision_log_path(pipeline_dir: Path, project_id: str) -> Path:
    return pipeline_dir / project_id / "decision_log.json"


def _merge_decision_log(
    pipeline_dir: Path, project_id: str, new_log: dict[str, Any]
) -> tuple[Path, Optional[bytes]]:
    """Append new decisions to the project-level decision log.

    Each stage may produce decisions. This function merges them into a
    single cumulative file so reviewers and the bench can inspect the
    full audit trail.

    Returns (log_path, original_bytes_or_None) so the caller can roll the
    log back if the checkpoint it belongs to fails to commit — the ruling
    and its checkpoint move together or not at all.
    """
    path = _decision_log_path(pipeline_dir, project_id)
    original: Optional[bytes] = path.read_bytes() if path.exists() else None
    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {
            "version": "1.0",
            "project_id": project_id,
            "decisions": [],
        }

    existing_ids = {d["decision_id"] for d in existing.get("decisions", [])}
    for decision in new_log.get("decisions", []):
        if decision.get("decision_id") not in existing_ids:
            existing["decisions"].append(decision)

    # The cumulative log is audit state: validate the MERGED result before
    # touching disk, and swap atomically so a failed write can never leave a
    # truncated or invalid decision log behind.
    try:
        validate_artifact("decision_log", existing)
    except Exception as exc:
        raise CheckpointValidationError(
            f"Merged decision log would be invalid — refusing to write: {exc}"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, existing)
    return path, original


def _restore_decision_log(path: Path, original: Optional[bytes]) -> None:
    """Roll the cumulative decision log back to its pre-merge state."""
    if original is None:
        path.unlink(missing_ok=True)
        return
    atomic_write_bytes(path, original)


def write_checkpoint(
    pipeline_dir: Path,
    project_id: str,
    stage: str,
    status: str,
    artifacts: dict[str, Any],
    *,
    pipeline_type: Optional[str] = None,
    style_playbook: Optional[str] = None,
    checkpoint_policy: str = "guided",
    human_approval_required: bool = False,
    human_approved: bool = False,
    review: Optional[dict] = None,
    cost_snapshot: Optional[dict] = None,
    error: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Path:
    """Write a checkpoint file for a pipeline stage."""
    # Backfill identity fields from the project marker so omitted kwargs
    # cannot bypass either gate enforcement or style validation.
    marker = None
    marker_path = pipeline_dir / project_id / PROJECT_MARKER_FILENAME
    if marker_path.exists() and (not pipeline_type or not style_playbook):
        try:
            with open(marker_path, encoding="utf-8") as f:
                marker = json.load(f)
        except (json.JSONDecodeError, OSError):
            marker = None
    if isinstance(marker, dict):
        if not pipeline_type and marker.get("pipeline_type"):
            pipeline_type = marker["pipeline_type"]
        if not style_playbook and marker.get("style_playbook"):
            style_playbook = marker["style_playbook"]
    _validate_style_playbook(style_playbook)

    # The manifest a project runs under is its signed pin (lib.pipeline_pin);
    # ``pipeline_type`` stays the bare name in the checkpoint and the tuple
    # {name, version, manifest_digest} rides alongside it.
    pin = _pin_for(pipeline_dir, project_id, pipeline_type)
    if pin is not None:
        pipeline_type = pin.name
        pipeline_ref = _effective_ref(pipeline_dir, project_id, pin.name)
    else:
        pipeline_ref = pipeline_type

    valid_stages = (
        set(get_pipeline_stages(pipeline_ref)) if pipeline_ref
        else ALL_KNOWN_STAGES
    )
    if stage not in valid_stages:
        raise ValueError(
            f"Invalid stage: {stage!r} for pipeline {pipeline_type!r}. "
            f"Valid stages: {sorted(valid_stages)}"
        )

    # --- Gate enforcement (GI-4) ---
    # The pipeline manifest is the binding source of truth for whether a stage
    # gates on human approval; a caller may gate MORE strictly (e.g. a
    # manual_all checkpoint policy) but never less. A gated stage can only be
    # written "completed" with explicit evidence of approval
    # (human_approved=True). Skipping a gate is a hard error.
    #
    # Enforcement happens at write time only: pre-existing checkpoints written
    # before gating (or by hand) still read as completed — deliberate
    # back-compat so in-flight and legacy projects keep resuming.
    manifest_gate = _stage_requires_approval(pipeline_ref, stage)
    gated = bool(manifest_gate) or human_approval_required
    if gated:
        human_approval_required = True
        if status == "completed" and not human_approved:
            gate_source = (
                f"human_approval_default: true in the {pipeline_type!r} manifest"
                if manifest_gate
                else "human_approval_required=True was passed by the caller"
            )
            raise CheckpointValidationError(
                f"GATE VIOLATION: stage {stage!r} requires human approval "
                f"({gate_source}) but status='completed' was written without "
                f"human_approved=True. Correct protocol: write "
                f"status='awaiting_human', present the artifact summary to the "
                f"user, END YOUR TURN, and only after the user approves "
                f"re-write with status='completed', human_approved=True."
            )

    _enforce_stage_prerequisites(
        pipeline_dir,
        project_id,
        pipeline_ref,
        stage,
        status,
    )

    manifest, stage_spec = _manifest_stage_spec(pipeline_ref, stage)
    _enforce_manifest_artifact_contract(
        pipeline_dir,
        project_id,
        pipeline_ref,
        stage,
        status,
        artifacts,
        stage_spec,
    )

    checkpoint = {
        "version": "1.0",
        "project_id": project_id,
        "pipeline_type": pipeline_type or "unknown",
        "stage": stage,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checkpoint_policy": checkpoint_policy,
        "human_approval_required": human_approval_required,
        "human_approved": human_approved,
        "artifacts": artifacts,
    }
    if pin is not None:
        checkpoint["pipeline"] = pin.to_dict()
        checkpoint["predecessors"] = _predecessor_digests(pipeline_dir, project_id, pipeline_ref, stage)
    if style_playbook is not None:
        checkpoint["style_playbook"] = style_playbook
    if review is not None:
        checkpoint["review"] = review
    if cost_snapshot is not None:
        checkpoint["cost_snapshot"] = cost_snapshot
    if error is not None:
        checkpoint["error"] = error
    if metadata is not None:
        checkpoint["metadata"] = metadata

    carries_decisions = "decision_log" in artifacts and isinstance(
        artifacts["decision_log"], dict
    )
    if carries_decisions:
        # Write decision_log_ref into proposal_packet and render_report
        # artifacts if they are present in this checkpoint. The ref path is
        # deterministic, so it can be injected BEFORE any validation runs.
        log_ref = str(_decision_log_path(pipeline_dir, project_id))
        for artifact_key in ("proposal_packet", "render_report"):
            if artifact_key in artifacts and isinstance(artifacts[artifact_key], dict):
                plan_or_top = artifacts[artifact_key]
                # proposal_packet stores it under production_plan
                if artifact_key == "proposal_packet":
                    plan = plan_or_top.get("production_plan")
                    if isinstance(plan, dict):
                        plan["decision_log_ref"] = log_ref
                else:
                    plan_or_top["decision_log_ref"] = log_ref

    # ALL validation happens before ANY state is persisted. A rejected
    # checkpoint must leave the project directory exactly as it found it —
    # previously the cumulative decision log was merged before validation,
    # so rejected checkpoints could still corrupt the audit trail.
    validate_checkpoint(checkpoint)

    if manifest is not None and manifest.get("validation_profile") == "authored-canon":
        from lib.canon_enforcement import enforce_authored_canon

        _halt_on_indeterminate_paid_calls(pipeline_dir / project_id)
        enforce_authored_canon(pipeline_dir, project_id, stage, status, artifacts, pin=pin)

    path = _checkpoint_path(pipeline_dir, project_id, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize the checkpoint FIRST — an unserializable payload must fail
    # before the decision log moves. Then commit the decision log, then swap
    # the checkpoint in atomically. If the checkpoint swap fails after the
    # log committed, roll the log back: a canon ruling must never exist in
    # the audit trail without the checkpoint that carried it.
    payload = (json.dumps(checkpoint, indent=2) + "\n").encode("utf-8")

    log_rollback: Optional[tuple[Path, Optional[bytes]]] = None
    if carries_decisions:
        log_rollback = _merge_decision_log(
            pipeline_dir, project_id, artifacts["decision_log"]
        )

    try:
        # Preserve run history: a superseded completed/awaiting_human
        # checkpoint is copied to history/ (stage versioning, gate audit
        # trail, replay).
        _archive_superseded_checkpoint(path, stage)
        # Unique temp name + fsync + rename; the temp is removed on failure.
        atomic_write_bytes(path, payload)
    except BaseException:
        if log_rollback is not None:
            _restore_decision_log(*log_rollback)
        raise

    return path


def _predecessor_digests(
    pipeline_dir: Path, project_id: str, pipeline_ref: Optional[str], stage: str
) -> list[dict[str, str]]:
    """{stage, checkpoint_digest} for every completed, non-invalidated
    predecessor checkpoint on disk (bound into 1.2 checkpoints so a later
    slice can narrow invalidation to exact descendants)."""
    if not pipeline_ref:
        return []
    stages = get_pipeline_stages(pipeline_ref)
    if stage not in stages:
        return []
    invalidated = invalidated_stages(pipeline_dir, project_id, pipeline_ref)
    out: list[dict[str, str]] = []
    for predecessor in stages[: stages.index(stage)]:
        path = _checkpoint_path(pipeline_dir, project_id, predecessor)
        if not path.exists() or predecessor in invalidated:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if _pin_mismatch(pipeline_dir, project_id, pipeline_ref, predecessor, path, data):
            continue
        if data.get("status") == "completed":
            out.append({"stage": predecessor, "checkpoint_digest": checkpoint_digest(path)})
    return out


def _halt_on_indeterminate_paid_calls(project_dir: Path) -> None:
    """Refuse to touch project state while a paid call has an unknown outcome.

    Thin hook over ``tools.cost_tracker.resume_check``: a reservation that is
    still ``submitting`` with no provider request id may already have been
    charged. The human reconciles it by hand; the pipeline never resubmits.
    """
    from tools.cost_tracker import IndeterminatePaidCallError, resume_check

    try:
        resume_check(project_dir)
    except IndeterminatePaidCallError as exc:
        ids = [r.get("reservation_id") for r in exc.reservations]
        raise CheckpointValidationError(
            f"PAID CALL INDETERMINATE: {len(ids)} reservation(s) were submitted "
            f"with no recorded provider request id or outcome: {ids}. Halting "
            f"— reconcile each with the provider by hand (mark completed or "
            f"failed in {project_dir / 'cost-reservations.jsonl'}) before "
            f"writing any further checkpoint. Never resubmit automatically."
        ) from exc


def acquire_run_lease(pipeline_dir: Path, project_id: str, wall_time_minutes: float):
    """Acquire the single-writer run lease for ``pipeline_dir/project_id``.

    Thin wrapper over ``lib.run_lease.acquire``. The orchestrator calls this
    once at run start and holds the returned ``Lease`` (context manager;
    call ``heartbeat()`` periodically) for the whole run. Raises
    ``lib.run_lease.LeaseHeldError`` when another live session owns it.
    """
    from lib.run_lease import acquire

    return acquire(Path(pipeline_dir) / project_id, wall_time_minutes)


def read_checkpoint(
    pipeline_dir: Path, project_id: str, stage: str
) -> Optional[dict[str, Any]]:
    """Read a checkpoint file. Returns None if not found."""
    path = _checkpoint_path(pipeline_dir, project_id, stage)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        checkpoint = json.load(f)
    validate_checkpoint(checkpoint)
    return checkpoint


def get_latest_checkpoint(
    pipeline_dir: Path, project_id: str
) -> Optional[dict[str, Any]]:
    """Find the most recent checkpoint for a project (by file mtime)."""
    project_dir = pipeline_dir / project_id
    if not project_dir.exists():
        return None

    checkpoints = sorted(
        project_dir.glob("checkpoint_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not checkpoints:
        return None

    with open(checkpoints[0], encoding="utf-8") as f:
        checkpoint = json.load(f)
    validate_checkpoint(checkpoint)
    return _project_invalidation(pipeline_dir, project_id, checkpoint)


def _project_invalidation(pipeline_dir: Path, project_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Return the checkpoint as read, plus ``invalidated_by`` when a retired
    or replaced look/headshot invalidates it. The file is never rewritten;
    an invalidated checkpoint is not ``completed`` for resume purposes."""
    if checkpoint.get("status") != "completed":
        return checkpoint
    try:
        invalidated = invalidated_stages(pipeline_dir, project_id, checkpoint.get("pipeline_type"))
    except CheckpointValidationError:
        return checkpoint
    hit = invalidated.get(checkpoint.get("stage"))
    if hit is not None:
        checkpoint = dict(checkpoint)
        checkpoint["invalidated_by"] = hit.to_dict()
    return checkpoint


def get_completed_stages(
    pipeline_dir: Path, project_id: str, pipeline_type: str | None = None
) -> list[str]:
    """Return list of stages that have a completed checkpoint.

    When pipeline_type is provided, only checks stages defined in that
    pipeline's manifest — preventing false positives from leftover
    checkpoints of a different pipeline type.
    """
    ref = _effective_ref(pipeline_dir, project_id, pipeline_type) if pipeline_type else None
    stages_to_check = get_pipeline_stages(ref or pipeline_type)
    invalidated = invalidated_stages(pipeline_dir, project_id, pipeline_type) if pipeline_type else {}
    completed = []
    for stage in stages_to_check:
        cp = read_checkpoint(pipeline_dir, project_id, stage)
        if cp and cp.get("status") == "completed" and stage not in invalidated:
            completed.append(stage)
    return completed


def get_next_stage(
    pipeline_dir: Path, project_id: str, pipeline_type: str | None = None
) -> Optional[str]:
    """Determine the next stage to run based on completed checkpoints.

    Uses pipeline-specific stage order so that pipelines with different
    stage sequences (e.g. cinematic vs explainer) progress correctly.
    """
    ref = _effective_ref(pipeline_dir, project_id, pipeline_type) if pipeline_type else None
    stages = get_pipeline_stages(ref or pipeline_type) if pipeline_type else STAGES
    completed = set(get_completed_stages(pipeline_dir, project_id, pipeline_type))
    for stage in stages:
        if stage not in completed:
            return stage
    return None

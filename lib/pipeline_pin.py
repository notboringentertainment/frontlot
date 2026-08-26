"""Per-project pipeline manifest pin (plan D10, review rounds 3–5).

Which manifest VERSION a project runs under is a signed decision, not a
file: the pin is the unique tip of the project's ``pipeline_migration``
approval receipt chain. Each receipt's record is
``{pipeline_name, version, manifest_digest}`` and its signed envelope names
``supersedes_receipt_id`` (None for the first). Rules:

- receipts form a monotonic supersession chain: a receipt may only supersede
  the current tip, and every receipt except the first must supersede one;
- the loader accepts only the UNIQUE ledger tip — two unsuperseded receipts,
  a dangling ``supersedes_receipt_id`` or a digest that no longer matches the
  manifest file all fail closed (``PipelinePinError``);
- a downgrade is a new human-approved receipt superseding the tip; editing
  ``project.json`` never changes the pin — ``pipeline_manifest_version`` there
  is a cache verified against the tip on every resolution.

Pipelines that ship no ``<name>@<version>.yaml`` variants (cinematic,
talking-head ...) resolve to their bare manifest and need no receipt.
For pipelines that DO (authored-film), a project with no migration receipt
resolves to the DEFAULT_VERSION ("1.1") so existing projects keep running;
``prepare_migration_request`` writes the gate request that pins them
explicitly — it mints nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from lib.canonical_json import record_sha256
from lib.pipeline_loader import (
    load_pipeline_readonly,
    manifest_digest,
    manifest_versions,
    parse_pipeline_ref,
)

MIGRATION_KIND = "pipeline_migration"
DEFAULT_VERSIONS = {"authored-film": "1.1"}
PROJECT_MARKER_FILENAME = "project.json"
CACHE_FIELD = "pipeline_manifest_version"


class PipelinePinError(RuntimeError):
    """The project's manifest pin cannot be resolved unambiguously."""


@dataclass(frozen=True)
class PinnedPipeline:
    name: str
    version: str
    manifest_digest: str
    receipt_id: Optional[str]
    bound_checkpoints: tuple[tuple[str, str], ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version, "manifest_digest": self.manifest_digest}

    def binds_checkpoint(self, stage: str, checkpoint_digest: str) -> bool:
        """True iff the migration receipt bound this exact checkpoint file
        (its stage + sha256) at migration time — the only way a checkpoint
        written under another tuple (a legacy one) can satisfy this pin."""
        return (stage, checkpoint_digest) in self.bound_checkpoints


def checkpoint_digests_on_disk(project_dir: Path | str) -> list[dict[str, str]]:
    """``[{stage, checkpoint_digest}]`` for every ``checkpoint_<stage>.json``
    in the project dir, sorted by stage — the set a migration receipt binds
    (Slice A #10). Computed from the bytes on disk at request AND at signing
    time; the gate refuses to sign if they differ."""
    import hashlib

    project_dir = Path(project_dir)
    out: list[dict[str, str]] = []
    for path in sorted(project_dir.glob("checkpoint_*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        stage = path.name[len("checkpoint_"):-len(".json")]
        if not stage:
            continue
        out.append({"stage": stage, "checkpoint_digest": hashlib.sha256(path.read_bytes()).hexdigest()})
    return out


def migration_record(
    pipeline_name: str, version: str, digest: str, bound_checkpoints: Optional[list[dict[str, str]]] = None
) -> dict[str, Any]:
    """The record hashed into a pipeline_migration receipt: the manifest
    tuple plus the digest set of every checkpoint that existed at migration
    time (#10). Readers accept a legacy checkpoint only if it is in this set."""
    bound = []
    for item in bound_checkpoints or []:
        stage, cd = item.get("stage"), item.get("checkpoint_digest")
        if not isinstance(stage, str) or not stage or not isinstance(cd, str) or len(cd) != 64:
            raise PipelinePinError(f"bound checkpoint entry {item!r} is not {{stage, checkpoint_digest}}")
        bound.append({"stage": stage, "checkpoint_digest": cd})
    bound.sort(key=lambda e: (e["stage"], e["checkpoint_digest"]))
    return {
        "pipeline_name": pipeline_name,
        "version": version,
        "manifest_digest": digest,
        "bound_checkpoints": bound,
    }


def _bound_set(record: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    bound = record.get("bound_checkpoints")
    if bound is None:
        return ()
    if not isinstance(bound, list):
        raise PipelinePinError("pipeline_migration record bound_checkpoints must be a list")
    out = []
    for item in bound:
        if not isinstance(item, dict):
            raise PipelinePinError("pipeline_migration record bound_checkpoints entries must be objects")
        out.append((str(item.get("stage")), str(item.get("checkpoint_digest"))))
    return tuple(out)


def _chain_tip(receipts: list[dict], pipeline_name: str) -> Optional[dict]:
    """Walk the supersession chain BACKWARD from its unique tip and return
    the tip (None when there are no receipts). Slice A #15: the walk must
    reach a root (``supersedes_receipt_id`` None) without revisiting a
    receipt, and must visit every migration receipt for this pipeline
    exactly once — a disconnected cycle or a second root fails closed."""
    rows = [
        r for r in receipts
        if isinstance(r.get("record"), dict) and r["record"].get("pipeline_name") == pipeline_name
    ]
    if not rows:
        return None
    by_id: dict[str, dict] = {}
    for r in rows:
        rid = r.get("receipt_id")
        if not isinstance(rid, str) or not rid or rid in by_id:
            raise PipelinePinError(f"pipeline_migration chain for {pipeline_name!r} has a duplicate or missing receipt_id {rid!r}")
        by_id[rid] = r
    superseded = {r.get("supersedes_receipt_id") for r in rows if r.get("supersedes_receipt_id") is not None}
    tips = [r for r in rows if r["receipt_id"] not in superseded]
    if len(tips) != 1:
        raise PipelinePinError(
            f"pipeline_migration chain for {pipeline_name!r} has {len(tips)} tips "
            f"({[t['receipt_id'] for t in tips]}); exactly one unsuperseded receipt is required"
        )
    tip = tips[0]
    visited: list[str] = []
    seen: set[str] = set()
    current: Optional[dict] = tip
    while current is not None:
        rid = current["receipt_id"]
        if rid in seen:
            raise PipelinePinError(
                f"pipeline_migration chain for {pipeline_name!r} cycles at {rid} "
                f"(walk: {' <- '.join(visited + [rid])})"
            )
        seen.add(rid)
        visited.append(rid)
        prev = current.get("supersedes_receipt_id")
        if prev is None:
            break
        if not isinstance(prev, str) or prev not in by_id:
            raise PipelinePinError(f"pipeline_migration receipt {rid} supersedes unknown receipt {prev!r}")
        current = by_id[prev]
    unvisited = sorted(set(by_id) - seen)
    if unvisited:
        raise PipelinePinError(
            f"pipeline_migration chain for {pipeline_name!r}: receipts {unvisited} are not on the "
            f"root-to-tip path (disconnected chain or cycle); every migration receipt must be visited exactly once"
        )
    return tip


def _read_marker(project_dir: Path) -> dict[str, Any]:
    path = project_dir / PROJECT_MARKER_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def pinned_pipeline(
    project_dir: Path | str, pipeline_type: str, *, project_id: Optional[str] = None
) -> PinnedPipeline:
    """Resolve the manifest tuple a project runs under.

    ``pipeline_type`` may be bare (``authored-film``) or explicit
    (``authored-film@1.2``); an explicit version that disagrees with the
    signed pin is an error, never an override.
    """
    project_dir = Path(project_dir)
    name, explicit = parse_pipeline_ref(pipeline_type)
    versions = manifest_versions(name)
    if not versions:
        manifest = load_pipeline_readonly(name)
        version = str(manifest.get("version"))
        if explicit is not None and explicit != version:
            raise PipelinePinError(
                f"pipeline {name!r} has no versioned manifests; {explicit!r} is not available"
            )
        return PinnedPipeline(name, version, manifest_digest(name), None)

    from lib.receipts import verified_approvals

    receipts = verified_approvals(project_dir, MIGRATION_KIND, entity_id=name, project_id=project_id)
    tip = _chain_tip(receipts, name)
    if tip is None:
        version = DEFAULT_VERSIONS.get(name, versions[0])
        receipt_id = None
    else:
        version = str(tip["record"].get("version"))
        receipt_id = tip["receipt_id"]
    if version not in versions:
        raise PipelinePinError(
            f"project pins {name}@{version} but no pipeline_defs/{name}@{version}.yaml exists"
        )
    digest = manifest_digest(f"{name}@{version}")
    bound = _bound_set(tip["record"]) if tip is not None else ()
    if tip is not None and tip["record"].get("manifest_digest") != digest:
        raise PipelinePinError(
            f"pipeline_migration receipt {receipt_id} binds {name}@{version} to digest "
            f"{tip['record'].get('manifest_digest')} but the manifest file now hashes to {digest} "
            f"— a changed manifest needs a new human-approved migration receipt"
        )
    if explicit is not None and explicit != version:
        raise PipelinePinError(
            f"checkpoint names {name}@{explicit} but the project's signed pin is {name}@{version}"
        )
    cached = _read_marker(project_dir).get(CACHE_FIELD)
    if cached is not None and str(cached) != version:
        raise PipelinePinError(
            f"project.json {CACHE_FIELD}={cached!r} disagrees with the signed pin {version!r}; "
            f"the cache is verified, never authoritative — run refresh_cache after the migration receipt"
        )
    return PinnedPipeline(name, version, digest, receipt_id, bound)


def refresh_cache(project_dir: Path | str, pipeline_type: str) -> PinnedPipeline:
    """Rewrite project.json's ``pipeline_manifest_version`` from the signed pin."""
    project_dir = Path(project_dir)
    marker = _read_marker(project_dir)
    marker.pop(CACHE_FIELD, None)
    path = project_dir / PROJECT_MARKER_FILENAME
    if marker:
        path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    pin = pinned_pipeline(project_dir, pipeline_type)
    marker[CACHE_FIELD] = pin.version
    path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return pin


def prepare_migration_request(
    project_dir: Path | str,
    project_id: str,
    pipeline_name: str,
    version: str,
    *,
    request_id: Optional[str] = None,
    summary: Optional[str] = None,
) -> Path:
    """Write a gate request pinning ``pipeline_name@version`` (mints nothing).

    The request supersedes the current chain tip when one exists. A human
    approves it with ``scripts/gate_approve.py`` from a terminal. The one-time
    migration for existing projects is exactly this call with version 1.1.
    """
    project_dir = Path(project_dir)
    if version not in manifest_versions(pipeline_name):
        raise PipelinePinError(f"no manifest pipeline_defs/{pipeline_name}@{version}.yaml")
    digest = manifest_digest(f"{pipeline_name}@{version}")
    from lib.receipts import verified_approvals

    tip = _chain_tip(verified_approvals(project_dir, MIGRATION_KIND, entity_id=pipeline_name), pipeline_name)
    record = migration_record(pipeline_name, version, digest, checkpoint_digests_on_disk(project_dir))
    request_id = request_id or f"pipeline-migration-{version.replace('.', '-')}"
    request = {
        "request_id": request_id,
        "project_id": project_id,
        "stage": "pipeline",
        "scope": f"pipeline:{pipeline_name}",
        "kind": MIGRATION_KIND,
        "entity_id": pipeline_name,
        "artifact": None,
        "pipeline_version": version,
        "approval_record": record,
        "envelope": {"supersedes_receipt_id": tip["receipt_id"] if tip else None},
        "source_checkpoint_digest": None,
        "summary": summary or (
            f"Pin project {project_id!r} to pipeline manifest {pipeline_name}@{version} "
            f"(digest {digest}); binds {len(record['bound_checkpoints'])} existing checkpoint file(s). "
            + (f"Supersedes migration receipt {tip['receipt_id']}." if tip else "First pin for this project.")
        ),
        "preview_paths": [],
    }
    req_dir = project_dir / ".gate-requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / f"{request_id}.json"
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return path


def record_sha256_of(record: dict) -> str:
    return record_sha256(record)

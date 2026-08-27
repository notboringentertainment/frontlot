#!/usr/bin/env python3
"""Human gate handler — the ONLY path that mints gate tokens (PLAN §5).

Directors never approve anything. They write a request file under
``projects/<slug>/.gate-requests/<request_id>.json`` describing what needs a
human decision, write the checkpoint ``awaiting_human``, and end their turn.

The human then runs this script from a real terminal::

    python scripts/gate_approve.py --project <slug>            # list pending
    python scripts/gate_approve.py --project <slug> --request <id>

It refuses to run without an interactive TTY on stdin (checked in ``main``
and again in ``_decide``), so an agent driving a non-interactive shell cannot
mint a token for itself. There is NO programmatic decision path (round 2
#4): the decision is read only from the interactive prompt, after the
canonical record has been printed, and ``_decide`` is internal — it takes the
``Constructed`` that was displayed and refuses unless the record it rebuilds
right before minting has the same digest. ``main`` enters
``lib.gates.handler_context()`` only around that call; ``mint_gate_token``
refuses outside it. All of this is process discipline, not an OS privilege
boundary — see the trust-boundary note in ``lib/gates.py``. On approval it mints a
one-use token, consumes it through ``record_human_approval`` (signed receipt +
consumed-token ledger + per-project receipt chain), and moves the request to
``.gate-requests/done/``. A decline moves the request to
``.gate-requests/declined/`` with the note; no receipt is written.

The record that gets signed is NEVER the agent's (Slice A inspection #3).
Every kind has a gate-side constructor (``CONSTRUCTORS``) that reloads the
authoritative inputs — the resolved wayfinder ticket, the staged normalized
import and its hash, the manifest digest and the checkpoint files on disk,
the pending ``awaiting_human`` checkpoint, ``project.yaml`` — validates the
complete canonical record against its schema and rules, and prints the
exact canonical JSON that will be signed. ``approval_record`` / ``envelope``
in the request are hints: if present they must equal the constructed values
or the request is refused. The record is constructed AGAIN immediately
before the token is minted, so what was shown is what is signed.

Request file contract::

    {
      "request_id": "...", "project_id": "...", "stage": "visual_bible",
      "scope": "character:<id>",
      "kind": "hero|sheet|location|poster|storyboard_batch|config|artifact_review|
               look_lock|reference_import|pipeline_migration|headshot",
      "entity_id": "<id>" | null, "artifact": {...} | null,
      "approval_record": {...} | null,     # HINT — must equal the constructed record
      "envelope": {...} | null,            # HINT — must equal the constructed envelope
      "source_checkpoint_digest": "..." | null,
      "source_ticket_path": "...",         # look_lock: ticket path relative to the wayfinder root
      "pipeline_version": "1.2",           # pipeline_migration: the version the human is pinning
      "summary": "one paragraph for the human",
      "preview_paths": ["canon/visual/objects/<sha>.png", ...]
    }

Kind ``headshot`` is a SELECTION gate (plan Slice A′, R7#3 / inspection #6):
the request carries no approval_record. The handler validates the pending
``headshot_packet`` checkpoint against the checkpoint + artifact schemas,
verifies each candidate's bytes, and — for the chosen candidate — re-runs
the active-look, prompt_recipe, generation-receipt + lineage and origin
enforcement before constructing the signed record (``lib.headshots.headshot_record``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.canonical_json import record_sha256  # noqa: E402
from lib import gates  # noqa: E402
from lib.paths import PROJECTS_DIR  # noqa: E402
from lib.receipts import APPROVAL_KINDS, record_human_approval  # noqa: E402
from lib.state_io import atomic_move  # noqa: E402

REQUEST_DIRNAME = ".gate-requests"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED = ("request_id", "project_id", "stage", "scope", "kind", "summary")
SELECTION_KINDS = {"headshot"}
HEADSHOT_CHECKPOINT = "checkpoint_headshots.json"
_HEX = frozenset("0123456789abcdef")


class GateHandlerError(RuntimeError):
    pass


def require_tty(stdin=None) -> None:
    stream = stdin if stdin is not None else sys.stdin
    if not hasattr(stream, "isatty") or not stream.isatty():
        raise GateHandlerError(
            "gate_approve.py must be run by a human from an interactive terminal "
            "(stdin is not a TTY). Agents cannot mint gate tokens."
        )


def project_root(slug: str, projects_dir: Path | None = None) -> Path:
    root = (projects_dir or PROJECTS_DIR) / slug
    if not root.is_dir():
        raise GateHandlerError(f"no project directory at {root}")
    return root


def validate_request_id(request_id: object) -> str:
    """Strict request-id grammar: no separators, no leading dot, at most 64 chars."""
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        raise GateHandlerError(f"invalid request_id {request_id!r}: must match {REQUEST_ID_RE.pattern}")
    return request_id


def _confined(path: Path, root: Path) -> Path:
    """Resolve ``path`` and require it to sit under ``<root>/.gate-requests``."""
    base = (root / REQUEST_DIRNAME).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise GateHandlerError(f"{path} is outside {base}") from exc
    return resolved


def pending_requests(root: Path) -> list[Path]:
    d = root / REQUEST_DIRNAME
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if p.is_file())


def load_request(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise GateHandlerError(f"{path.name}: request missing fields {missing}")
    if data["kind"] not in APPROVAL_KINDS:
        raise GateHandlerError(f"{path.name}: unknown kind {data['kind']!r}")
    if data["kind"] in SELECTION_KINDS and data.get("approval_record") is not None:
        raise GateHandlerError(
            f"{path.name}: a {data['kind']} request must not carry approval_record — "
            f"the gate handler constructs the record from the pending packet"
        )
    if data.get("approval_record") is not None and not isinstance(data.get("approval_record"), dict):
        raise GateHandlerError(f"{path.name}: approval_record hint must be an object")
    if validate_request_id(data["request_id"]) != path.stem:
        raise GateHandlerError(f"{path.name}: request_id {data['request_id']!r} does not equal the file stem")
    return data


# ---- gate-side record constructors (Slice A #3) ----


@dataclass(frozen=True)
class Constructed:
    """Exactly what will be signed for a request: the canonical record, its
    signed envelope, the receipt key, and human-readable evidence lines."""

    record: dict[str, Any]
    envelope: Optional[dict[str, Any]]
    entity_id: Optional[str]
    artifact: Optional[dict[str, Any]] = None
    evidence: tuple[str, ...] = ()
    pre_commit_check: Optional[Callable[[], None]] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return record_sha256(self.record)


def _scope_key(req: dict) -> tuple[str, str]:
    scope = str(req.get("scope") or "")
    if ":" not in scope:
        raise GateHandlerError(f"scope {scope!r} is not <entity_kind>:<entity_id>")
    kind, entity_id = scope.split(":", 1)
    if not entity_id:
        raise GateHandlerError(f"scope {scope!r} names no entity")
    return kind, entity_id


def _require_entity(req: dict) -> str:
    entity_id = req.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id:
        raise GateHandlerError(f"{req.get('kind')} request needs entity_id")
    return entity_id


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _load_pending_checkpoint(root: Path, req: dict, stage: Optional[str] = None) -> tuple[Path, dict]:
    """The ``awaiting_human`` checkpoint of ``stage`` (default: the request's
    stage) for this project, schema-validated (checkpoint + per-stage artifacts)."""
    from lib.checkpoint import CheckpointValidationError, validate_checkpoint

    stage = stage or str(req["stage"])
    if not re.match(r"^[a-z][a-z0-9_]*$", stage):
        raise GateHandlerError(f"stage {stage!r} is not a valid stage name")
    path = root / f"checkpoint_{stage}.json"
    if not path.is_file() or path.is_symlink():
        raise GateHandlerError(f"no checkpoint_{stage}.json in {root}")
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateHandlerError(f"checkpoint_{stage}.json is unreadable: {exc}") from exc
    if not isinstance(checkpoint, dict):
        raise GateHandlerError(f"checkpoint_{stage}.json is not an object")
    try:
        validate_checkpoint(checkpoint)
    except CheckpointValidationError as exc:
        raise GateHandlerError(f"checkpoint_{stage}.json fails schema validation: {exc}") from exc
    if checkpoint.get("status") != "awaiting_human" or checkpoint.get("project_id") != req["project_id"]:
        raise GateHandlerError(
            f"{req['kind']} approval needs the awaiting_human {stage} checkpoint of project {req['project_id']!r}"
        )
    if checkpoint.get("stage") != stage:
        raise GateHandlerError(f"checkpoint_{stage}.json claims stage {checkpoint.get('stage')!r}")
    return path, checkpoint


def _verify_image_ref(root: Path, ref: Any, label: str) -> str:
    """The ImageRef's file must be project-local and hash to its asset_id."""
    from lib.pathsafe import PathSafetyError, resolve_input, sha256_file

    if not isinstance(ref, dict) or not _sha256_hex(ref.get("asset_id")):
        raise GateHandlerError(f"{label} has no sha256 asset_id")
    try:
        file = resolve_input(str(ref.get("path", "")), root)
    except PathSafetyError as exc:
        raise GateHandlerError(f"{label} path is not project-local: {exc}") from exc
    actual = sha256_file(file)
    if actual != ref["asset_id"]:
        raise GateHandlerError(
            f"{label} {ref.get('path')!r} hashes to {actual}, not its asset_id {ref['asset_id']} "
            f"— the file shown is not the asset in the checkpoint"
        )
    return actual


def _construct_look_lock(root: Path, req: dict) -> Constructed:
    from lib.look_ingest import (
        LookIngestError,
        active_look_for,
        confine_ticket_path,
        parse_look_ticket,
        wayfinder_root_for,
    )

    entity_kind, scope_entity = _scope_key(req)
    entity_id = _require_entity(req)
    if scope_entity != entity_id:
        raise GateHandlerError(f"scope {req['scope']!r} does not name entity_id {entity_id!r}")
    hint_env = req.get("envelope") or {}
    action = hint_env.get("action", "activate")
    try:
        current = active_look_for(root, entity_kind, entity_id, project_id=req["project_id"])
    except LookIngestError as exc:
        raise GateHandlerError(f"look_lock chain for ({entity_kind}, {entity_id}) is not readable: {exc}") from exc
    if action == "retire":
        if current is None:
            raise GateHandlerError(f"no active look for ({entity_kind}, {entity_id}) to retire")
        record = {"entity_kind": entity_kind, "entity_id": entity_id, "look_hash": current.look_hash}
        envelope = {"action": "retire", "entity_kind": entity_kind, "look_hash": current.look_hash}
        return Constructed(record, envelope, entity_id, evidence=(
            f"retires active look {current.look_hash} (receipt {current.receipt_id})",
        ))
    if action != "activate":
        raise GateHandlerError(f"look_lock action must be activate|retire, got {action!r}")
    ticket_rel = req.get("source_ticket_path")
    if not isinstance(ticket_rel, str) or not ticket_rel:
        raise GateHandlerError("look_lock request needs source_ticket_path (relative to the wayfinder root)")
    try:
        wf_root = wayfinder_root_for(root)
        ticket = confine_ticket_path(wf_root / ticket_rel, wf_root)
        look = parse_look_ticket(ticket, wayfinder_root=wf_root)
    except LookIngestError as exc:
        raise GateHandlerError(f"look ticket refused: {exc}") from exc
    if look.key != (entity_kind, entity_id):
        raise GateHandlerError(f"ticket {ticket.name} describes {look.key}, not ({entity_kind}, {entity_id})")
    if current is not None and current.look_hash == look.look_hash:
        raise GateHandlerError(f"look {look.look_hash} is already the active look for ({entity_kind}, {entity_id})")
    promotion_refs = hint_env.get("promotion_refs") or []
    if not isinstance(promotion_refs, list) or not all(isinstance(r, dict) for r in promotion_refs):
        raise GateHandlerError("promotion_refs must be a list of objects")
    envelope = {
        "action": "activate",
        "entity_kind": entity_kind,
        "look_hash": look.look_hash,
        "supersedes_look_hash": current.look_hash if current else None,
        "promotion_refs": list(promotion_refs),
        "source_ticket_ref": look.source_ticket_ref,
    }
    evidence = (
        f"ticket {ticket} (wayfinder root {wf_root})",
        f"source_ticket_ref {json.dumps(look.source_ticket_ref, sort_keys=True)}",
        f"look_hash {look.look_hash}" + (f" supersedes {current.look_hash}" if current else " (first activation)"),
    )
    return Constructed(look.payload, envelope, entity_id, evidence=evidence)


def _construct_reference_import(root: Path, req: dict) -> Constructed:
    from lib.pathsafe import PathSafetyError, resolve_input
    from lib.reference_import import (
        ORIGIN_CLASSES,
        ORIGIN_IMPORTED_SYNTHETIC,
        STAGING_DIR,
        ReferenceImportError,
        assert_origin_unique_for_signing,
        import_record,
        normalize_image_bytes,
        refuse_conflicting_origin,
        validate_import_record,
    )

    hint = req.get("approval_record") or {}
    hint_env = req.get("envelope") or {}
    origin_class = hint_env.get("origin_class", hint.get("origin_class"))
    pixel_hash = hint_env.get("normalized_pixel_hash", hint.get("normalized_pixel_hash"))
    if origin_class not in ORIGIN_CLASSES:
        raise GateHandlerError(f"reference_import needs origin_class in {ORIGIN_CLASSES}")
    if not _sha256_hex(pixel_hash):
        raise GateHandlerError("reference_import needs a 64-hex normalized_pixel_hash")
    try:
        staged = resolve_input(STAGING_DIR / f"reference-{pixel_hash}.png", root)
    except PathSafetyError as exc:
        raise GateHandlerError(f"staged normalized image for {pixel_hash} is missing or not project-local: {exc}") from exc
    data = staged.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != pixel_hash:
        raise GateHandlerError(f"staged image {staged.name} hashes to {actual}, not {pixel_hash}")
    try:
        renormalized = normalize_image_bytes(data)
    except ReferenceImportError as exc:
        raise GateHandlerError(f"staged image is not a normalizable PNG: {exc}") from exc
    if renormalized.sha256 != pixel_hash:
        raise GateHandlerError(
            f"staged image is not in normalized form (re-normalizing gives {renormalized.sha256}); "
            f"only the deterministic normalized PNG is imported"
        )
    origin_tool = hint.get("origin_tool") if origin_class == ORIGIN_IMPORTED_SYNTHETIC else None
    try:
        record = validate_import_record(import_record(origin_class, pixel_hash, origin_tool=origin_tool))
        refuse_conflicting_origin(root, pixel_hash, origin_class, project_id=req["project_id"])
    except ReferenceImportError as exc:
        raise GateHandlerError(str(exc)) from exc
    envelope = {"origin_class": origin_class, "normalized_pixel_hash": pixel_hash}
    # #12: the receipt key carries no caller metadata either — it is derived from the hash.
    entity_id = f"reference-{pixel_hash[:12]}"

    def recheck() -> None:  # #14: under the consumed token, before anything is committed
        try:
            assert_origin_unique_for_signing(root, pixel_hash, origin_class, project_id=req["project_id"])
        except ReferenceImportError as exc:
            raise GateHandlerError(f"origin conflict detected at signing: {exc}") from exc

    evidence = (
        f"staged normalized PNG {staged} ({renormalized.width}x{renormalized.height}, sha256 {pixel_hash})",
        f"attestation (fixed): {record['attestation_text']!r}",
    )
    return Constructed(record, envelope, entity_id, evidence=evidence, pre_commit_check=recheck)


def _construct_pipeline_migration(root: Path, req: dict) -> Constructed:
    from lib.pipeline_loader import manifest_digest, manifest_versions
    from lib.pipeline_pin import (
        MIGRATION_KIND,
        PipelinePinError,
        _chain_tip,
        checkpoint_digests_on_disk,
        migration_record,
    )
    from lib.receipts import verified_approvals

    pipeline_name = _require_entity(req)
    hint = req.get("approval_record") or {}
    version = req.get("pipeline_version", hint.get("version"))
    if hint.get("pipeline_name", pipeline_name) != pipeline_name:
        raise GateHandlerError("pipeline_migration record hint names another pipeline than entity_id")
    if not isinstance(version, str) or version not in manifest_versions(pipeline_name):
        raise GateHandlerError(
            f"pipeline_migration needs a version in {manifest_versions(pipeline_name)}, got {version!r}"
        )
    digest = manifest_digest(f"{pipeline_name}@{version}")
    bound = checkpoint_digests_on_disk(root)
    try:
        record = migration_record(pipeline_name, version, digest, bound)
        tip = _chain_tip(
            verified_approvals(root, MIGRATION_KIND, entity_id=pipeline_name, project_id=req["project_id"]),
            pipeline_name,
        )
    except PipelinePinError as exc:
        raise GateHandlerError(str(exc)) from exc
    envelope = {"supersedes_receipt_id": tip["receipt_id"] if tip else None}
    evidence = (
        f"manifest pipeline_defs/{pipeline_name}@{version}.yaml digest {digest}",
        f"binds {len(bound)} checkpoint file(s) on disk: " + ", ".join(f"{b['stage']}={b['checkpoint_digest'][:12]}…" for b in bound),
        f"supersedes migration receipt {tip['receipt_id']}" if tip else "first pin for this project",
    )
    return Constructed(record, envelope, pipeline_name, evidence=evidence, extra={"pipeline_name": pipeline_name})


def _construct_config(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import config_approval_record
    from lib.project_config import ProjectConfigError, read_project_config

    try:
        data, digest = read_project_config(root)
    except ProjectConfigError as exc:
        raise GateHandlerError(str(exc)) from exc
    record = config_approval_record(digest)
    entity_id = req.get("entity_id") or "project_config"
    evidence = (
        f"project.yaml sha256 {digest}",
        f"budget_usd_cap {data.get('budget_usd_cap')}  default_video_endpoint {data.get('default_video_endpoint')}",
        f"provider_egress {json.dumps(data.get('provider_egress'), sort_keys=True)}",
    )
    return Constructed(record, None, str(entity_id), evidence=evidence)


def _construct_storyboard_batch(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import STORYBOARD_BATCH_ENTITY_ID, storyboard_batch_record

    _, checkpoint = _load_pending_checkpoint(root, req)
    manifest = (checkpoint.get("artifacts") or {}).get("asset_manifest")
    if not isinstance(manifest, dict):
        raise GateHandlerError("pending checkpoint carries no asset_manifest")
    frames: dict[str, str] = {}
    for asset in manifest.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("asset_class") != "storyboard_frame":
            continue
        shot_id = asset.get("shot_id")
        if not isinstance(shot_id, str) or not shot_id:
            raise GateHandlerError(f"storyboard_frame {asset.get('id')!r} has no shot_id")
        if shot_id in frames:
            raise GateHandlerError(f"shot {shot_id!r} has more than one storyboard_frame")
        frames[shot_id] = _verify_image_ref(
            root, {"asset_id": asset.get("asset_id") or asset.get("sha256") or _file_sha(root, asset), "path": asset.get("path")},
            f"storyboard_frame for shot {shot_id!r}",
        )
    if not frames:
        raise GateHandlerError("pending asset_manifest has no storyboard_frame assets")
    try:
        record = storyboard_batch_record(frames)
    except ValueError as exc:
        raise GateHandlerError(str(exc)) from exc
    evidence = tuple(f"shot {shot} -> {sha}" for shot, sha in sorted(frames.items()))
    return Constructed(record, None, STORYBOARD_BATCH_ENTITY_ID, evidence=evidence)


def _file_sha(root: Path, asset: dict) -> str:
    from lib.pathsafe import PathSafetyError, resolve_input, sha256_file

    try:
        return sha256_file(resolve_input(str(asset.get("path", "")), root))
    except PathSafetyError as exc:
        raise GateHandlerError(f"asset {asset.get('id')!r} path is not project-local: {exc}") from exc


def _bible_and_palette(root: Path, req: dict) -> tuple[dict, dict]:
    _, checkpoint = _load_pending_checkpoint(root, req)
    bible = (checkpoint.get("artifacts") or {}).get("visual_bible")
    if not isinstance(bible, dict):
        raise GateHandlerError("pending checkpoint carries no visual_bible")
    return bible, bible.get("palette") or {}


def _construct_character(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import character_approval_record, sheet_roles

    entity_id = _require_entity(req)
    bible, palette = _bible_and_palette(root, req)
    entry = next((e for e in bible.get("characters") or [] if isinstance(e, dict) and e.get("id") == entity_id), None)
    if entry is None:
        raise GateHandlerError(f"character {entity_id!r} is not in the pending visual_bible")
    evidence = []
    refs = [("hero", entry.get("hero"))] + [(role, (entry.get("sheet") or {}).get(role)) for role in sheet_roles(entry)]
    for role, ref in refs:
        if ref:
            _verify_image_ref(root, ref, f"character {entity_id!r} {role}")
            evidence.append(f"{role}: {root / ref['path']} (sha256 {ref['asset_id']})")
    return Constructed(character_approval_record(entry, palette), None, entity_id, evidence=tuple(evidence))


def _construct_location(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import location_approval_record

    entity_id = _require_entity(req)
    bible, palette = _bible_and_palette(root, req)
    entry = next((e for e in bible.get("locations") or [] if isinstance(e, dict) and e.get("id") == entity_id), None)
    if entry is None:
        raise GateHandlerError(f"location {entity_id!r} is not in the pending visual_bible")
    evidence = []
    refs = [("establishing", entry.get("establishing"))] + [(f"angle_{i}", a) for i, a in enumerate(entry.get("angles") or [])]
    for role, ref in refs:
        if ref:
            _verify_image_ref(root, ref, f"location {entity_id!r} {role}")
            evidence.append(f"{role}: {root / ref['path']} (sha256 {ref['asset_id']})")
    return Constructed(location_approval_record(entry, palette), None, entity_id, evidence=tuple(evidence))


def _construct_poster(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import POSTER_ENTITY_ID, poster_approval_record

    bible, palette = _bible_and_palette(root, req)
    poster = bible.get("poster")
    if not isinstance(poster, dict):
        raise GateHandlerError("pending visual_bible has no poster")
    evidence = []
    for role in ("key_art", "title_card", "poster_final"):
        ref = poster.get(role)
        if ref:
            _verify_image_ref(root, ref, f"poster {role}")
            evidence.append(f"{role}: {root / ref['path']} (sha256 {ref['asset_id']})")
    return Constructed(poster_approval_record(poster, palette), None, POSTER_ENTITY_ID, evidence=tuple(evidence))


def _construct_artifact_review(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import _version, artifact_review_digest

    hint = req.get("artifact") or {}
    name = hint.get("artifact_type")
    if not isinstance(name, str) or not name:
        raise GateHandlerError("artifact_review request needs artifact.artifact_type")
    _, checkpoint = _load_pending_checkpoint(root, req)
    artifact = (checkpoint.get("artifacts") or {}).get(name)
    if not isinstance(artifact, dict):
        raise GateHandlerError(f"pending checkpoint carries no artifact {name!r}")
    if "migration_status" not in artifact:
        raise GateHandlerError(f"artifact {name!r} was not migrated (no migration_status) — nothing to review")
    digest = artifact_review_digest(artifact)
    version = _version(artifact)
    artifact_tuple = {
        "artifact_type": name, "artifact_version": version, "artifact_digest": digest, "migration_status": "reviewed",
    }
    for key, value in artifact_tuple.items():
        if key in hint and hint[key] != value:
            raise GateHandlerError(f"artifact_review hint {key}={hint[key]!r} disagrees with the checkpoint ({value!r})")
    record = {"artifact_type": name, "artifact_version": version, "artifact_digest": digest}
    evidence = (f"artifact {name} v{version} digest {digest} (migration_status now {artifact.get('migration_status')!r})",)
    return Constructed(record, None, None, artifact=artifact_tuple, evidence=evidence)


# ---- headshot selection gate (R7#3, inspection #6) ----


def headshot_candidates(req: dict, root: Path) -> tuple[str, dict, list[dict]]:
    """Validate the pending headshot_packet checkpoint (checkpoint schema +
    headshot_packet schema) and each candidate's bytes. Returns
    ``(candidates_checkpoint_digest, packet_entry, candidates)``."""
    from lib.checkpoint import checkpoint_digest
    from schemas.artifacts import validate_artifact

    entity_id = _require_entity(req)
    path, checkpoint = _load_pending_checkpoint(root, req, "headshots")
    packet = (checkpoint.get("artifacts") or {}).get("headshot_packet") or {}
    try:
        validate_artifact("headshot_packet", packet)
    except Exception as exc:  # noqa: BLE001 - jsonschema/artifact errors
        raise GateHandlerError(f"headshot_packet fails its schema: {getattr(exc, 'message', exc)}") from exc
    if packet.get("state") != "pending":
        raise GateHandlerError("headshot_packet in the checkpoint is not state=pending")
    entry = next(
        (e for e in packet.get("characters") or [] if isinstance(e, dict) and e.get("entity_id") == entity_id),
        None,
    )
    if entry is None:
        raise GateHandlerError(f"character {entity_id!r} has no pending candidates in the packet")
    candidates = list(entry.get("candidates") or [])
    if not 1 <= len(candidates) <= 4:
        raise GateHandlerError(f"character {entity_id!r} has {len(candidates)} candidates; expected 1..4")
    seen: set[str] = set()
    for i, cand in enumerate(candidates):
        asset_id = cand.get("asset_id")
        if not _sha256_hex(asset_id) or asset_id in seen:
            raise GateHandlerError(f"candidate {i + 1} has a missing or duplicate asset_id")
        seen.add(asset_id)
        _verify_image_ref(root, cand, f"candidate {i + 1} preview")
    return checkpoint_digest(path), entry, candidates


def _enforce_candidate(root: Path, req: dict, entry: dict, chosen: dict) -> tuple[str, Optional[str], str]:
    """Re-run look, recipe, receipt + lineage and origin enforcement for the
    chosen candidate. Returns ``(origin, import_receipt_id, look_hash)``."""
    from lib.look_ingest import LookIngestError, active_look_for, verify_look_refs
    from lib.receipts import find_generation, normalize_prompt_recipe
    from lib.reference_import import ReferenceImportError, synthetic_import_receipt, verify_lineage

    project_id = req["project_id"]
    entity_id = entry["entity_id"]
    look_ref = entry.get("look_ref") or {}
    look_hash = look_ref.get("look_hash")
    if not _sha256_hex(look_hash):
        raise GateHandlerError("pending entry has no look_ref.look_hash")
    # (1) active look: the packet's look_ref must be the active, generation-sufficient look
    try:
        verify_look_refs(root, [{"entity_kind": "character", "entity_id": entity_id, "look_hash": look_hash}], project_id=project_id)
        active = active_look_for(root, "character", entity_id, project_id=project_id)
    except LookIngestError as exc:
        raise GateHandlerError(f"active look check failed: {exc}") from exc
    if active is None or active.receipt_id != look_ref.get("receipt_id"):
        raise GateHandlerError(
            f"packet look_ref cites receipt {look_ref.get('receipt_id')!r} but the active look_lock receipt is "
            f"{active.receipt_id if active else None}"
        )
    # (2) prompt recipe: sealed to the active look
    recipe = entry.get("prompt_recipe")
    if recipe is not None:
        try:
            recipe = normalize_prompt_recipe(recipe)
        except ValueError as exc:
            raise GateHandlerError(f"prompt_recipe invalid: {exc}") from exc
        if recipe["look_hash"] != look_hash:
            raise GateHandlerError(f"prompt_recipe names look {recipe['look_hash']}, not the active look {look_hash}")
    # (3) generation receipt + lineage
    asset_id = chosen["asset_id"]
    provenance = chosen.get("provenance") or {}
    receipt = find_generation(root, asset_id, project_id=project_id)
    if receipt is None:
        raise GateHandlerError(f"candidate {asset_id} has no verified generation receipt")
    if receipt.get("receipt_id") != provenance.get("generation_receipt_id"):
        raise GateHandlerError(
            f"candidate {asset_id} cites generation receipt {provenance.get('generation_receipt_id')!r} but the "
            f"verified receipt is {receipt.get('receipt_id')}"
        )
    try:
        verify_lineage(root, asset_id, project_id=project_id, label=f"candidate {asset_id[:12]}")
    except ReferenceImportError as exc:
        raise GateHandlerError(f"candidate lineage refused: {exc}") from exc
    # (4) origin
    kind = provenance.get("generator_kind")
    if kind != receipt.get("generator_kind"):
        raise GateHandlerError(
            f"candidate claims generator_kind {kind!r} but its receipt says {receipt.get('generator_kind')!r}"
        )
    if kind == "imported":
        attestation = synthetic_import_receipt(root, asset_id, project_id=project_id)
        if attestation is None or attestation.get("receipt_id") != receipt.get("attestation_receipt_id"):
            raise GateHandlerError(
                f"imported candidate {asset_id} has no verified imported_synthetic reference_import receipt "
                f"{receipt.get('attestation_receipt_id')!r}"
            )
        if provenance.get("attestation_receipt_id") != attestation["receipt_id"] or provenance.get("import_receipt_id") != receipt["receipt_id"]:
            raise GateHandlerError("imported candidate provenance does not cite its attestation/import receipts")
        return "imported_synthetic", str(receipt["receipt_id"]), look_hash
    if kind not in ("model", "local"):
        raise GateHandlerError(f"candidate generator_kind {kind!r} is not model|local|imported")
    wanted = {"entity_kind": "character", "entity_id": entity_id, "look_hash": look_hash}
    if wanted not in (receipt.get("look_refs") or []):
        raise GateHandlerError(
            f"generated candidate {asset_id} was not receipted with look_ref {wanted} — it was not generated "
            f"from the active look"
        )
    if recipe is not None:
        sealed = receipt.get("prompt_recipe")
        if sealed != recipe:
            raise GateHandlerError(
                f"generated candidate {asset_id} was receipted with prompt_recipe {sealed} but the packet claims {recipe}"
            )
        prompt = receipt.get("prompt")
        if not isinstance(prompt, str) or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != recipe["rendered_sha256"]:
            raise GateHandlerError(
                f"generated candidate {asset_id}: the receipted prompt does not hash to prompt_recipe.rendered_sha256"
            )
    return "generated", None, look_hash


def show_candidates(entry: dict, candidates: list[dict], root: Path, out=None) -> None:
    out = out or sys.stdout
    look_hash = (entry.get("look_ref") or {}).get("look_hash")
    print(f"\ncharacter {entry.get('entity_id')}   look_hash {look_hash}", file=out)
    for i, cand in enumerate(candidates, 1):
        kind = (cand.get("provenance") or {}).get("generator_kind")
        print(f"  [{i}] {root / cand['path']}   ({kind}, sha256 {cand['asset_id'][:12]}...)", file=out)


def build_headshot_record(root: Path, req: dict, selection: int) -> tuple[dict, dict]:
    """Construct the signed headshot record for ``selection`` (1-based) from
    the verified pending packet. Returns ``(record, envelope)``."""
    built = _construct_headshot(root, req, selection)
    return built.record, dict(built.envelope or {})


def _construct_headshot(root: Path, req: dict, selection: Optional[int] = None) -> Constructed:
    from lib.headshots import HeadshotError, active_headshots, headshot_record, prompt_recipe_sha256

    digest, entry, candidates = headshot_candidates(req, root)
    if selection is None:
        raise GateHandlerError("a headshot approval needs a candidate selection")
    if not 1 <= selection <= len(candidates):
        raise GateHandlerError(f"selection {selection} is not in 1..{len(candidates)}")
    chosen = candidates[selection - 1]
    origin, import_receipt_id, look_hash = _enforce_candidate(root, req, entry, chosen)
    try:
        record = headshot_record(
            entity_id=entry["entity_id"],
            look_hash=look_hash,
            asset_id=chosen["asset_id"],
            origin=origin,
            import_receipt_id=import_receipt_id,
            prompt_recipe_sha256=prompt_recipe_sha256(entry.get("prompt_recipe")),
            candidates_checkpoint_digest=digest,
        )
        current = active_headshots(root, project_id=req["project_id"]).get(entry["entity_id"])
    except HeadshotError as exc:
        raise GateHandlerError(str(exc)) from exc
    envelope = {
        "action": "activate",
        "entity_kind": "character",
        "look_hash": look_hash,
        "supersedes_receipt_id": current.receipt_id if current else None,
    }
    evidence = (
        f"candidate [{selection}] {root / chosen['path']} (sha256 {chosen['asset_id']}, origin {origin})",
        f"candidates checkpoint digest {digest}",
    )
    return Constructed(record, envelope, entry["entity_id"], evidence=evidence)


CONSTRUCTORS: dict[str, Callable[..., Constructed]] = {
    "look_lock": _construct_look_lock,
    "reference_import": _construct_reference_import,
    "pipeline_migration": _construct_pipeline_migration,
    "config": _construct_config,
    "storyboard_batch": _construct_storyboard_batch,
    "artifact_review": _construct_artifact_review,
    "hero": _construct_character,
    "sheet": _construct_character,
    "location": _construct_location,
    "poster": _construct_poster,
    "headshot": _construct_headshot,
}
assert set(CONSTRUCTORS) == set(APPROVAL_KINDS), "every approval kind needs a gate-side constructor"


def construct(root: Path, req: dict, *, selection: Optional[int] = None) -> Constructed:
    """Build and cross-check the record for ``req``: the constructed record
    and envelope must equal the request's hints when those are present."""
    kind = req.get("kind")
    if kind not in CONSTRUCTORS:
        raise GateHandlerError(f"unknown kind {kind!r}")
    built = CONSTRUCTORS[kind](root, req, selection) if kind in SELECTION_KINDS else CONSTRUCTORS[kind](root, req)
    hint = req.get("approval_record")
    if hint is not None and hint != built.record:
        raise GateHandlerError(
            f"request approval_record (sha256 {record_sha256(hint)}) does not equal the record constructed from "
            f"the authoritative inputs (sha256 {built.digest}) — refused; the agent's record is only a hint"
        )
    hint_env = req.get("envelope")
    if hint_env is not None and kind not in SELECTION_KINDS and hint_env != built.envelope:
        raise GateHandlerError(
            f"request envelope {json.dumps(hint_env, sort_keys=True)} does not equal the constructed envelope "
            f"{json.dumps(built.envelope, sort_keys=True)} — refused"
        )
    if req.get("entity_id") is not None and built.entity_id is not None and req["entity_id"] != built.entity_id:
        raise GateHandlerError(f"request entity_id {req['entity_id']!r} does not equal the constructed key {built.entity_id!r}")
    return built


def canonical_text(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)


def show_request(req: dict, root: Path, out=None, built: Optional[Constructed] = None) -> None:
    out = out or sys.stdout
    print(f"\n=== Gate request {req['request_id']} ===", file=out)
    print(f"stage: {req['stage']}   scope: {req['scope']}   kind: {req['kind']}", file=out)
    print(f"\n{req['summary']}\n", file=out)
    for rel in req.get("preview_paths") or []:
        p = root / rel
        print(f"  preview: {p}  {'(missing!)' if not p.exists() else ''}", file=out)
    if built is not None:
        show_constructed(built, out)


def show_constructed(built: Constructed, out=None) -> None:
    """Print exactly what will be signed: the canonical record JSON, its
    sha256, the envelope, the key and the evidence it was built from."""
    out = out or sys.stdout
    print("\n--- record to be signed (constructed by the gate from authoritative inputs) ---", file=out)
    print(canonical_text(built.record), file=out)
    print(f"record sha256: {built.digest}", file=out)
    if built.envelope is not None:
        print(f"envelope: {json.dumps(built.envelope, sort_keys=True)}", file=out)
    if built.entity_id is not None:
        print(f"entity_id: {built.entity_id}", file=out)
    if built.artifact is not None:
        print(f"artifact: {json.dumps(built.artifact, sort_keys=True)}", file=out)
    for line in built.evidence:
        print(f"  evidence: {line}", file=out)


# ---- decisions ----


def _decline(req: dict, req_path: Path, root: Path, note: str | None) -> None:
    declined = root / REQUEST_DIRNAME / "declined"
    declined.mkdir(parents=True, exist_ok=True)
    req["declined_note"] = note
    _confined(declined / req_path.name, root).write_text(json.dumps(req, indent=2), encoding="utf-8")
    _confined(req_path, root).unlink()


def _pending_request_path(req: dict, root: Path) -> Path:
    """The on-disk pending request ``req`` names, or GateHandlerError."""
    request_id = validate_request_id(req.get("request_id"))
    req_path = _confined(root / REQUEST_DIRNAME / f"{request_id}.json", root)
    if req_path.stem != request_id or not req_path.is_file():
        raise GateHandlerError(f"no pending request {request_id!r}")
    if req.get("kind") not in APPROVAL_KINDS:
        raise GateHandlerError(f"unknown kind {req.get('kind')!r}")
    return req_path


def _decline_request(req: dict, root: Path, note: str | None) -> None:
    """Record a human decline (no receipt). Reject-all on a selection kind needs a note."""
    require_tty()
    req_path = _pending_request_path(req, root)
    if req["kind"] in SELECTION_KINDS and not note:
        raise GateHandlerError("reject-all needs a note (it is logged for the regeneration round)")
    _decline(req, req_path, root, note)


def _decide(
    req: dict,
    root: Path,
    *,
    shown: Constructed,
    note: str | None,
    selection: Optional[int] = None,
) -> dict:
    """Internal: sign the record the human just approved at the prompt.

    There is no ``answer`` parameter — reaching this function IS the
    affirmative answer, and only ``main`` (after ``show_constructed``) calls
    it. ``shown`` is the ``Constructed`` that was displayed; the record is
    constructed AGAIN here from the authoritative inputs and must have the
    same digest and envelope, so the inputs cannot change between display
    and signature. Re-checks the TTY (not only in ``main``) so importing
    this module and calling ``_decide`` from a non-interactive process is
    refused; ``mint_gate_token`` additionally refuses outside
    ``gates.handler_context()``.
    """
    require_tty()
    req_path = _pending_request_path(req, root)
    kind = req["kind"]
    if not isinstance(shown, Constructed):
        raise GateHandlerError("_decide needs the Constructed record that was displayed to the human")

    built = construct(root, req, selection=selection)
    if shown.digest != built.digest or shown.record != built.record or shown.envelope != built.envelope:
        raise GateHandlerError(
            "the authoritative inputs changed between display and approval — the record shown is not the "
            "record that would be signed; re-run the request"
        )

    token = gates.mint_gate_token(
        req["project_id"], req["stage"], req["scope"], built.digest,
        user_response={"answer": "approved", "note": note, "selection": selection},
    )
    receipt = record_human_approval(
        root, req["project_id"], req["stage"], req["scope"], built.record, token, kind,
        entity_id=built.entity_id, artifact=built.artifact,
        source_checkpoint_digest=req.get("source_checkpoint_digest"),
        envelope=built.envelope,
        pre_commit_check=built.pre_commit_check,
    )
    if kind == "pipeline_migration":
        from lib.pipeline_pin import refresh_cache

        refresh_cache(root, built.extra["pipeline_name"])
    done = root / REQUEST_DIRNAME / "done"
    done.mkdir(parents=True, exist_ok=True)
    atomic_move(req_path, _confined(done / req_path.name, root))
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="project slug under projects/")
    ap.add_argument("--request", help="request id to decide; omit to list pending")
    ap.add_argument("--projects-dir", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="construct and print the record that WOULD be signed, then exit; never prompts or mints (safe for agents)")
    args = ap.parse_args(argv)

    try:
        if not args.dry_run:
            require_tty()
        root = project_root(args.project, args.projects_dir)
        reqs = pending_requests(root)
        if not args.request:
            if not reqs:
                print("no pending gate requests")
                return 0
            for p in reqs:
                r = load_request(p)
                print(f"{r['request_id']}  {r['stage']}  {r['scope']}  {r['kind']}")
            return 0
        match = [p for p in reqs if p.stem == validate_request_id(args.request)]
        if not match:
            raise GateHandlerError(f"no pending request {args.request!r}")
        req = load_request(match[0])
        show_request(req, root)
        if args.dry_run:
            if req["kind"] in SELECTION_KINDS:
                _, entry, candidates = headshot_candidates(req, root)
                show_candidates(entry, candidates, root)
                print("dry-run: selection kinds stop here (no candidate chosen).")
            else:
                show_constructed(construct(root, req))
                print("dry-run: record constructed and verified; nothing signed.")
            return 0
        selection: int | None = None
        shown: Optional[Constructed] = None
        approved = False
        if req["kind"] in SELECTION_KINDS:
            _, entry, candidates = headshot_candidates(req, root)
            show_candidates(entry, candidates, root)
            n = len(candidates)
            reject_all = False
            while True:
                raw = input(f"Type a number 1-{n} to choose, r to reject all, or q to quit without deciding: ").strip().lower()
                if raw == "q":
                    print("quit — nothing decided, request left pending")
                    return 0
                if raw == "r":
                    if input("Reject ALL candidates and regenerate? type 'reject' to confirm: ").strip().lower() == "reject":
                        reject_all = True
                        break
                    continue
                if raw.isdigit() and 1 <= int(raw) <= n:
                    selection = int(raw)
                    break
                print(f"  not understood: {raw!r} — enter a number 1-{n}, r, or q")
            if selection is not None:
                shown = construct(root, req, selection=selection)
                show_constructed(shown)
                while True:
                    a = input("Sign this record? y to sign, n to go back, q to quit: ").strip().lower()
                    if a in ("y", "n", "q"):
                        break
                    print("  enter y, n, or q")
                if a == "q":
                    print("quit — nothing decided, request left pending")
                    return 0
                approved = a == "y"
                if not approved:
                    print("not signed — request left pending; run again to choose")
                    return 0
                note = input("Note (optional): ").strip() or None
            else:
                note = input("Note (required for reject-all): ").strip() or None
                if not note:
                    print("a note is required to reject all — nothing decided, request left pending")
                    return 0
        else:
            shown = construct(root, req)
            show_constructed(shown)
            while True:
                a = input("Approve? y to sign, n to decline, q to quit without deciding: ").strip().lower()
                if a in ("y", "n", "q"):
                    break
                print("  enter y, n, or q")
            if a == "q":
                print("quit — nothing decided, request left pending")
                return 0
            approved = a == "y"
            note = input("Note (optional): ").strip() or None
        if approved and shown is not None:
            # The handler marker exists only for this call; minting is refused elsewhere.
            with gates.handler_context():
                receipt = _decide(req, root, shown=shown, note=note, selection=selection)
            print(f"approved — receipt {receipt['receipt_id']}")
        else:
            _decline_request(req, root, note)
            print("declined — no receipt written")
        return 0
    except GateHandlerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

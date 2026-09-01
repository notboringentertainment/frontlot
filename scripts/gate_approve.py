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
boundary — see the trust-boundary note in ``lib/gates.py``. Backlot's embedded
terminal is a pty owned by ``scripts/gate_sign.py``, a user process the board
attaches to over a 0600 unix socket, guarded by a per-server secret against
browser cross-origin access; it is the same process-discipline trust as the
user's Terminal, not a fourth handler. On approval it mints a one-use token,
consumes it through ``record_human_approval`` (signed receipt +
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
import os
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
from lib.receipts import APPROVAL_KINDS, ReceiptError, exact_approval, record_human_approval, verified_approvals  # noqa: E402
from lib.state_io import atomic_move, atomic_write_json  # noqa: E402

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
    if "declined_note" in data and path.parent.name == REQUEST_DIRNAME:
        _complete_committed_decline(path, data)
    return data


def _complete_committed_decline(path: Path, data: dict) -> None:
    """A pending file carrying ``declined_note`` is a decline whose move to
    declined/ was interrupted (crash between the rewrite and the rename).
    Complete the move under the approval lock, then refuse: it can never be
    displayed or approved afterwards."""
    root = path.parent.parent
    with gates.receipt_lock(str(data["project_id"]), "approval"):
        if path.is_file() and not path.is_symlink():
            declined = root / REQUEST_DIRNAME / "declined"
            declined.mkdir(parents=True, exist_ok=True)
            atomic_move(_confined(path, root), _confined(declined / path.name, root))
    raise GateHandlerError(
        f"request {data['request_id']!r} was declined (note {data.get('declined_note')!r}); "
        f"its move to declined/ is complete — nothing to decide"
    )


# Fields the CLI supplies at decision time (trusted in-memory, never read
# from disk at decision), the agent's non-authoritative hints, and the
# transition markers the gate itself writes. Every other field of a pending
# request is immutable between display and decision.
DECISION_FIELDS = frozenset({"reason", "note", "selection", "_chosen_items"})
HINT_FIELDS = frozenset({"approval_record", "envelope"})
MARKER_FIELDS = frozenset({"approval_receipt_id", "declined_note"})


def _reload_pending(req: dict, root: Path) -> tuple[Path, dict]:
    """Under the approval lock: reload the pending file ``req`` names, refuse
    if any immutable field differs from the in-memory request, and return
    the on-disk request with the trusted decision-time fields overlaid."""
    req_path = _pending_request_path(req, root)
    disk = load_request(req_path)
    skip = DECISION_FIELDS | HINT_FIELDS | MARKER_FIELDS
    changed = sorted(k for k in (set(disk) | set(req)) - skip if disk.get(k) != req.get(k))
    if changed:
        raise GateHandlerError(
            f"request {req['request_id']!r} changed on disk since it was displayed (fields {changed}); re-run the request"
        )
    current = dict(disk)
    for k in DECISION_FIELDS | HINT_FIELDS:
        if k in req:
            current[k] = req[k]
    return req_path, current


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


def _read_json_file(path: Path) -> Optional[dict]:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _bind_request_digest(req: dict, path: Path, *, required: bool) -> Optional[str]:
    """The request's ``source_checkpoint_digest`` must equal the digest of
    the pending checkpoint file it was published against (D3 item 2/3).
    ``required``: a missing/None digest is refused (sheet); otherwise a
    request without a digest is not compared (legacy kinds)."""
    from lib.checkpoint import checkpoint_digest

    bound = req.get("source_checkpoint_digest")
    if bound is None and not required:
        return None
    if not _sha256_hex(bound):
        raise GateHandlerError(
            f"{req.get('kind')} request {req.get('request_id')!r} needs a 64-hex source_checkpoint_digest bound to "
            f"{path.name} (got {bound!r}) — republish the request"
        )
    actual = checkpoint_digest(path)
    if actual != bound:
        raise GateHandlerError(
            f"{req.get('kind')} request {req.get('request_id')!r} source_checkpoint_digest {bound[:12]}… is not the "
            f"current {path.name} digest {actual[:12]}… — the checkpoint changed since the request was published; "
            f"republish the request"
        )
    return bound


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
    # D20 R1#13: an import may bind ONE cast entity (a slug from the proposal
    # cast, never a name). The binding is verified against the proposal
    # checkpoint on disk, not taken from the hint on trust.
    bound_entity = hint.get("entity_id")
    if bound_entity is not None:
        if not isinstance(bound_entity, str) or not re.match(r"^[a-z0-9-]+$", bound_entity):
            raise GateHandlerError(f"reference_import entity_id {bound_entity!r} is not a cast entity slug")
        proposal = _read_json_file(root / "checkpoint_proposal.json")
        packet = ((proposal or {}).get("artifacts") or {}).get("proposal_packet") or {}
        cast = packet.get("cast") or {}
        known = set(cast.get("character_ids") or []) | set(cast.get("location_ids") or [])
        if bound_entity not in known:
            raise GateHandlerError(
                f"reference_import binds entity {bound_entity!r}, which is not in the proposal cast "
                f"(checkpoint_proposal.json); an import is for a cast entity or for nobody"
            )
    try:
        record = validate_import_record(import_record(origin_class, pixel_hash, origin_tool=origin_tool, entity_id=bound_entity))
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
        require_migration_coverage,
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
        require_migration_coverage(root, pipeline_name, version)  # D20.7: legacy heroes must be grandfathered
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

    def pre_commit() -> None:  # re-derived under the consumed token (R3#4)
        try:
            require_migration_coverage(root, pipeline_name, version)
        except PipelinePinError as exc:
            raise GateHandlerError(str(exc)) from exc
        if checkpoint_digests_on_disk(root) != bound:
            raise GateHandlerError("checkpoint files changed between request and signing; re-run the migration request")

    return Constructed(record, envelope, pipeline_name, evidence=evidence, extra={"pipeline_name": pipeline_name},
                       pre_commit_check=pre_commit)


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
    return _bible_palette_path(root, req)[:2]


def _bible_palette_path(root: Path, req: dict) -> tuple[dict, dict, Path]:
    path, checkpoint = _load_pending_checkpoint(root, req)
    bible = (checkpoint.get("artifacts") or {}).get("visual_bible")
    if not isinstance(bible, dict):
        raise GateHandlerError("pending checkpoint carries no visual_bible")
    return bible, bible.get("palette") or {}, path


def _checkpoint_pin(root: Path, req: dict):
    """The pipeline tuple of the pending checkpoint, verified against the
    project's signed pin (inspection #2: never derived from the mutable
    project.json alone). Sheet gates exist only for authored-film."""
    from lib.pipeline_pin import PipelinePinError, pinned_pipeline

    _, checkpoint = _load_pending_checkpoint(root, req)
    tuple_ = checkpoint.get("pipeline")
    if not isinstance(tuple_, dict) or tuple_.get("name") != "authored-film" or not tuple_.get("version"):
        raise GateHandlerError("the pending visual_bible checkpoint carries no authored-film pipeline tuple; sheet gates exist only for authored-film")
    try:
        pin = pinned_pipeline(root, f"authored-film@{tuple_['version']}")
    except PipelinePinError as exc:
        raise GateHandlerError(f"cannot resolve the project's pinned pipeline: {exc}") from exc
    if pin.to_dict() != tuple_:
        raise GateHandlerError(f"pending checkpoint tuple {tuple_} is not the signed pin {pin.to_dict()}")
    return pin


def _full_sheet_check(root: Path, entry: dict, entity_id: str, req: dict) -> dict:
    """D19 R1#11: the gate runs the SAME verifier canon enforcement runs at
    checkpoint write (lineage, look binding, headshot binding, prompt-recipe
    fidelity, and under authored-film 1.3 the QC verdict chain). Returns
    ``{role: verdict_row}`` (empty under 1.2)."""
    from lib.canon_enforcement import _generation_receipt_rows, _is_hero_qc_manifest, _is_qc_manifest
    from lib.checkpoint import CheckpointValidationError
    from lib.headshots import HeadshotError, active_headshots
    from lib.look_ingest import LookIngestError, active_look_for
    from lib.project_config import ProjectConfigError, load_verified_project_config
    from lib.sheet_verify import verify_character_sheet

    pin = _checkpoint_pin(root, req)
    qc_required = _is_qc_manifest(pin)
    config = None
    if qc_required:
        try:
            config = load_verified_project_config(root)
            if _is_hero_qc_manifest(pin):
                config.require_hero_qc()
        except ProjectConfigError as exc:
            raise GateHandlerError(f"sheet QC needs a verified 1.1 project config (1.2 under 1.4): {exc}") from exc
    try:
        look = active_look_for(root, "character", entity_id)
        heads = active_headshots(root)
    except (LookIngestError, HeadshotError) as exc:
        raise GateHandlerError(str(exc)) from exc
    receipts_by_sha = {r["output_sha256"]: r for r in _generation_receipt_rows(root)}
    try:
        return verify_character_sheet(
            root, entry, active_look=look, active_headshot=heads.get(entity_id) if isinstance(heads, dict) else None,
            receipts_by_sha=receipts_by_sha, qc_required=qc_required, config=config, qc_must_be_present=qc_required,
            pin=pin,
        )
    except CheckpointValidationError as exc:
        raise GateHandlerError(str(exc)) from exc


def _construct_character(root: Path, req: dict) -> Constructed:
    from lib.canon_enforcement import character_approval_record, sheet_roles

    entity_id = _require_entity(req)

    def _load() -> tuple[dict, dict, Optional[str]]:
        bible, palette, path = _bible_palette_path(root, req)
        # D3 item 2: a sheet request is bound to the checkpoint it was
        # published against; None is refused. Hero requests compare when bound.
        bound = _bind_request_digest(req, path, required=req.get("kind") == "sheet")
        entry = next((e for e in bible.get("characters") or [] if isinstance(e, dict) and e.get("id") == entity_id), None)
        if entry is None:
            raise GateHandlerError(f"character {entity_id!r} is not in the pending visual_bible")
        return entry, palette, bound

    entry, palette, bound = _load()
    evidence = []
    refs = [("hero", entry.get("hero"))] + [(role, (entry.get("sheet") or {}).get(role)) for role in sheet_roles(entry)]
    for role, ref in refs:
        if ref:
            _verify_image_ref(root, ref, f"character {entity_id!r} {role}")
            evidence.append(f"{role}: {root / ref['path']} (sha256 {ref['asset_id']})")
    verdicts = _full_sheet_check(root, entry, entity_id, req)
    for role, row in sorted(verdicts.items()):
        warn = ", ".join(row.get("warnings") or []) or "none"
        state = "pass" if row.get("verdict") == "pass" else f"FAIL {row.get('failing_items')} accepted by qc_override"
        evidence.append(f"{role}: QC {state} (policy {row.get('policy_version')}, judge {row.get('provider')}/{row.get('model')}, "
                        f"attempt {row.get('attempt_n')}, receipt {row.get('receipt_id')}, warnings: {warn})")

    def pre_commit() -> None:
        """Under the consumed token and the approval lock: reload the
        checkpoint from disk, recompute the digest and the entry, and refuse
        on any difference from what was shown (mirrors the headshot closure)."""
        entry_now, palette_now, bound_now = _load()
        if entry_now != entry or palette_now != palette or bound_now != bound:
            raise GateHandlerError("the pending visual_bible checkpoint changed while approving; re-run the request")
        _full_sheet_check(root, entry_now, entity_id, req)

    return Constructed(character_approval_record(entry, palette), None, entity_id, evidence=tuple(evidence),
                       pre_commit_check=pre_commit)


def _show_batch_field(req: dict) -> list[str]:
    """Display convenience only — the authoritative field is reconstructed
    and compared inside the constructor; this just orients the human."""
    field = req.get("field") or []
    items = sorted({str(i) for r in field for i in r.get("failing_items") or []})
    print(f"\nBatch hero waiver — {len(field)} failed candidate(s) in the reviewed field:")
    for i, r in enumerate(field, 1):
        print(f"  {i}. {str(r.get('asset_id'))[:12]}…  FAIL {sorted(r.get('failing_items') or [])}  ({r.get('path')})")
    for it in items:
        blocks = sum(1 for r in field if it in (r.get("failing_items") or []))
        alone = sum(1 for r in field if set(r.get("failing_items") or []) == {it})
        print(f"  item {it!r}: blocks {blocks} candidate(s); accepting it alone unlocks {alone}")
    return items


def _construct_qc_override_batch(root: Path, req: dict) -> Constructed:
    """Field-bound batch waiver (qc_override record 1.1, authored-film 1.5).
    The field is RECONSTRUCTED from the verified QC chain — the request's
    field is compared, never trusted — and the signed record binds the
    manifest digest, the previewed unlock list, the look (hash AND receipt),
    the config snapshot, the request id, and the active-headshot tip. All
    live-state checks re-run at signature time because ``_decide``
    re-constructs under the approval lock."""
    from lib import qc_receipts
    from lib.canon_enforcement import _is_hero_batch_manifest
    from lib.canonical_json import record_sha256
    from lib.headshots import active_headshots
    from lib.look_ingest import active_look_for
    from lib.pipeline_pin import _read_marker, pinned_pipeline
    from lib.project_config import load_verified_project_config
    from lib.receipts import field_manifest_sha256, find_approval, verified_approvals
    from lib.sheet_qc.verify import NON_OVERRIDABLE, hero_override_field, overrides_for

    entity_id = _require_entity(req)
    pin = pinned_pipeline(root, str(_read_marker(root).get("pipeline_type") or "authored-film"))
    if not _is_hero_batch_manifest(pin):
        raise GateHandlerError(f"a batch qc_override needs authored-film 1.5; project is pinned to {pin.name}@{pin.version}")
    config = load_verified_project_config(root)
    qc = config.require_hero_qc()
    cfg_receipt = find_approval(root, "config", record_sha256=record_sha256({"config_sha256": config.digest}))
    if cfg_receipt is None:
        raise GateHandlerError("no verified config approval receipt")
    if req.get("config_sha256") != config.digest or req.get("config_approval_receipt_id") != cfg_receipt["receipt_id"]:
        raise GateHandlerError("the request's config snapshot is not the currently verified project.yaml — state moved; re-run headshot_run")
    look = active_look_for(root, "character", entity_id)
    if look is None or look.look_hash != req.get("look_hash") or look.receipt_id != req.get("look_receipt_id"):
        raise GateHandlerError("the request's look binding is not the active look (hash AND receipt) — state moved; re-run headshot_run")
    used = len(qc_receipts.hero_attempts_started(root, entity_id, look.look_hash))
    cap = int(qc.max_hero_attempts)
    if used < cap:
        raise GateHandlerError(f"the hero budget is no longer exhausted ({used} of {cap} attempts) — generation should resume; re-run headshot_run")
    if int(req.get("budget_cap") or -1) != cap:
        raise GateHandlerError("the request's budget snapshot does not match the signed config — a cap change voids the request; re-run headshot_run")
    current = active_headshots(root).get(entity_id)
    expected = req.get("expected_active_headshot_receipt_id")
    if (current.receipt_id if current else None) != expected:
        raise GateHandlerError("the active headshot changed since the request was published — refused; re-run headshot_run")
    try:
        cp = json.loads((root / "checkpoint_headshots.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cp = {}
    rejected = set(((cp.get("metadata") or {}).get("rejected_candidates") or {}).get(entity_id) or [])
    if current is not None:
        rejected.add(current.asset_id)  # the active face is never in a waiver field (inspection #9)
    field = hero_override_field(root, entity_id, look.look_hash, qc=qc, rejected=rejected, look_receipt_id=look.receipt_id)
    prior_unlocked: set[str] = set()
    for r in verified_approvals(root, "qc_override", entity_id=entity_id):
        rec = r.get("record") or {}
        if rec.get("record_version") == "1.1" and rec.get("look_hash") == look.look_hash \
                and rec.get("look_receipt_id") == look.receipt_id:
            prior_unlocked |= set(rec.get("unlocked_asset_ids") or [])
    field = [r for r in field if r["asset_id"] not in prior_unlocked]
    if not field:
        raise GateHandlerError("the reconstructed residual field is empty — nothing left to waive; re-run headshot_run")
    manifest = field_manifest_sha256(field)
    if manifest != req.get("field_manifest_sha256"):
        raise GateHandlerError("the reconstructed field does not match the request's manifest — state moved; re-run headshot_run")
    # Full-tuple comparison (post-build inspection #5): the DISPLAYED rows —
    # failing items included — must equal the reconstruction, so a doctored
    # request cannot show the human a misleading description of the field.
    shown_rows = sorted((str(r.get("qc_receipt_id")), str(r.get("asset_id")),
                         tuple(sorted(str(i) for i in r.get("failing_items") or [])),
                         str(r.get("path"))) for r in req.get("field") or [])
    real_rows = sorted((r["qc_receipt_id"], r["asset_id"], tuple(r["failing_items"]),
                        f"canon/visual/objects/{r['asset_id']}.png") for r in field)
    if shown_rows != real_rows:
        raise GateHandlerError("the request's displayed field disagrees with reconstruction (rows, failing items, or paths) — refused")
    if sorted(req.get("preview_paths") or []) != sorted(f"canon/visual/objects/{r['asset_id']}.png" for r in field):
        raise GateHandlerError("the request's preview_paths disagree with the reconstructed field — refused")
    for r in field:
        _verify_image_ref(root, {"asset_id": r["asset_id"], "path": f"canon/visual/objects/{r['asset_id']}.png"},
                          f"candidate {r['asset_id'][:12]}")
    all_items = sorted({i for r in field for i in r["failing_items"]})
    chosen_raw = req.get("_chosen_items")
    if not isinstance(chosen_raw, list) or not chosen_raw:
        raise GateHandlerError("a batch qc_override needs the human's chosen item subset")
    chosen = {str(i) for i in chosen_raw}
    unknown = sorted(chosen - set(all_items))
    if unknown:
        raise GateHandlerError(f"item_ids {unknown} are not failing items of the field (field items: {all_items})")
    if chosen & NON_OVERRIDABLE:
        raise GateHandlerError("a judge protocol failure is never overridable; re-judge")
    batch: list[dict] = []
    unlocked: list[str] = []
    evidence = [f"field of {len(field)} failed candidate(s), manifest {manifest[:12]}…, look {look.look_hash[:12]}… "
                f"(receipt {look.receipt_id}), budget {used}/{cap} under config receipt {cfg_receipt['receipt_id']}"]
    for r in field:
        failing = set(r["failing_items"])
        inherited = overrides_for(root, r["qc_receipt_id"], entity_id) & failing
        accepted = (chosen & failing) | inherited
        if failing <= accepted:
            batch.append({"qc_receipt_id": r["qc_receipt_id"], "asset_id": r["asset_id"],
                          "accepted_item_ids": sorted(accepted)})
            unlocked.append(r["asset_id"])
            inh = f" (+{sorted(inherited)} inherited from signed 1.0 overrides)" if inherited - chosen else ""
            evidence.append(f"UNLOCKS {r['asset_id'][:12]}… accepting {sorted(accepted)}{inh} — canon/visual/objects/{r['asset_id']}.png")
    batch.sort(key=lambda b: (b["qc_receipt_id"], b["asset_id"]))
    if not unlocked:
        raise GateHandlerError(f"accepting {sorted(chosen)} unlocks NO candidate — nothing to sign; choose more items or decline with a note")
    evidence.append(f"result previewed and sealed: {len(unlocked)} candidate(s) unlocked; casting stays at the selection gate")
    reason = req.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < 10 or "REPLACE" in reason:
        raise GateHandlerError("qc_override needs the human's typed reason (10+ characters); the request's placeholder is not one")
    # The request binds the headshots checkpoint it was published against
    # (post-build inspection #6).
    if req.get("source_checkpoint_digest") is None:
        raise GateHandlerError("a batch qc_override request must bind its source checkpoint digest")
    _bind_request_digest(req, root / HEADSHOT_CHECKPOINT, required=True)
    record = {"record_version": "1.1", "request_id": req["request_id"], "batch": batch,
              "field_manifest_sha256": manifest, "unlocked_asset_ids": sorted(unlocked),
              "look_hash": look.look_hash, "look_receipt_id": look.receipt_id,
              "config_approval_receipt_id": cfg_receipt["receipt_id"], "config_sha256": config.digest,
              "budget_cap": cap, "attempts_spent": used,
              "expected_active_headshot_receipt_id": expected, "reason": reason.strip()}

    def pre_commit() -> None:
        """Runs INSIDE the receipt transaction, after the one-use token —
        the last look at live state before the signature publishes
        (post-build inspection #6): checkpoint digest, look (both bindings),
        verified config, budget exhaustion, headshot tip, and the field
        manifest are all re-derived and must still hold. EVERY failure in
        here — whatever its type — surfaces as GateHandlerError so _decide
        abandons the spent-token request (round-4 #1); publication failures
        outside this callback stay pending for WAL recovery."""
        try:
            _pre_commit_body()
        except GateHandlerError:
            raise
        except BaseException as exc:  # noqa: BLE001 — typed refusal boundary:
            # even KeyboardInterrupt inside the callback must abandon the
            # spent-token request rather than leave it pending (r5 #2)
            raise GateHandlerError(f"pre-commit re-derivation failed: {exc!r}") from exc

    def _pre_commit_body() -> None:
        from lib.headshots import active_headshots as _ah
        from lib.look_ingest import active_look_for as _alf
        from lib.project_config import ProjectConfigError as _PCE, load_verified_project_config as _lvpc

        _bind_request_digest(req, root / HEADSHOT_CHECKPOINT, required=True)
        now_look = _alf(root, "character", entity_id)
        if now_look is None or now_look.look_hash != look.look_hash or now_look.receipt_id != look.receipt_id:
            raise GateHandlerError("the active look changed while approving; the field-bound waiver is void")
        try:
            now_cfg = _lvpc(root)
        except _PCE as exc:
            raise GateHandlerError(f"config no longer verifies while approving: {exc}") from exc
        if now_cfg.digest != config.digest:
            raise GateHandlerError("project.yaml changed while approving; the config snapshot is void")
        now_used = len(qc_receipts.hero_attempts_started(root, entity_id, look.look_hash))
        if now_used < int(now_cfg.require_hero_qc().max_hero_attempts):
            raise GateHandlerError("the hero budget is no longer exhausted while approving; generation should resume")
        now_tip = _ah(root).get(entity_id)
        if (now_tip.receipt_id if now_tip else None) != expected:
            raise GateHandlerError("the active headshot changed while approving; the waiver is void")
        from lib.receipts import field_manifest_sha256 as _fms
        rejected_now = rejected
        now_field = [r for r in hero_override_field(root, entity_id, look.look_hash, qc=now_cfg.require_hero_qc(), rejected=rejected_now, look_receipt_id=look.receipt_id)
                     if r["asset_id"] not in prior_unlocked]
        if _fms(now_field) != manifest:
            raise GateHandlerError("the reviewed field changed while approving; the waiver is void")

    return Constructed(record=record, envelope={"field_manifest_sha256": manifest},
                       entity_id=entity_id, evidence=tuple(evidence), pre_commit_check=pre_commit)


def _construct_qc_override(root: Path, req: dict) -> Constructed:
    """D19.4: the human accepts specific FAILED items of ONE verdict. The
    record is rebuilt from the signed verdict: the request only names the
    receipt, the item ids and the reason; everything else comes from the
    QC chain and is shown before signing. Batch requests (authored-film 1.5)
    dispatch to the field-bound constructor."""
    from lib import qc_receipts

    if req.get("batch"):
        return _construct_qc_override_batch(root, req)

    entity_id = _require_entity(req)
    rid = req.get("qc_receipt_id")
    if not isinstance(rid, str) or not rid:
        raise GateHandlerError("qc_override request needs qc_receipt_id")
    row = qc_receipts.find_verdict_by_id(root, rid)
    if row is None:
        raise GateHandlerError(f"qc receipt {rid!r} is not a verified verdict of this project")
    if row.get("entity_id") != entity_id:
        raise GateHandlerError(f"verdict {rid} is for {row.get('entity_id')!r}, request names {entity_id!r}")
    if row.get("verdict") != "fail":
        raise GateHandlerError("only a FAILED verdict can be overridden")
    if row.get("provider") == "local":
        raise GateHandlerError("deterministic pre-check failures cannot be overridden; regenerate")
    items = req.get("item_ids")
    if not isinstance(items, list) or not items:
        raise GateHandlerError("qc_override request needs item_ids (the failed items you accept)")
    from lib.sheet_qc.policy import checklist
    from lib.sheet_qc.verify import NON_OVERRIDABLE

    failing = set(row.get("failing_items") or [])
    if failing & NON_OVERRIDABLE:
        raise GateHandlerError(f"verdict {rid} failed on {sorted(failing & NON_OVERRIDABLE)} — a judge protocol failure is never overridable; re-judge")
    authoritative = {i.id for i in checklist(str(row.get("role"))) if i.severity == "fail"}
    unknown = sorted(set(items) - failing)
    if unknown:
        raise GateHandlerError(f"item_ids {unknown} are not failing items of verdict {rid} (failing: {sorted(failing)})")
    foreign = sorted(set(items) - authoritative)
    if foreign:
        raise GateHandlerError(f"item_ids {foreign} are not fail-severity checklist items for role {row.get('role')!r}")
    reason = req.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < 10 or "REPLACE" in reason:
        raise GateHandlerError("qc_override needs the human's typed reason (10+ characters); the request's placeholder is not one")
    _verify_image_ref(root, {"asset_id": row["asset_id"], "path": f"canon/visual/objects/{row['asset_id']}.png"}, "judged asset")
    notes = {i["id"]: i.get("note") for i in row.get("items") or [] if isinstance(i, dict)}
    evidence = [f"asset: {root / 'canon' / 'visual' / 'objects' / (row['asset_id'] + '.png')}",
                f"role {row.get('role')}, judge {row.get('provider')}/{row.get('model')}, attempt {row.get('attempt_n')}"]
    for i in sorted(items):
        evidence.append(f"accepting failed item {i}: judge said {notes.get(i)!r}")
    record = {"qc_receipt_id": rid, "asset_id": row["asset_id"], "role": row.get("role"),
              "item_ids": sorted(set(items)), "reason": reason.strip()}
    return Constructed(record, {"qc_receipt_id": rid}, entity_id, evidence=tuple(evidence))


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
    hero_qc, pin, config = _hero_qc_context(root, req)
    packet_version = str(packet.get("version") or "1.0")
    # The presentation cap is version-keyed (round-2 #6 / round-4 #4): 4 for
    # packets ≤1.1; the signed hero-attempt cap for a 1.2 packet (1.5 pin).
    from lib.canon_enforcement import _is_hero_batch_manifest
    batch_pin = _is_hero_batch_manifest(pin)
    cap = int(config.require_hero_qc().max_hero_attempts) if (batch_pin and packet_version == "1.2") else 4
    if not 1 <= len(candidates) <= cap:
        raise GateHandlerError(f"character {entity_id!r} has {len(candidates)} candidates; expected 1..{cap}")
    seen: set[str] = set()
    for i, cand in enumerate(candidates):
        asset_id = cand.get("asset_id")
        if not _sha256_hex(asset_id) or asset_id in seen:
            raise GateHandlerError(f"candidate {i + 1} has a missing or duplicate asset_id")
        seen.add(asset_id)
        _verify_image_ref(root, cand, f"candidate {i + 1} preview")
    # D20 (inspection #3): under 1.4 nothing is SHOWN that has not been judged —
    # every candidate is verified before the list is displayed, not only the
    # one the human picks.
    if hero_qc:
        from lib.headshot_verify import HeadshotVerifyError, verify_headshot_candidate
        from lib.look_ingest import active_look_for

        expected_pkt = "1.2" if batch_pin else "1.1"
        if packet_version != expected_pkt:
            raise GateHandlerError(f"authored-film {pin.version} selects from a headshot_packet {expected_pkt}; the pending packet is {packet.get('version')!r}")
        if batch_pin:
            # Every 1.2 selection validates the sealed run snapshot (post-build
            # inspection #8) — clean-only packets included: the select state
            # binds the config that produced the presentation.
            meta = (checkpoint.get("metadata") or {})
            sel_state = (meta.get("run_state") or {}).get(entity_id) or {}
            # round-2 inspection #7: the select snapshot is REQUIRED and fully
            # checked — an absent or wrong-mode state never skips validation.
            if sel_state.get("mode") != "select" or sel_state.get("request_id") != req.get("request_id"):
                raise GateHandlerError("no select run-state binds this request to the presented packet — re-run headshot_run")
            if sel_state.get("config_sha256") != config.digest:
                raise GateHandlerError("project.yaml changed since this packet was presented — re-run headshot_run")
            if int(sel_state.get("budget_cap") or -1) != int(config.require_hero_qc().max_hero_attempts):
                raise GateHandlerError("the hero-attempt cap changed since this packet was presented — re-run headshot_run")
            from lib.canonical_json import record_sha256 as _rs256
            from lib.receipts import find_approval as _fa
            cfg_row = _fa(root, "config", record_sha256=_rs256({"config_sha256": config.digest}))
            if cfg_row is None or sel_state.get("config_approval_receipt_id") != cfg_row.get("receipt_id"):
                raise GateHandlerError("the config approval receipt changed since this packet was presented — re-run headshot_run")
        if batch_pin and any(c.get("qc_override_receipt_id") for c in candidates):
            # A 1.2 packet whose field relied on exhaustion must still be
            # exhausted under the CURRENT config (round-4 #4): a raised cap
            # means generation should resume, not a stale field get cast.
            from lib import qc_receipts as _qr
            look_hash = str((entry.get("look_ref") or {}).get("look_hash") or "")
            used = len(_qr.hero_attempts_started(root, entity_id, look_hash))
            hero_cap = int(config.require_hero_qc().max_hero_attempts)
            if used < hero_cap:
                raise GateHandlerError(f"the hero budget is no longer exhausted ({used} of {hero_cap}); "
                                       f"the presented field is stale — re-run headshot_run to resume generation")
        look = active_look_for(root, "character", entity_id)
        for i, cand in enumerate(candidates):
            try:
                verify_headshot_candidate(root, entry, cand, active_look=look, config=config, pin=pin)
            except HeadshotVerifyError as exc:
                raise GateHandlerError(f"candidate {i + 1} is not presentable: {exc}") from exc
    return checkpoint_digest(path), entry, candidates


def _enforce_candidate(root: Path, req: dict, entry: dict, chosen: dict) -> tuple[str, Optional[str], str]:
    """Re-run look, recipe, receipt + lineage and origin enforcement for the
    chosen candidate. Returns ``(origin, import_receipt_id, look_hash)``."""
    from lib.look_ingest import LookIngestError, active_look_for, verify_look_refs
    from lib.receipts import find_generation, find_generation_by_id, normalize_prompt_recipe
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
    cited = provenance.get("generation_receipt_id")
    receipt = find_generation_by_id(root, str(cited), output_sha256=asset_id, project_id=project_id) if cited else None
    if receipt is None:
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
        attestation = synthetic_import_receipt(root, asset_id, project_id=project_id, receipt_id=receipt.get("attestation_receipt_id"))
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


def _hero_qc_context(root: Path, req: dict) -> tuple[bool, Any, Any]:
    """``(hero_qc, pin, config)`` for a headshot-stage gate: whether the
    pending checkpoint's signed pin is authored-film 1.4 and, if so, the
    verified 1.2 config (D20)."""
    from lib.canon_enforcement import _is_hero_qc_manifest
    from lib.project_config import ProjectConfigError, load_verified_project_config

    pin = _checkpoint_pin(root, req)
    if not _is_hero_qc_manifest(pin):
        return False, pin, None
    try:
        config = load_verified_project_config(root)
        config.require_hero_qc()
    except ProjectConfigError as exc:
        raise GateHandlerError(f"hero QC needs a verified 1.2 project config: {exc}") from exc
    return True, pin, config


def is_retire_request(req: dict) -> bool:
    return req.get("kind") == "headshot" and (req.get("envelope") or {}).get("action") == "retire"


def _construct_headshot_retire(root: Path, req: dict) -> Constructed:
    """D20.7 (inspection #4): retire the ACTIVE hero of a character without a
    replacement — the governed way out of a legacy hero that fails the hero
    judge and that the writer will not override. Nothing downstream survives
    it (sheets built on the hero are invalidated by the chain)."""
    from lib.headshots import HeadshotError, active_headshots

    entity_id = _require_entity(req)
    try:
        current = active_headshots(root, project_id=req["project_id"]).get(entity_id)
    except HeadshotError as exc:
        raise GateHandlerError(str(exc)) from exc
    if current is None:
        raise GateHandlerError(f"character {entity_id!r} has no active headshot to retire")
    record = {"entity_kind": "character", "entity_id": entity_id, "look_hash": current.look_hash}
    envelope = {"action": "retire", "entity_kind": "character", "look_hash": current.look_hash,
                "supersedes_receipt_id": current.receipt_id}

    def pre_commit() -> None:
        now = active_headshots(root, project_id=req["project_id"]).get(entity_id)
        if now is None or now.receipt_id != current.receipt_id:
            raise GateHandlerError("the active headshot changed while approving; re-run the request")

    evidence = (f"retires active headshot receipt {current.receipt_id} (asset {current.asset_id}); every sheet, storyboard and take "
                f"built on it is invalidated",)
    return Constructed(record, envelope, entity_id, evidence=evidence, pre_commit_check=pre_commit)


def _construct_headshot(root: Path, req: dict, selection: Optional[int] = None) -> Constructed:
    from lib.headshot_verify import HeadshotVerifyError, verify_headshot_candidate
    from lib.headshots import HeadshotError, active_headshots, headshot_record, prompt_recipe_sha256
    from lib.look_ingest import active_look_for

    if is_retire_request(req):
        return _construct_headshot_retire(root, req)
    digest, entry, candidates = headshot_candidates(req, root)
    # D3 item 3: when the request carries a digest it must be the digest of
    # the pending headshots checkpoint the candidates were read from.
    if req.get("source_checkpoint_digest") is not None:
        _bind_request_digest(req, root / HEADSHOT_CHECKPOINT, required=True)
    if selection is None:
        raise GateHandlerError("a headshot approval needs a candidate selection")
    if not 1 <= selection <= len(candidates):
        raise GateHandlerError(f"selection {selection} is not in 1..{len(candidates)}")
    chosen = candidates[selection - 1]
    origin, import_receipt_id, look_hash = _enforce_candidate(root, req, entry, chosen)
    hero_qc, pin, config = _hero_qc_context(root, req)
    evidence = [
        f"candidate [{selection}] {root / chosen['path']} (sha256 {chosen['asset_id']}, origin {origin})",
        f"candidates checkpoint digest {digest}",
    ]
    pre_commit = None
    generation_receipt_id = qc_receipt_id = None
    if hero_qc:
        # D20: the chosen candidate must carry a verifiable hero verdict; the
        # record seals what the gate relied on. (Packet version and every
        # candidate were already verified for display in headshot_candidates.)
        def _full() -> dict:
            look = active_look_for(root, "character", entry["entity_id"])
            try:
                return verify_headshot_candidate(root, entry, chosen, active_look=look, config=config, pin=pin)
            except HeadshotVerifyError as exc:
                raise GateHandlerError(str(exc)) from exc

        verdict = _full()
        generation_receipt_id = str((chosen.get("provenance") or {}).get("generation_receipt_id"))
        qc_receipt_id = str(verdict["receipt_id"])
        warn = ", ".join(verdict.get("warnings") or []) or "none"
        if chosen.get("qc_override_receipt_id"):
            state = (f"FAIL {verdict.get('failing_items')} accepted by batch override {chosen['qc_override_receipt_id']} "
                     f"(field manifest {str(chosen.get('field_manifest_sha256'))[:12]}…)")
        elif chosen.get("legacy_citations"):
            state = f"FAIL {verdict.get('failing_items')} accepted by qc_override {[c['receipt_id'] for c in chosen['legacy_citations']]}"
        else:
            state = "pass" if verdict.get("verdict") == "pass" else f"FAIL {verdict.get('failing_items')} accepted by qc_override"
        evidence.append(f"hero QC {state} (judge {verdict.get('provider')}/{verdict.get('model')}, attempt {verdict.get('attempt_n')}, "
                        f"receipt {qc_receipt_id}, warnings: {warn})")
    # Record version follows the packet contract: 1.2 under a 1.5 pin (the
    # packet carried each candidate's authority; the record copies the chosen
    # candidate's citation verbatim — round-3 #7, round-4 #1), 1.1 under 1.4.
    from lib.canon_enforcement import _is_hero_batch_manifest as _batch_pin_fn
    batch_record = hero_qc and _batch_pin_fn(pin)
    try:
        if batch_record and origin == "imported_synthetic":
            record = headshot_record(
                entity_id=entry["entity_id"], look_hash=look_hash, asset_id=chosen["asset_id"], origin=origin,
                import_receipt_id=import_receipt_id, prompt_recipe_sha256=prompt_recipe_sha256(entry.get("prompt_recipe")),
                candidates_checkpoint_digest=digest, record_version="1.2",
                generation_receipt_id=generation_receipt_id, qc_receipt_id=qc_receipt_id,
            )
        elif batch_record:
            record = headshot_record(
                entity_id=entry["entity_id"], look_hash=look_hash, asset_id=chosen["asset_id"], origin=origin,
                import_receipt_id=import_receipt_id, prompt_recipe_sha256=prompt_recipe_sha256(entry.get("prompt_recipe")),
                candidates_checkpoint_digest=digest, record_version="1.2",
                generation_receipt_id=generation_receipt_id, qc_receipt_id=qc_receipt_id,
                qc_override_receipt_id=chosen.get("qc_override_receipt_id"),
                qc_override_record_sha256=chosen.get("qc_override_record_sha256"),
                field_manifest_sha256=chosen.get("field_manifest_sha256"),
                legacy_citations=list(chosen.get("legacy_citations") or []),
            )
        else:
            record = headshot_record(
                entity_id=entry["entity_id"],
                look_hash=look_hash,
                asset_id=chosen["asset_id"],
                origin=origin,
                import_receipt_id=import_receipt_id,
                prompt_recipe_sha256=prompt_recipe_sha256(entry.get("prompt_recipe")),
                candidates_checkpoint_digest=digest,
                record_version="1.1" if hero_qc else "1.0",
                generation_receipt_id=generation_receipt_id,
                qc_receipt_id=qc_receipt_id,
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

    def pre_commit() -> None:
        """Inspection #4: under the consumed token and the approval lock,
        re-derive the whole decision — the supersession tip, the pending
        checkpoint (digest + candidates), and under 1.4 the hero verdict —
        so a concurrent approval cannot commit a stale-tip receipt."""
        try:
            now = active_headshots(root, project_id=req["project_id"]).get(entry["entity_id"])
        except HeadshotError as exc:
            raise GateHandlerError(str(exc)) from exc
        if (now.receipt_id if now else None) != envelope["supersedes_receipt_id"]:
            raise GateHandlerError(
                f"the active headshot for {entry['entity_id']!r} changed while approving (now {now.receipt_id if now else None}); "
                f"re-run the request"
            )
        digest_now, entry_now, candidates_now = headshot_candidates(req, root)
        if digest_now != digest or entry_now != entry or candidates_now != candidates:
            raise GateHandlerError("the pending headshot checkpoint changed while approving; re-run the request")
        if req.get("source_checkpoint_digest") is not None:
            _bind_request_digest(req, root / HEADSHOT_CHECKPOINT, required=True)
        if hero_qc:
            _full()

    return Constructed(record, envelope, entry["entity_id"], evidence=tuple(evidence), pre_commit_check=pre_commit)


def _construct_headshot_grandfather(root: Path, req: dict) -> Constructed:
    """D20.7: attest that the ACTIVE legacy (1.0-record) hero of a character
    passed the hero judge. Adjunct to the headshot chain: the legacy receipt
    stays the tip; this receipt binds {entity, legacy receipt, asset, look,
    verdict} and is what verify_active_headshot accepts under 1.4."""
    from lib.headshot_verify import HeadshotVerifyError, grandfather_record, require_hero_verdict
    from lib.headshots import HeadshotError, active_headshots, record_version_of
    from lib.look_ingest import LookIngestError, active_look_for
    from lib.project_config import ProjectConfigError, load_verified_project_config
    from lib.receipts import find_generation

    entity_id = _require_entity(req)
    qc_receipt_id = req.get("qc_receipt_id")  # a request field (like pipeline_version), never the record hint
    if not isinstance(qc_receipt_id, str) or not qc_receipt_id:
        raise GateHandlerError("headshot_grandfather needs the hero verdict's qc_receipt_id in the request")
    try:
        current = active_headshots(root, project_id=req["project_id"]).get(entity_id)
        look = active_look_for(root, "character", entity_id)
    except (HeadshotError, LookIngestError) as exc:
        raise GateHandlerError(str(exc)) from exc
    if current is None:
        raise GateHandlerError(f"character {entity_id!r} has no active headshot to grandfather")
    if record_version_of(current.record) != "1.0":
        raise GateHandlerError(f"the active headshot of {entity_id!r} is a {record_version_of(current.record)} record; only legacy 1.0 records are grandfathered")
    if look is None or look.look_hash != current.look_hash:
        raise GateHandlerError(f"the active headshot of {entity_id!r} was approved against a look that is not the active look; re-approve instead")
    try:
        config = load_verified_project_config(root)
        config.require_hero_qc()
    except ProjectConfigError as exc:
        raise GateHandlerError(f"grandfathering needs a verified 1.2 project config: {exc}") from exc

    def _verify() -> dict:
        try:
            return require_hero_verdict(root, qc_receipt_id=qc_receipt_id, asset_id=current.asset_id, entity_id=entity_id,
                                        active_look=look, config=config, gen_receipt=find_generation(root, current.asset_id),
                                        grandfather=True)
        except HeadshotVerifyError as exc:
            raise GateHandlerError(str(exc)) from exc

    verdict = _verify()
    try:
        record = grandfather_record(entity_id=entity_id, legacy_headshot_receipt_id=current.receipt_id, asset_id=current.asset_id,
                                    look_hash=current.look_hash, qc_receipt_id=qc_receipt_id)
    except HeadshotVerifyError as exc:
        raise GateHandlerError(str(exc)) from exc
    envelope = {"attests_receipt_id": current.receipt_id, "entity_kind": "character", "look_hash": current.look_hash}
    warn = ", ".join(verdict.get("warnings") or []) or "none"
    state = "pass" if verdict.get("verdict") == "pass" else f"FAIL {verdict.get('failing_items')} accepted by qc_override"
    evidence = (
        f"legacy headshot receipt {current.receipt_id} (asset {current.asset_id}, {root / 'canon/visual/objects' / (current.asset_id + '.png')})",
        f"hero QC {state} (judge {verdict.get('provider')}/{verdict.get('model')}, receipt {qc_receipt_id}, warnings: {warn})",
        "adjunct attestation: the legacy receipt stays the chain tip; existing sheets stay valid",
    )
    return Constructed(record, envelope, entity_id, evidence=evidence, pre_commit_check=_verify)


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
    "qc_override": _construct_qc_override,
    "headshot_grandfather": _construct_headshot_grandfather,
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
    if hint_env is not None and (kind not in SELECTION_KINDS or is_retire_request(req)) and hint_env != built.envelope:
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
    """Record a human decline (no receipt). Reject-all on a selection kind
    needs a note. Runs under the approval lock: reload + confirm still
    pending, rewrite the pending file in place with ``declined_note`` (the
    commit), then ``atomic_move`` it to declined/ — never two files, and a
    crash after the rewrite is completed by ``load_request``."""
    require_tty()
    if req.get("kind") not in APPROVAL_KINDS:
        raise GateHandlerError(f"unknown kind {req.get('kind')!r}")
    if req["kind"] in SELECTION_KINDS and not note:
        raise GateHandlerError("reject-all needs a note (it is logged for the regeneration round)")
    _pending_request_path(req, root)  # grammar + existence before any lock file is touched
    with gates.receipt_lock(str(req["project_id"]), "approval"):
        req_path, current = _reload_pending(req, root)
        current["declined_note"] = note
        atomic_write_json(req_path, current)
        declined = root / REQUEST_DIRNAME / "declined"
        declined.mkdir(parents=True, exist_ok=True)
        atomic_move(req_path, _confined(declined / req_path.name, root))


def _matching_receipts(root: Path, kind: str, entity_id: Optional[str], digest: str, bound: Optional[str]) -> list[dict]:
    """Verified receipts of ``kind`` whose (entity_id, record_sha256,
    source_checkpoint_digest) equal the reconstructed tuple."""
    return [
        r for r in verified_approvals(root, kind, entity_id=entity_id)
        if r.get("entity_id") == entity_id and r.get("record_sha256") == digest
        and r.get("source_checkpoint_digest") == bound
    ]


def _complete_done(req_path: Path, root: Path, current: dict, receipt_id: str) -> None:
    """The approve transition: rewrite the pending file in place with the
    receipt id (an unsigned marker), then move it to done/ — never two files."""
    current = dict(current, approval_receipt_id=receipt_id)
    atomic_write_json(req_path, current)
    done = root / REQUEST_DIRNAME / "done"
    done.mkdir(parents=True, exist_ok=True)
    atomic_move(req_path, _confined(done / req_path.name, root))


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

    The whole decision runs under ``gates.receipt_lock(project, "approval")``
    — the stream ``record_human_approval`` commits on — so logical-request
    uniqueness holds across processes: reload → marker/recovery lookup →
    mint → commit → enrich the pending file → move to done/.
    """
    require_tty()
    kind = req["kind"]
    if not isinstance(shown, Constructed):
        raise GateHandlerError("_decide needs the Constructed record that was displayed to the human")
    if kind not in APPROVAL_KINDS:
        raise GateHandlerError(f"unknown kind {kind!r}")
    _pending_request_path(req, root)  # grammar + existence before any lock file is touched

    with gates.receipt_lock(str(req["project_id"]), "approval"):
        req_path, current = _reload_pending(req, root)
        is_batch_override = kind == "qc_override" and bool(current.get("batch"))
        try:
            built = construct(root, current, selection=selection)
            if shown.digest != built.digest or shown.record != built.record or shown.envelope != built.envelope:
                raise GateHandlerError(
                    "the authoritative inputs changed between display and approval — the record shown is not the "
                    "record that would be signed; re-run the request"
                )
        except GateHandlerError:
            if is_batch_override:
                # State moved between display and approval: the field-bound
                # request can never be signed as displayed. Mandated order
                # (round-4 #8), under the approval lock: move pending →
                # abandoned FIRST; the runner clears its run_state on resume.
                abandoned = req_path.parent / "abandoned" / req_path.name
                abandoned.parent.mkdir(parents=True, exist_ok=True)
                os.replace(req_path, abandoned)
                print(f"request {current.get('request_id')} moved to abandoned/ — state changed; re-run headshot_run", file=sys.stderr)
            raise
        bound = current.get("source_checkpoint_digest")

        marker = current.get("approval_receipt_id")
        if marker is not None:
            # Crash after the rewrite, before the move: the pending file names
            # a receipt id. It is an unsigned marker — verify it against the
            # reconstructed tuple (explicit digest; None compared as None).
            try:
                receipt = exact_approval(
                    root, receipt_id=str(marker), kind=kind, entity_id=built.entity_id,
                    record_sha256=built.digest, source_checkpoint_digest=bound,
                )
            except ReceiptError as exc:
                raise GateHandlerError(
                    f"pending request {current['request_id']!r} names approval_receipt_id {marker!r}, which is not "
                    f"the verified receipt for this record — refused: {exc}"
                ) from exc
            _complete_done(req_path, root, current, receipt["receipt_id"])
            return receipt

        matches = _matching_receipts(root, kind, built.entity_id, built.digest, bound)
        receipt: Optional[dict] = None
        if kind == "sheet":
            # Tuple recovery (crash after commit, before the rewrite) exists
            # only for sheets, and only while the bound digest still
            # revalidates against the current checkpoint (construct did that).
            if len(matches) == 1:
                receipt = matches[0]
            elif len(matches) > 1:
                raise GateHandlerError(
                    f"{len(matches)} verified sheet receipts already match this request's tuple "
                    f"({[r.get('receipt_id') for r in matches]}); refusing to guess — a human must reconcile"
                )
        elif kind == "qc_override" and len(matches) == 1 \
                and (matches[0].get("record") or {}).get("record_version") == "1.1" \
                and (matches[0].get("record") or {}).get("request_id") == current.get("request_id"):
            # Batch records bind their request_id (round-3 #4): a crash after
            # receipt commit but before the done-move left a pending request
            # whose unique verified receipt names it. Attach and complete.
            receipt = matches[0]
        elif matches and bound is not None:
            # A request bound to a checkpoint digest whose exact tuple is
            # already signed is a duplicate of a decided request: refuse and
            # name it (never silent reuse). Unbound requests (None digest:
            # config, pipeline re-pin, a permitted re-import of the same
            # pixels) are new approvals of a repeatable record.
            raise GateHandlerError(
                f"a verified {kind} receipt already matches this request's record and checkpoint digest "
                f"({[r.get('receipt_id') for r in matches]}); refused — receipts do not bind a request id, "
                f"so the gate never silently attaches an old approval to a new request"
            )

        if receipt is None:
            token = gates.mint_gate_token(
                current["project_id"], current["stage"], current["scope"], built.digest,
                user_response={"answer": "approved", "note": note, "selection": selection},
            )
            try:
                receipt = record_human_approval(
                    root, current["project_id"], current["stage"], current["scope"], built.record, token, kind,
                    entity_id=built.entity_id, artifact=built.artifact,
                    source_checkpoint_digest=bound,
                    envelope=built.envelope,
                    pre_commit_check=built.pre_commit_check,
                )
            except GateHandlerError:
                if is_batch_override:
                    # Post-token TYPED pre-commit failure only (round-3 #1):
                    # once publication begins, any other exception leaves the
                    # request pending so WAL recovery can complete the commit —
                    # abandoning there would hide authority the WAL replays.
                    # the one-use token is spent and the field-bound request
                    # can never be signed as displayed — durably abandon it,
                    # move first, under the approval lock (round-4 #8).
                    abandoned = req_path.parent / "abandoned" / req_path.name
                    abandoned.parent.mkdir(parents=True, exist_ok=True)
                    if req_path.is_file():
                        os.replace(req_path, abandoned)
                    print(f"request {current.get('request_id')} moved to abandoned/ — state changed after the token was "
                          f"consumed; re-run headshot_run", file=sys.stderr)
                raise
            if kind == "pipeline_migration":
                from lib.pipeline_pin import refresh_cache

                refresh_cache(root, built.extra["pipeline_name"])
        _complete_done(req_path, root, current, receipt["receipt_id"])
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
            if req["kind"] in SELECTION_KINDS and not is_retire_request(req):
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
        if req["kind"] in SELECTION_KINDS and not is_retire_request(req):
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
            if req["kind"] == "qc_override":
                if req.get("batch"):
                    # Field-bound batch waiver (authored-film 1.5): the human
                    # chooses WHICH failed items to accept; the constructor
                    # reconstructs the field from the QC chain and previews
                    # exactly which candidates the choice unlocks before
                    # anything is signed.
                    items = _show_batch_field(req)
                    while True:
                        raw = input(f"Accept which failed items? (comma list from {items}, 'all', or q to quit): ").strip().lower()
                        if raw == "q":
                            print("quit — nothing decided, request left pending")
                            return 0
                        chosen = sorted(items) if raw == "all" else sorted({p.strip() for p in raw.split(",") if p.strip()})
                        if chosen and set(chosen) <= set(items):
                            break
                        print(f"  choose from {items} (comma-separated) or 'all'")
                    req["_chosen_items"] = chosen
                # The reason is the human's, typed here — never the request's text.
                while True:
                    typed = input("Why is the judge wrong on these items? (10+ characters, or q to quit): ").strip()
                    if typed.lower() == "q":
                        print("quit — nothing decided, request left pending")
                        return 0
                    if len(typed) >= 10 and "REPLACE" not in typed:
                        break
                    print("  a real reason is required (10+ characters)")
                req["reason"] = typed
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
            if not approved and req["kind"] == "qc_override" and req.get("batch"):
                # Declining a batch waiver is a recorded decision (round-3 #8).
                while True:
                    note = input("Decline note (required, 10+ characters): ").strip()
                    if len(note) >= 10:
                        break
                    print("  declining the field needs a real note — it is the record that the writer chose the judge-approved faces only")
            else:
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

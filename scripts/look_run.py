#!/usr/bin/env python3
"""D20.1 — move a writer's resolved look ticket to the look_lock gate, and
record the ratification in the checkpoint. No spend, no appearance decisions.

Usage:
  scripts/look_run.py --project <slug> --entity <id> [--kind character|location]
                      [--casting <image>] [--supersede] [--dry-run]

One entity per run, one gate per run. The command is idempotent: run it
again after every gate. It keeps a durable RUN STATE in
``checkpoint_look_lock.json`` ``metadata.run_state[<entity>]`` and on any
later invocation finishes or resumes ONLY the request that state names
(R3#3 / R4#1 / R5#1 / R5#2):

  missing   the crash window between state and publish → re-verify, republish
  pending   print the approval command, exit
  done      finish (mode-specific, R6#2): look_lock → refresh the look_lock
            checkpoint with the ratified look; casting → finalize the import
            and, in the same checkpoint write, transition to the look request
  declined  consume the note into the decision log, clear state, exit 4
  mismatch  fail closed

``--casting <image>`` (D10, R1#9): a real-person casting-inspiration image
for the writer's eyes only. It goes through its own ``reference_import``
gate first; no model, director, judge, prompt, receipt or ticket text ever
receives it or a description of it. Refused once the entity's look is
ratified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.run_common import (  # noqa: E402
    RunError, gate_command, hold_lease, read_request, request_id_for, require_entity_id, resolve_project_root, write_decision,
)

STAGE = "look_lock"
EXIT_DECLINED = 4


class LookRunError(RunError):
    pass


class Declined(LookRunError):
    """The writer declined the request; the note is logged."""


def _log(out, msg: str) -> None:
    print(msg, file=out or sys.stdout)


# ---- checkpoint + run state ----

def _checkpoint(root: Path) -> dict[str, Any]:
    path = root / f"checkpoint_{STAGE}.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookRunError(f"{path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _run_state(root: Path, entity_id: str) -> Optional[dict[str, Any]]:
    meta = _checkpoint(root).get("metadata") or {}
    state = (meta.get("run_state") or {}).get(entity_id)
    return dict(state) if isinstance(state, dict) else None


def _packet_on_disk(root: Path) -> dict[str, Any]:
    packet = (_checkpoint(root).get("artifacts") or {}).get("look_packet")
    if isinstance(packet, dict):
        return json.loads(json.dumps(packet))
    return {"version": "1.0", "looks": [], "complete": False}


def _proposal(root: Path) -> dict[str, Any]:
    path = root / "checkpoint_proposal.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookRunError(f"look_lock needs a completed proposal checkpoint: {exc}") from exc
    packet = ((data or {}).get("artifacts") or {}).get("proposal_packet")
    if not isinstance(packet, dict):
        raise LookRunError("checkpoint_proposal.json carries no proposal_packet")
    return packet


def _write(root: Path, packet: dict[str, Any], *, run_state: dict[str, Optional[dict]], status: str = "in_progress") -> str:
    """ONE checkpoint write carrying the packet and the run-state change
    (set or cleared) together; returns the new file digest."""
    from lib.checkpoint import checkpoint_digest, write_checkpoint

    current = _checkpoint(root)
    meta = dict(current.get("metadata") or {})
    states = dict(meta.get("run_state") or {})
    for entity, state in run_state.items():
        if state is None:
            states.pop(entity, None)
        else:
            states[entity] = state
    meta["run_state"] = states
    revs = dict(meta.get("run_revisions") or {})
    for entity, state in run_state.items():
        if state is not None:
            revs[entity] = max(int(revs.get(entity, 0)), int(state.get("revision", 0)))
    meta["run_revisions"] = revs
    path = write_checkpoint(root.parent, root.name, STAGE, status, {"look_packet": packet},
                            pipeline_type="authored-film", metadata=meta)
    return checkpoint_digest(path)


def _next_revision(root: Path, entity_id: str) -> int:
    meta = _checkpoint(root).get("metadata") or {}
    return int((meta.get("run_revisions") or {}).get(entity_id, 0)) + 1


# ---- ticket location ----

def locate_ticket(wayfinder_root: Path, entity_kind: str, entity_id: str):
    """The ONE resolved ticket whose ``## Look spec`` describes (kind, id).
    Matched by content, never by filename (D20.1 step 2)."""
    from lib.look_ingest import RESOLVED_SUBDIR, LookIngestError, _resolved_tickets, parse_look_ticket

    resolved_dir = wayfinder_root / RESOLVED_SUBDIR
    if not resolved_dir.is_dir():
        raise LookRunError(f"no resolved tickets directory {resolved_dir}")
    matches = []
    for path, _meta, _raw in _resolved_tickets(resolved_dir, resolved_dir / "\0never"):
        try:
            look = parse_look_ticket(path, wayfinder_root=wayfinder_root)
        except LookIngestError:
            continue  # not a (valid) look ticket; the one we want must parse
        if look.key == (entity_kind, entity_id):
            matches.append((path, look))
    if not matches:
        raise LookRunError(
            f"no resolved look ticket describes ({entity_kind}, {entity_id}) under {resolved_dir}; "
            f"the writer answers the ticket in story-wayfinder first"
        )
    if len(matches) > 1:
        raise LookRunError(f"{len(matches)} resolved tickets claim ({entity_kind}, {entity_id}): "
                           + ", ".join(str(p.relative_to(wayfinder_root)) for p, _ in matches) + " — resolve to one")
    path, look = matches[0]
    return path.relative_to(wayfinder_root), look


# ---- the command ----

def run_look(
    project_root: Path | str, entity_id: str, *, entity_kind: str = "character", casting: Optional[Path | str] = None,
    supersede: bool = False, dry_run: bool = False, out=None,
) -> dict[str, Any]:
    from lib.canon_enforcement import _is_look_lock_manifest
    from lib.look_ingest import LookIngestError, active_look_for, wayfinder_root_for
    from lib.pipeline_pin import PipelinePinError, _read_marker, pinned_pipeline
    from lib.project_config import ProjectConfigError, load_verified_project_config

    root = Path(project_root).resolve()
    project_id = root.name
    entity_id = require_entity_id(entity_id)
    if entity_kind not in ("character", "location"):
        raise LookRunError(f"--kind must be character|location, got {entity_kind!r}")
    try:
        config = load_verified_project_config(root)  # any verified version: no paid call here (R3#2)
        pin = pinned_pipeline(root, str(_read_marker(root).get("pipeline_type") or "authored-film"))
        wf_root = wayfinder_root_for(root)
    except (ProjectConfigError, PipelinePinError, LookIngestError) as exc:
        raise LookRunError(str(exc)) from exc
    if not _is_look_lock_manifest(pin):
        raise LookRunError(f"project is pinned to {pin.name}@{pin.version}; look_run needs authored-film 1.2 or later")

    with hold_lease(root, config):
        state = _run_state(root, entity_id)
        if state is not None:
            return _resume(root, project_id, entity_id, state, wf_root, out)
        try:
            current = active_look_for(root, entity_kind, entity_id)
        except LookIngestError as exc:
            raise LookRunError(str(exc)) from exc
        if casting is not None:
            if entity_kind != "character":
                raise LookRunError("casting inspiration is for characters only")
            if current is not None:
                raise LookRunError(f"the look for {entity_id!r} is ratified; casting inspiration is imported before ratification only")
            return _start_casting(root, project_id, entity_id, Path(casting), dry_run, out)
        return _start_look(root, project_id, entity_kind, entity_id, wf_root, current, supersede, dry_run, out)


def _start_look(root, project_id, entity_kind, entity_id, wf_root, current, supersede, dry_run, out) -> dict[str, Any]:
    from lib.look_ingest import look_lock_request

    ticket_rel, look = locate_ticket(wf_root, entity_kind, entity_id)
    if current is not None:
        if current.look_hash == look.look_hash:
            _log(out, f"look for {entity_id!r} is already ratified (look_hash {look.look_hash[:12]}…); nothing to do")
            return {"entity_id": entity_id, "status": "active", "look_hash": look.look_hash}
        if not supersede:
            raise LookRunError(
                f"{entity_id!r} already has an active look ({current.look_hash[:12]}…) and the ticket now hashes to "
                f"{look.look_hash[:12]}…; re-run with --supersede to replace it (downstream headshot and sheets are invalidated)"
            )
    rev = _next_revision(root, entity_id)
    request_id = request_id_for("look", entity_id, rev)
    if dry_run:
        _log(out, f"[dry-run] would request {request_id}: ratify look {look.look_hash} for ({entity_kind}, {entity_id}) "
                  f"from ticket {ticket_rel}" + (f", superseding {current.look_hash[:12]}…" if current else ""))
        return {"entity_id": entity_id, "status": "dry_run", "request_id": request_id, "look_hash": look.look_hash}
    state = {"mode": "look_lock", "revision": rev, "request_id": request_id, "look_hash": look.look_hash,
             "entity_kind": entity_kind, "ticket_path": str(ticket_rel), "expected_kind": "look_lock"}
    digest = _write(root, _packet_on_disk(root), run_state={entity_id: state})  # state BEFORE the request (R5#1)
    look_lock_request(root, project_id, look, request_id=request_id, ticket_path=ticket_rel, source_checkpoint_digest=digest)
    return _pending(root, entity_id, request_id, out, f"look_lock request written for {entity_id!r} (revision {rev}, look_hash {look.look_hash[:12]}…)")


def _start_casting(root, project_id, entity_id, image: Path, dry_run, out) -> dict[str, Any]:
    from lib.reference_import import ORIGIN_CASTING, ReferenceImportError, normalize_reference_import, publish_import_request, stage_reference_upload

    if not image.is_file():
        raise LookRunError(f"--casting {image} is not a file")
    rev = _next_revision(root, entity_id)
    request_id = request_id_for("casting", entity_id, rev)
    if dry_run:
        _log(out, f"[dry-run] would stage {image.name} as casting inspiration for {entity_id!r} and request {request_id}")
        return {"entity_id": entity_id, "status": "dry_run", "request_id": request_id}
    try:
        staged = stage_reference_upload(root, image)  # a COPY; the writer's original is never consumed (R1#10)
        normalized = normalize_reference_import(root, staged, origin_class=ORIGIN_CASTING, entity_id=entity_id)
    except ReferenceImportError as exc:
        raise LookRunError(str(exc)) from exc
    state = {"mode": "casting", "revision": rev, "request_id": request_id,
             "normalized_pixel_hash": normalized.normalized_pixel_hash,
             "normalized": {"width": normalized.width, "height": normalized.height, "source_format": normalized.source_format},
             "expected_kind": "reference_import"}
    digest = _write(root, _packet_on_disk(root), run_state={entity_id: state})
    try:
        publish_import_request(root, project_id, normalized, request_id=request_id, source_checkpoint_digest=digest)
    except ReferenceImportError as exc:
        raise LookRunError(str(exc)) from exc
    return _pending(root, entity_id, request_id, out,
                    f"casting inspiration staged for {entity_id!r} (for your eyes only; never sent anywhere)")


def _pending(root, entity_id, request_id, out, headline: str) -> dict[str, Any]:
    cmd = gate_command(root, request_id)
    _log(out, f"{headline}\nApprove in Terminal (y to sign), or decline with a note:\n  {cmd}")
    return {"entity_id": entity_id, "status": "pending", "request_id": request_id, "command": cmd}


# ---- resume (the five states) ----

def _resume(root, project_id, entity_id, state, wf_root, out) -> dict[str, Any]:
    request_id = str(state.get("request_id") or "")
    mode = state.get("mode")
    if mode not in ("look_lock", "casting") or not request_id:
        raise LookRunError(f"run state for {entity_id!r} is malformed: {state}")
    where, req = read_request(root, request_id)
    if where == "pending":
        return _pending(root, entity_id, request_id, out, f"request {request_id} is still waiting for the writer")
    if where == "declined":
        note = str((req or {}).get("declined_note") or "").strip() or "(no note)"
        write_decision(root, stage=STAGE, category="revision", subject=f"{mode} request {request_id} declined for {entity_id}",
                       reason=note, selected="declined", user_approved=True)
        _write(root, _packet_on_disk(root), run_state={entity_id: None})
        _log(out, f"declined: {note}\nEdit the ticket in story-wayfinder and rerun.")
        raise Declined(note)
    if where == "missing":
        return _republish(root, project_id, entity_id, state, wf_root, out)
    # done: verify the receipt is the one this state expects, then finish
    if req is None or req.get("kind") != state.get("expected_kind") or req.get("entity_id") not in (entity_id, f"reference-{str(state.get('normalized_pixel_hash', ''))[:12]}"):
        raise LookRunError(f"run state names request {request_id} ({state.get('expected_kind')}); the done request has another shape — refusing to guess")
    bound = req.get("source_checkpoint_digest")
    if not isinstance(bound, str) or len(bound) != 64:
        raise LookRunError(f"done request {request_id} carries no source_checkpoint_digest; it was not published by look_run — refusing")
    if mode == "look_lock":
        return _finish_look(root, project_id, entity_id, state, wf_root, out, bound)
    return _finish_casting(root, project_id, entity_id, state, wf_root, out, bound)


def _receipt_bound_to(rows: list, receipt_id: str, bound: str) -> dict:
    """Inspection #1: the receipt that finishes a run must be the one the gate
    signed for THIS request — it carries the checkpoint digest the request
    was bound to (signed into the receipt by the gate)."""
    row = next((r for r in rows if r.get("receipt_id") == receipt_id), None)
    if row is None:
        raise LookRunError(f"receipt {receipt_id} is not in the verified chain")
    if row.get("source_checkpoint_digest") != bound:
        raise LookRunError(
            f"receipt {receipt_id} was signed for checkpoint digest {str(row.get('source_checkpoint_digest'))[:12]}…, not this "
            f"request's {bound[:12]}…; the done request is not the one that produced it — refusing"
        )
    return row


def _republish(root, project_id, entity_id, state, wf_root, out) -> dict[str, Any]:
    from lib.look_ingest import look_lock_request
    from lib.reference_import import (
        ORIGIN_CASTING, NormalizedImport, ReferenceImportError, STAGING_DIR, import_record, publish_import_request,
    )

    request_id = state["request_id"]
    if state["mode"] == "look_lock":
        ticket_rel, look = locate_ticket(wf_root, str(state.get("entity_kind") or "character"), entity_id)
        if look.look_hash != state.get("look_hash"):
            raise LookRunError(f"run state expects look_hash {str(state.get('look_hash'))[:12]}… but the ticket now hashes to "
                               f"{look.look_hash[:12]}…; the ticket changed mid-run — clear the state by declining, then rerun")
        digest = _write(root, _packet_on_disk(root), run_state={entity_id: state})
        look_lock_request(root, project_id, look, request_id=request_id, ticket_path=ticket_rel, source_checkpoint_digest=digest)
        return _pending(root, entity_id, request_id, out, f"republished {request_id} (the request file was missing)")
    pixel_hash = str(state.get("normalized_pixel_hash") or "")
    staged = root / STAGING_DIR / f"reference-{pixel_hash}.png"
    from lib.pathsafe import sha256_file
    if not staged.is_file() or sha256_file(staged) != pixel_hash:
        raise LookRunError(f"staged casting image for {request_id} is missing or altered; rerun --casting with the image")
    dims = state.get("normalized") or {}
    try:
        normalized = NormalizedImport(ORIGIN_CASTING, pixel_hash, staged, import_record(ORIGIN_CASTING, pixel_hash, entity_id=entity_id),
                                      int(dims.get("width") or 0), int(dims.get("height") or 0), str(dims.get("source_format") or "png"))
        digest = _write(root, _packet_on_disk(root), run_state={entity_id: state})
        publish_import_request(root, project_id, normalized, request_id=request_id, source_checkpoint_digest=digest)
    except ReferenceImportError as exc:
        raise LookRunError(str(exc)) from exc
    return _pending(root, entity_id, request_id, out, f"republished {request_id} (the request file was missing)")


def _finish_look(root, project_id, entity_id, state, wf_root, out, bound: str) -> dict[str, Any]:
    from lib.look_ingest import LookIngestError, active_look_for, look_lock_receipts

    kind = str(state.get("entity_kind") or "character")
    try:
        current = active_look_for(root, kind, entity_id)
    except LookIngestError as exc:
        raise LookRunError(str(exc)) from exc
    if current is None or current.look_hash != state.get("look_hash"):
        raise LookRunError(f"request {state['request_id']} is done but the active look for {entity_id!r} is not the one it named "
                           f"({current.look_hash[:12] if current else None}… vs {str(state.get('look_hash'))[:12]}…)")
    row = _receipt_bound_to(look_lock_receipts(root), current.receipt_id, bound)
    # Inspection #9: the packet entry comes from the SIGNED receipt (payload +
    # ticket ref), never from re-reading the ticket, so a ticket edited after
    # ratification cannot trap this run; the next run sees the change and
    # asks for --supersede.
    entry = {"entity_kind": kind, "entity_id": entity_id, "look_spec": current.payload, "look_hash": current.look_hash,
             "receipt_id": current.receipt_id, "source_ticket_ref": row.get("source_ticket_ref") or {"id": "unknown"}}
    packet = _packet_on_disk(root)
    packet["looks"] = [e for e in packet.get("looks") or [] if (e.get("entity_kind"), e.get("entity_id")) != (kind, entity_id)] + [entry]
    proposal = _proposal(root)
    cast = proposal.get("cast") or {}
    wanted = {("character", c) for c in cast.get("character_ids") or []} | {("location", l) for l in cast.get("location_ids") or []}
    packet["complete"] = wanted <= {(e["entity_kind"], e["entity_id"]) for e in packet["looks"]}
    _write(root, packet, run_state={entity_id: None})  # outcome + state cleared in ONE write
    _log(out, f"look for {entity_id!r} ratified (receipt {current.receipt_id}, look_hash {current.look_hash[:12]}…); "
              f"look_packet now carries {len(packet['looks'])} look(s), complete={packet['complete']}\n"
              f"Next: cd {REPO} && .venv/bin/python scripts/headshot_run.py --project {project_id} --entity {entity_id}")
    try:
        _, look = locate_ticket(wf_root, kind, entity_id)
        if look.look_hash != current.look_hash:
            _log(out, "note: the ticket changed after ratification; run look_run --supersede when the writer wants the new answer")
    except LookRunError:
        pass
    return {"entity_id": entity_id, "status": "ratified", "look_hash": current.look_hash, "receipt_id": current.receipt_id}


def _finish_casting(root, project_id, entity_id, state, wf_root, out, bound: str) -> dict[str, Any]:
    from lib.look_ingest import active_look_for, look_lock_request
    from lib.reference_import import ORIGIN_CASTING, ReferenceImportError, finalize_reference_import, record_entity_id, reference_import_receipts

    pixel_hash = str(state.get("normalized_pixel_hash") or "")
    receipt = None
    for r in reference_import_receipts(root):
        if r.get("normalized_pixel_hash") == pixel_hash and r.get("origin_class") == ORIGIN_CASTING \
                and record_entity_id(r.get("record")) == entity_id and r.get("source_checkpoint_digest") == bound:
            receipt = r
    if receipt is None:
        raise LookRunError(f"request {state['request_id']} is done but no verified casting_inspiration receipt signed for it binds {pixel_hash[:12]}… to {entity_id!r}")
    try:
        finalize_reference_import(root, receipt["receipt_id"])  # the one sanctioned read: hash check + move (R2#5)
    except ReferenceImportError as exc:
        raise LookRunError(str(exc)) from exc
    # atomic transition (R6#2): the same checkpoint write replaces the casting state with the look_lock state
    kind = "character"
    ticket_rel, look = locate_ticket(wf_root, kind, entity_id)
    current = active_look_for(root, kind, entity_id)
    if current is not None and current.look_hash == look.look_hash:
        _write(root, _packet_on_disk(root), run_state={entity_id: None})
        _log(out, f"casting inspiration imported for {entity_id!r}; the look is already ratified")
        return {"entity_id": entity_id, "status": "active", "look_hash": look.look_hash}
    rev = int(state.get("revision") or 0) + 1
    request_id = request_id_for("look", entity_id, rev)
    new_state = {"mode": "look_lock", "revision": rev, "request_id": request_id, "look_hash": look.look_hash,
                 "entity_kind": kind, "ticket_path": str(ticket_rel), "expected_kind": "look_lock"}
    digest = _write(root, _packet_on_disk(root), run_state={entity_id: new_state})
    look_lock_request(root, project_id, look, request_id=request_id, ticket_path=ticket_rel, source_checkpoint_digest=digest)
    return _pending(root, entity_id, request_id, out,
                    f"casting inspiration imported for {entity_id!r} (canon/visual/casting-inspiration/, your eyes only); "
                    f"look_lock request written (revision {rev})")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--kind", default="character", choices=("character", "location"))
    ap.add_argument("--casting", help="a casting-inspiration image (real person) for the writer's eyes only")
    ap.add_argument("--supersede", action="store_true", help="replace an already ratified look with the ticket's current answer")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        root = resolve_project_root(a.project)
        run_look(root, a.entity, entity_kind=a.kind, casting=a.casting, supersede=a.supersede, dry_run=a.dry_run)
    except Declined:
        return EXIT_DECLINED
    except RunError as exc:
        print(f"look_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

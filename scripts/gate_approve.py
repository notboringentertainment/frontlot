#!/usr/bin/env python3
"""Human gate handler — the ONLY path that mints gate tokens (PLAN §5).

Directors never approve anything. They write a request file under
``projects/<slug>/.gate-requests/<request_id>.json`` describing what needs a
human decision, write the checkpoint ``awaiting_human``, and end their turn.

The human then runs this script from a real terminal::

    python scripts/gate_approve.py --project <slug>            # list pending
    python scripts/gate_approve.py --project <slug> --request <id>

It refuses to run without an interactive TTY on stdin (checked in ``main``
and again in ``decide``), so an agent driving a non-interactive shell cannot
mint a token for itself. This is process discipline, not an OS privilege
boundary — see the trust-boundary note in ``lib/gates.py``. On approval it mints a
one-use token, consumes it through ``record_human_approval`` (signed receipt +
consumed-token ledger), and moves the request to ``.gate-requests/done/``.
A decline moves the request to ``.gate-requests/declined/`` with the note; no
receipt is written.

Request file contract::

    {
      "request_id": "...", "project_id": "...", "stage": "visual_bible",
      "scope": "character:<id>",
      "kind": "hero|sheet|location|poster|storyboard_batch|config|artifact_review|
               look_lock|reference_import|pipeline_migration|headshot",
      "entity_id": "<id>" | null, "artifact": {...} | null,
      "approval_record": {...},            # exactly what enforcement will re-hash
      "envelope": {...} | null,            # per-kind signed fields (lib.receipts.ENVELOPE_FIELDS)
      "source_checkpoint_digest": "..." | null,
      "summary": "one paragraph for the human",
      "preview_paths": ["canon/visual/objects/<sha>.png", ...]
    }

Kind ``headshot`` is a SELECTION gate (plan Slice A′, R7#3): the request
carries no approval_record. The handler reads the ``pending`` headshot_packet
from ``checkpoint_headshots.json`` (status awaiting_human), verifies each
candidate preview's bytes against its asset hash and its membership in the
packet, enumerates them, accepts ``1..N`` or reject-all (+ note), and
constructs the signed record itself (``lib.headshots.headshot_record``) with
the candidates checkpoint digest — the agent never authors it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.canonical_json import record_sha256  # noqa: E402
from lib.gates import mint_gate_token  # noqa: E402
from lib.paths import PROJECTS_DIR  # noqa: E402
from lib.receipts import APPROVAL_KINDS, record_human_approval  # noqa: E402
from lib.state_io import atomic_move  # noqa: E402

REQUEST_DIRNAME = ".gate-requests"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED = ("request_id", "project_id", "stage", "scope", "kind", "summary")
SELECTION_KINDS = {"headshot"}
HEADSHOT_CHECKPOINT = "checkpoint_headshots.json"


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
    if data["kind"] not in SELECTION_KINDS and not isinstance(data.get("approval_record"), dict):
        raise GateHandlerError(f"{path.name}: request missing fields ['approval_record']")
    if data["kind"] in SELECTION_KINDS and data.get("approval_record") is not None:
        raise GateHandlerError(
            f"{path.name}: a {data['kind']} request must not carry approval_record — "
            f"the gate handler constructs the record from the pending packet"
        )
    if validate_request_id(data["request_id"]) != path.stem:
        raise GateHandlerError(f"{path.name}: request_id {data['request_id']!r} does not equal the file stem")
    return data


def show_request(req: dict, root: Path, out=None) -> None:
    out = out or sys.stdout
    print(f"\n=== Gate request {req['request_id']} ===", file=out)
    print(f"stage: {req['stage']}   scope: {req['scope']}   kind: {req['kind']}", file=out)
    if isinstance(req.get("approval_record"), dict):
        print(f"record sha256: {record_sha256(req['approval_record'])}", file=out)
    if req.get("envelope"):
        print(f"envelope: {json.dumps(req['envelope'], sort_keys=True)}", file=out)
    print(f"\n{req['summary']}\n", file=out)
    for rel in req.get("preview_paths") or []:
        p = root / rel
        print(f"  preview: {p}  {'(missing!)' if not p.exists() else ''}", file=out)


# ---- headshot selection gate ----


def headshot_candidates(req: dict, root: Path) -> tuple[str, dict, list[dict]]:
    """Read the pending headshot_packet from the awaiting_human checkpoint
    and verify each candidate's bytes. Returns
    ``(candidates_checkpoint_digest, packet_entry, candidates)``."""
    from lib.checkpoint import checkpoint_digest
    from lib.pathsafe import PathSafetyError, resolve_input, sha256_file

    entity_id = req.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id:
        raise GateHandlerError("headshot request needs entity_id")
    path = root / HEADSHOT_CHECKPOINT
    if not path.is_file():
        raise GateHandlerError(f"no {HEADSHOT_CHECKPOINT} in {root}")
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("status") != "awaiting_human" or checkpoint.get("project_id") != req["project_id"]:
        raise GateHandlerError("headshot selection needs the awaiting_human headshots checkpoint of this project")
    packet = (checkpoint.get("artifacts") or {}).get("headshot_packet") or {}
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
        if not isinstance(asset_id, str) or len(asset_id) != 64 or asset_id in seen:
            raise GateHandlerError(f"candidate {i + 1} has a missing or duplicate asset_id")
        seen.add(asset_id)
        try:
            file = resolve_input(str(cand.get("path", "")), root)
        except PathSafetyError as exc:
            raise GateHandlerError(f"candidate {i + 1} path is not project-local: {exc}") from exc
        if not file.is_file():
            raise GateHandlerError(f"candidate {i + 1} preview {cand.get('path')!r} is missing")
        actual = sha256_file(file)
        if actual != asset_id:
            raise GateHandlerError(
                f"candidate {i + 1} preview {cand.get('path')!r} hashes to {actual}, not its asset_id {asset_id} "
                f"— the file shown is not the candidate in the packet"
            )
    return checkpoint_digest(path), entry, candidates


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
    from lib.headshots import active_headshots, headshot_record, prompt_recipe_sha256

    digest, entry, candidates = headshot_candidates(req, root)
    if not 1 <= selection <= len(candidates):
        raise GateHandlerError(f"selection {selection} is not in 1..{len(candidates)}")
    chosen = candidates[selection - 1]
    provenance = chosen.get("provenance") or {}
    imported = provenance.get("generator_kind") == "imported"
    look_hash = (entry.get("look_ref") or {}).get("look_hash")
    if not isinstance(look_hash, str) or len(look_hash) != 64:
        raise GateHandlerError("pending entry has no look_ref.look_hash")
    record = headshot_record(
        entity_id=entry["entity_id"],
        look_hash=look_hash,
        asset_id=chosen["asset_id"],
        origin="imported_synthetic" if imported else "generated",
        import_receipt_id=provenance.get("generation_receipt_id") if imported else None,
        prompt_recipe_sha256=prompt_recipe_sha256(entry.get("prompt_recipe")),
        candidates_checkpoint_digest=digest,
    )
    current = active_headshots(root, project_id=req["project_id"]).get(entry["entity_id"])
    envelope = {
        "action": "activate",
        "entity_kind": "character",
        "look_hash": look_hash,
        "supersedes_receipt_id": current.receipt_id if current else None,
    }
    return record, envelope


# ---- decisions ----


def _decline(req: dict, req_path: Path, root: Path, note: str | None) -> None:
    declined = root / REQUEST_DIRNAME / "declined"
    declined.mkdir(parents=True, exist_ok=True)
    req["declined_note"] = note
    _confined(declined / req_path.name, root).write_text(json.dumps(req, indent=2), encoding="utf-8")
    _confined(req_path, root).unlink()


def decide(
    req: dict,
    root: Path,
    *,
    answer: str,
    note: str | None,
    projects_dir: Path | None = None,
    selection: int | None = None,
) -> dict | None:
    """Apply a human decision. ``answer`` is 'y' or 'n'. Returns the receipt on approval.

    For selection kinds (headshot) ``answer='y'`` needs ``selection`` (1-based
    candidate number); ``answer='n'`` is reject-all and requires a note.

    Re-checks the TTY here (not only in ``main``) so importing this module and
    calling ``decide`` from a non-interactive process is refused as well.
    """
    require_tty()
    request_id = validate_request_id(req.get("request_id"))
    req_path = _confined(root / REQUEST_DIRNAME / f"{request_id}.json", root)
    if req_path.stem != request_id or not req_path.is_file():
        raise GateHandlerError(f"no pending request {request_id!r}")
    kind = req.get("kind")
    if kind not in APPROVAL_KINDS:
        raise GateHandlerError(f"unknown kind {kind!r}")

    if kind in SELECTION_KINDS:
        if answer != "y":
            if not note:
                raise GateHandlerError("reject-all needs a note (it is logged for the regeneration round)")
            _decline(req, req_path, root, note)
            return None
        if selection is None:
            raise GateHandlerError("a headshot approval needs a candidate selection")
        record, envelope = build_headshot_record(root, req, selection)
        entity_id = req["entity_id"]
    else:
        if answer != "y":
            _decline(req, req_path, root, note)
            return None
        record = req["approval_record"]
        envelope = req.get("envelope")
        entity_id = req.get("entity_id")

    digest = record_sha256(record)
    token = mint_gate_token(
        req["project_id"], req["stage"], req["scope"], digest,
        user_response={"answer": "approved", "note": note, "selection": selection},
    )
    receipt = record_human_approval(
        root, req["project_id"], req["stage"], req["scope"], record, token, kind,
        entity_id=entity_id, artifact=req.get("artifact"),
        source_checkpoint_digest=req.get("source_checkpoint_digest"),
        envelope=envelope,
    )
    if kind == "pipeline_migration":
        from lib.pipeline_pin import refresh_cache

        refresh_cache(root, record.get("pipeline_name"))
    done = root / REQUEST_DIRNAME / "done"
    done.mkdir(parents=True, exist_ok=True)
    atomic_move(req_path, _confined(done / req_path.name, root))
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="project slug under projects/")
    ap.add_argument("--request", help="request id to decide; omit to list pending")
    ap.add_argument("--projects-dir", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
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
        selection: int | None = None
        if req["kind"] in SELECTION_KINDS:
            _, entry, candidates = headshot_candidates(req, root)
            show_candidates(entry, candidates, root)
            raw = input(f"Choose candidate [1-{len(candidates)}] or 'r' to reject all: ").strip().lower()
            if raw.isdigit():
                selection = int(raw)
                answer = "y"
            else:
                answer = "n"
            note = input("Note (required for reject-all): ").strip() or None
        else:
            answer = input("Approve? [y/N] ").strip().lower()
            answer = "y" if answer == "y" else "n"
            note = input("Note (optional): ").strip() or None
        receipt = decide(req, root, answer=answer, note=note, selection=selection)
        if receipt:
            print(f"approved — receipt {receipt['receipt_id']}")
        else:
            print("declined — no receipt written")
        return 0
    except GateHandlerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

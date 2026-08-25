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
      "scope": "character:<id>", "kind": "hero|sheet|location|poster|storyboard_batch|config|artifact_review",
      "entity_id": "<id>" | null, "artifact": {...} | null,
      "approval_record": {...},            # exactly what enforcement will re-hash
      "source_checkpoint_digest": "..." | null,
      "summary": "one paragraph for the human",
      "preview_paths": ["canon/visual/objects/<sha>.png", ...]
    }
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
from lib.receipts import record_human_approval  # noqa: E402
from lib.state_io import atomic_move  # noqa: E402

REQUEST_DIRNAME = ".gate-requests"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED = ("request_id", "project_id", "stage", "scope", "kind", "approval_record", "summary")


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
    if validate_request_id(data["request_id"]) != path.stem:
        raise GateHandlerError(f"{path.name}: request_id {data['request_id']!r} does not equal the file stem")
    return data


def show_request(req: dict, root: Path, out=sys.stdout) -> None:
    print(f"\n=== Gate request {req['request_id']} ===", file=out)
    print(f"stage: {req['stage']}   scope: {req['scope']}   kind: {req['kind']}", file=out)
    print(f"record sha256: {record_sha256(req['approval_record'])}", file=out)
    print(f"\n{req['summary']}\n", file=out)
    for rel in req.get("preview_paths") or []:
        p = root / rel
        print(f"  preview: {p}  {'(missing!)' if not p.exists() else ''}", file=out)


def decide(req: dict, root: Path, *, answer: str, note: str | None, projects_dir: Path | None = None) -> dict | None:
    """Apply a human decision. ``answer`` is 'y' or 'n'. Returns the receipt on approval.

    Re-checks the TTY here (not only in ``main``) so importing this module and
    calling ``decide`` from a non-interactive process is refused as well.
    """
    require_tty()
    request_id = validate_request_id(req.get("request_id"))
    req_path = _confined(root / REQUEST_DIRNAME / f"{request_id}.json", root)
    if req_path.stem != request_id or not req_path.is_file():
        raise GateHandlerError(f"no pending request {request_id!r}")
    if answer == "y":
        digest = record_sha256(req["approval_record"])
        token = mint_gate_token(
            req["project_id"], req["stage"], req["scope"], digest,
            user_response={"answer": "approved", "note": note},
        )
        receipt = record_human_approval(
            root, req["project_id"], req["stage"], req["scope"], req["approval_record"], token, req["kind"],
            entity_id=req.get("entity_id"), artifact=req.get("artifact"),
            source_checkpoint_digest=req.get("source_checkpoint_digest"),
        )
        done = root / REQUEST_DIRNAME / "done"
        done.mkdir(parents=True, exist_ok=True)
        atomic_move(req_path, _confined(done / req_path.name, root))
        return receipt
    declined = root / REQUEST_DIRNAME / "declined"
    declined.mkdir(parents=True, exist_ok=True)
    req["declined_note"] = note
    _confined(declined / req_path.name, root).write_text(json.dumps(req, indent=2), encoding="utf-8")
    _confined(req_path, root).unlink()
    return None


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
        answer = input("Approve? [y/N] ").strip().lower()
        note = input("Note (optional): ").strip() or None
        receipt = decide(req, root, answer="y" if answer == "y" else "n", note=note)
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

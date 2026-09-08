#!/usr/bin/env python3
"""C4 / D-C — publish one take for Ben's decision, then apply what he signed.

Usage:
  scripts/take_request.py --project <slug> --shot <shot_id> --take <asset id>
  scripts/take_request.py --project <slug> --request <request id> --finish
  scripts/take_request.py --project <slug> --request <request id> --withdraw

Codex generates takes inside the shot's allowance and take limit, writes the
``awaiting_human`` assets checkpoint, and publishes ONE gate request per
candidate take with this command. Ben sees the take on the board, with its
storyboard frame and the references it was conditioned on, and clicks accept
or reject. That click is the whole decision: it mints a signed ``take``
receipt (``usage_status`` ``selected`` or ``rejected``) through
``gate_approve``, and ``--finish`` copies that decision — and only that
decision — into the asset manifest. A rejected take keeps its place in the
manifest as history.

One decision per shot is in flight at a time: while a take request for a shot
is waiting for Ben, or has been signed and not applied, publishing another one
for that shot is refused. The rest of the shot's candidates queue behind it.

``--withdraw`` retires a request that can no longer be decided — the checkpoint
moved under it, or the take is no longer a candidate. It is refused while the
request is still decidable (that take is accepted or rejected at the gate, and
both are recorded), it mints nothing, and it exists so a stuck request cannot
sit in ``.gate-requests/`` blocking a later migration.

This script never decides anything: it publishes the request and applies a
receipt that already exists. The request carries no ``approval_record``,
because the record IS the human's answer and does not exist until he answers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.run_common import (  # noqa: E402
    REQUEST_DIRNAME, RunError, gate_command, read_request, request_id_for, request_paths, request_state,
    resolve_project_root, write_decision,
)

STAGE = "assets"


class TakeRequestError(RunError):
    pass


def _log(out, message: str) -> None:
    if out is not None:
        print(message, file=out)


def _checkpoint_path(root: Path) -> Path:
    return root / f"checkpoint_{STAGE}.json"


def _assets_checkpoint(root: Path) -> dict[str, Any]:
    import json

    path = _checkpoint_path(root)
    if path.is_symlink() or not path.is_file():
        raise TakeRequestError(f"no checkpoint_{STAGE}.json in {root}")
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TakeRequestError(f"checkpoint_{STAGE}.json is unreadable: {exc}") from exc
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("artifacts"), dict):
        raise TakeRequestError(f"checkpoint_{STAGE}.json is not a checkpoint")
    return checkpoint


def _manifest(checkpoint: dict[str, Any]) -> dict[str, Any]:
    manifest = (checkpoint.get("artifacts") or {}).get("asset_manifest")
    if not isinstance(manifest, dict):
        raise TakeRequestError(f"the {STAGE} checkpoint carries no asset_manifest")
    return manifest


def _take_of(manifest: dict[str, Any], take_asset_id: str) -> dict[str, Any]:
    named = [a for a in manifest.get("assets") or [] if isinstance(a, dict) and a.get("id") == take_asset_id]
    if len(named) != 1:
        raise TakeRequestError(
            f"take {take_asset_id!r} names {len(named)} assets in the asset_manifest; refusing to guess"
        )
    take = named[0]
    if take.get("asset_class") != "shot_visual":
        raise TakeRequestError(f"asset {take_asset_id!r} is a {take.get('asset_class')!r}, not a shot_visual take")
    return take


def _frame_of(manifest: dict[str, Any], shot_id: str) -> Optional[dict[str, Any]]:
    return next(
        (a for a in manifest.get("assets") or []
         if isinstance(a, dict) and a.get("asset_class") == "storyboard_frame" and a.get("shot_id") == shot_id),
        None,
    )


def _carried(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Every field of the existing checkpoint that ``write_checkpoint`` would
    otherwise drop back to its default. Applying a take decision changes the
    manifest and nothing else: the stage keeps its spend snapshot, its review,
    its metadata (run state) and its policy flags. ``pipeline`` / ``predecessors``
    / ``timestamp`` are the writer's own and are recomputed."""
    carried: dict[str, Any] = {
        "checkpoint_policy": str(checkpoint.get("checkpoint_policy") or "guided"),
        "human_approval_required": bool(checkpoint.get("human_approval_required")),
        "human_approved": bool(checkpoint.get("human_approved")),
    }
    for field in ("pipeline_type", "style_playbook", "error"):
        value = checkpoint.get(field)
        if isinstance(value, str) and value:
            carried[field] = value
    for field in ("review", "cost_snapshot", "metadata"):
        value = checkpoint.get(field)
        if isinstance(value, dict):
            carried[field] = value
    return carried


def _generation(root: Path, take: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(sha256 of the take's bytes, its verified generation receipt)``."""
    from lib.pathsafe import PathSafetyError, resolve_input, sha256_file
    from lib.receipts import find_generation

    try:
        sha = sha256_file(resolve_input(str(take.get("path", "")), root))
    except (PathSafetyError, OSError) as exc:
        raise TakeRequestError(f"take {take.get('id')!r} is not a readable project-local file: {exc}") from exc
    generation = find_generation(root, sha)
    if generation is None:
        raise TakeRequestError(
            f"take {take.get('id')!r} ({take.get('path')!r}, sha256 {sha}) has no verified generation "
            f"receipt — only receipted footage can be decided"
        )
    return sha, generation


def _request_id(root: Path, take_asset_id: str) -> tuple[str, str]:
    """``(request_id, state)`` for this take: the pending request if one is
    open, else the next unused revision. A decided (done) request is never
    reopened under its own id."""
    revision = 1
    while True:
        request_id = request_id_for("take", take_asset_id, revision)
        state = request_state(root, request_id)
        if state in ("missing", "pending"):
            return request_id, state
        revision += 1


def _take_requests(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """``(state, request)`` for every take request still in play — pending or
    signed (done). Unreadable or foreign files are not this function's business
    and are skipped; the gate handler and the migration guard see them."""
    import json

    out: list[tuple[str, dict[str, Any]]] = []
    base = root / REQUEST_DIRNAME
    for state, directory in (("pending", base), ("done", base / "done")):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("kind") == "take" and data.get("request_id") == path.stem:
                out.append((state, data))
    return out


def _open_decision_for_shot(
    root: Path, shot_id: str, manifest: dict[str, Any], *, republishing: Optional[str] = None
) -> Optional[tuple[str, dict[str, Any]]]:
    """The take decision already in flight for ``shot_id``, if any: a pending
    request waiting for Ben, or a signed one whose take is still a candidate
    because nobody applied it yet. ``republishing`` names the take whose own
    PENDING request is being rewritten in place — that is the same decision,
    not a second one."""
    assets = {a.get("id"): a for a in manifest.get("assets") or [] if isinstance(a, dict)}
    for state, request in _take_requests(root):
        if request.get("scope") != f"shot:{shot_id}":
            continue
        entity_id = request.get("entity_id")
        if state == "pending":
            if entity_id == republishing:
                continue
            return state, request
        take = assets.get(entity_id)
        if take is None or take.get("usage_status") == "candidate":
            return state, request  # signed but not applied
    return None


def publish_take_request(root: Path | str, shot_id: str, take_asset_id: str, *, out=None) -> dict[str, Any]:
    """Write (or republish) the gate request for one candidate take.

    One decision per shot is in flight at a time (constraint 18): while a take
    request for this shot is waiting for Ben, or has been signed and not yet
    applied, publishing another one is refused. Two open requests would mean
    two decisions for one shot, and applying the first moves the checkpoint
    digest the second was signed against, so the second could never be applied
    anyway. The runner queues the remaining candidates."""
    from lib.checkpoint import checkpoint_digest
    from lib.state_io import atomic_write_json

    root = Path(root)
    checkpoint = _assets_checkpoint(root)
    if checkpoint.get("status") != "awaiting_human":
        raise TakeRequestError(
            f"the {STAGE} checkpoint is {checkpoint.get('status')!r}; a take is decided while the stage waits "
            f"for the human (awaiting_human)"
        )
    take = _take_of(_manifest(checkpoint), take_asset_id)
    if take.get("shot_id") != shot_id:
        raise TakeRequestError(f"take {take_asset_id!r} belongs to shot {take.get('shot_id')!r}, not {shot_id!r}")
    if take.get("usage_status") != "candidate":
        raise TakeRequestError(
            f"take {take_asset_id!r} is already {take.get('usage_status')!r} — it has been decided; "
            f"a new decision needs a new take"
        )
    sha, generation = _generation(root, take)
    frame = _frame_of(_manifest(checkpoint), shot_id)
    request_id, state = _request_id(root, take_asset_id)
    open_decision = _open_decision_for_shot(
        root, shot_id, _manifest(checkpoint), republishing=take_asset_id if state == "pending" else None,
    )
    if open_decision is not None:
        open_state, open_request = open_decision
        open_id = str(open_request.get("request_id"))
        waiting = ("is waiting for the writer's decision" if open_state == "pending"
                   else "has been signed but not applied")
        finish = (f"{REPO}/.venv/bin/python scripts/take_request.py --project {root.name} "
                  f"--request {open_id} --finish")
        raise TakeRequestError(
            f"shot {shot_id!r} already has take request {open_id} (take {open_request.get('entity_id')!r}), which "
            f"{waiting} — one take decision per shot at a time. "
            + (f"Decide it on the board, or: {gate_command(root, open_id)}\n" if open_state == "pending" else "")
            + f"Then apply it: {finish}\n"
            f"If it can no longer be decided, withdraw it: {finish.replace('--finish', '--withdraw')}"
        )
    previews = [str(take.get("path"))] + ([str(frame.get("path"))] if isinstance(frame, dict) else [])
    request = {
        "request_id": request_id,
        "project_id": str(checkpoint.get("project_id") or root.name),
        "stage": STAGE,
        "scope": f"shot:{shot_id}",
        "kind": "take",
        "entity_id": take_asset_id,
        "artifact": None,
        "approval_record": None,  # the record IS the answer; it does not exist yet
        "source_checkpoint_digest": checkpoint_digest(_checkpoint_path(root)),
        "summary": (
            f"Take {take.get('take_id')!r} for shot {shot_id} ({take.get('model_endpoint')}, "
            f"{take.get('duration_seconds') or '?'}s). Accept it into the film or reject it."
        ),
        "preview_paths": previews,
    }
    directory = root / REQUEST_DIRNAME
    directory.mkdir(exist_ok=True)
    atomic_write_json(directory / f"{request_id}.json", request)
    _log(out, f"{'republished' if state == 'pending' else 'published'} {request_id} "
              f"(take {sha[:12]}…, generation {generation['receipt_id']})\n"
              f"Ben decides it on the board, or: {gate_command(root, request_id)}")
    return request


def finish_take(root: Path | str, request_id: str, *, out=None) -> dict[str, Any]:
    """Apply the decision Ben signed: set ``usage_status`` from the receipt.

    Nothing here chooses anything. The receipt is looked up by the id the gate
    wrote into the done request, and its record must be the record this take,
    this shot, these bytes and this generation receipt produce — for one of
    the two possible answers. Whichever one verifies is what gets written."""
    from lib.canon_enforcement import TAKE_USAGE_DECISIONS, take_record
    from lib.canonical_json import record_sha256
    from lib.checkpoint import checkpoint_digest, write_checkpoint
    from lib.receipts import ReceiptError, exact_approval

    root = Path(root)
    state, request = read_request(root, request_id)
    if state != "done" or request is None:
        raise TakeRequestError(
            f"request {request_id!r} is {state}; only a signed (done) take request can be applied"
        )
    if request.get("kind") != "take":
        raise TakeRequestError(f"request {request_id!r} is a {request.get('kind')!r} request")
    receipt_id = request.get("approval_receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise TakeRequestError(f"done request {request_id!r} names no approval_receipt_id")
    take_asset_id = str(request.get("entity_id") or "")

    checkpoint = _assets_checkpoint(root)
    manifest = _manifest(checkpoint)
    take = _take_of(manifest, take_asset_id)
    shot_id = str(take.get("shot_id") or "")
    sha, generation = _generation(root, take)
    project_id = str(checkpoint.get("project_id") or root.name)

    def _verify(usage_status: str, *, bound: Any) -> Optional[dict[str, Any]]:
        record = take_record(project_id=project_id, shot_id=shot_id, asset_id=sha,
                             generation_receipt_id=generation["receipt_id"], usage_status=usage_status)
        try:
            return exact_approval(root, receipt_id=receipt_id, kind="take", entity_id=take_asset_id,
                                  record_sha256=record_sha256(record), source_checkpoint_digest=bound)
        except ReceiptError:
            return None

    current = take.get("usage_status")
    if current in TAKE_USAGE_DECISIONS:
        # Already applied. The manifest may only say what the receipt says.
        if _verify(current, bound=request.get("source_checkpoint_digest")) is None:
            raise TakeRequestError(
                f"take {take_asset_id!r} is recorded {current!r} but receipt {receipt_id} does not say so"
            )
        _log(out, f"take {take_asset_id} is already {current} (receipt {receipt_id}); nothing to do")
        return {"asset_id": take_asset_id, "shot_id": shot_id, "usage_status": current,
                "receipt_id": receipt_id, "applied": False}
    if current != "candidate":
        raise TakeRequestError(f"take {take_asset_id!r} is {current!r}, not a candidate")

    bound = request.get("source_checkpoint_digest")
    if checkpoint.get("status") != "awaiting_human" or checkpoint_digest(_checkpoint_path(root)) != bound:
        raise TakeRequestError(
            f"request {request_id!r} was signed against checkpoint digest {str(bound)[:12]}… but the {STAGE} "
            f"checkpoint is now {checkpoint.get('status')!r} with a different digest; it changed after signing. "
            f"Withdraw it (--withdraw) and re-publish the take request against the current checkpoint."
        )
    decided = next((s for s in TAKE_USAGE_DECISIONS if _verify(s, bound=bound) is not None), None)
    if decided is None:
        raise TakeRequestError(
            f"receipt {receipt_id} does not verify as a decision on take {take_asset_id!r} of shot {shot_id!r} "
            f"(sha256 {sha}, generation {generation['receipt_id']})"
        )

    take["usage_status"] = decided
    artifacts = dict(checkpoint.get("artifacts") or {})
    artifacts["asset_manifest"] = manifest
    write_checkpoint(root.parent, root.name, STAGE, "awaiting_human", artifacts, **_carried(checkpoint))
    _log(out, f"take {take_asset_id} is {decided} (receipt {receipt_id})")
    return {"asset_id": take_asset_id, "shot_id": shot_id, "usage_status": decided,
            "receipt_id": receipt_id, "applied": True}


def _still_decidable(root: Path, state: str, request: dict[str, Any]) -> Optional[str]:
    """Why this request can still be acted on, or None when it is stuck.

    A pending request is decidable while the gate would accept it: the stage
    still waits for the human, the checkpoint still hashes to the digest the
    request is bound to, and the take is still a candidate. A signed one is
    applicable under the same conditions (``finish_take``); one whose decision
    is already in the manifest is finished, not stuck."""
    from lib.canon_enforcement import TAKE_USAGE_DECISIONS
    from lib.checkpoint import checkpoint_digest

    try:
        checkpoint = _assets_checkpoint(root)
        take = _take_of(_manifest(checkpoint), str(request.get("entity_id") or ""))
    except TakeRequestError:
        return None
    if state == "done" and take.get("usage_status") in TAKE_USAGE_DECISIONS:
        return f"its decision is already recorded in the manifest ({take.get('usage_status')})"
    if take.get("usage_status") != "candidate":
        return None
    if checkpoint.get("status") != "awaiting_human":
        return None
    if checkpoint_digest(_checkpoint_path(root)) != request.get("source_checkpoint_digest"):
        return None
    return ("the writer can still decide it on the board" if state == "pending"
            else "it is signed and can still be applied with --finish")


def withdraw_take_request(root: Path | str, request_id: str, *, out=None) -> dict[str, Any]:
    """Retire a take request that can no longer be decided or applied.

    This is the runner's counterpart to ``sheet_run --abandon``, and it is the
    ONLY way a take request leaves play without a decision. It is legal only
    when the request is stuck — the checkpoint moved under it, or the take is
    no longer a candidate — never when Ben could still decide it: a decidable
    take is accepted or rejected at the gate, and both of those are signed.
    Nothing is minted here; the withdrawal is recorded in the decision log and
    the request is archived so it stops blocking a later migration."""
    from lib.state_io import atomic_move

    root = Path(root)
    state, request = read_request(root, request_id)
    if state in ("declined", "abandoned"):
        _log(out, f"take request {request_id} is already {state}; nothing to withdraw")
        return {"request_id": request_id, "status": state, "withdrawn": False}
    if state == "missing" or request is None:
        raise TakeRequestError(f"no take request {request_id!r} to withdraw")
    if request.get("kind") != "take":
        raise TakeRequestError(f"request {request_id!r} is a {request.get('kind')!r} request")
    decidable = _still_decidable(root, state, request)
    if decidable is not None:
        raise TakeRequestError(
            f"take request {request_id!r} is not stuck — {decidable}. A take that can still be decided is "
            f"accepted or rejected at the gate (both are recorded); withdrawing it would lose the decision."
        )
    subject = f"withdraw take request {request_id} for shot {request.get('scope')}"
    if not _decision_logged(root, subject):
        write_decision(
            root, stage=STAGE, category="revision", subject=subject, selected="withdrawn", user_approved=False,
            reason=(f"take {request.get('entity_id')!r} can no longer be decided against checkpoint digest "
                    f"{str(request.get('source_checkpoint_digest'))[:12]}…; no take receipt was minted"),
        )
    paths = request_paths(root, request_id)
    archive = paths["abandoned"]
    if paths[state].is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        atomic_move(paths[state], archive)
    elif not archive.is_file():
        raise TakeRequestError(f"request {request_id} vanished while being withdrawn; refusing to guess")
    _log(out, f"withdrew {request_id}: nothing was decided and no receipt exists. "
              f"Re-publish the take when the stage is ready for a decision.")
    return {"request_id": request_id, "status": "abandoned", "withdrawn": True}


def _decision_logged(root: Path, subject: str) -> bool:
    import json

    path = root / "decision_log.json"
    if not path.is_file():
        return False
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(isinstance(d, dict) and d.get("subject") == subject for d in log.get("decisions") or [])


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--shot", help="shot id the take realizes (publishing)")
    ap.add_argument("--take", help="asset_manifest id of the candidate take (publishing)")
    ap.add_argument("--request", help="request id to apply or withdraw")
    ap.add_argument("--finish", action="store_true", help="apply the take decision Ben signed")
    ap.add_argument("--withdraw", action="store_true",
                    help="retire a take request that can no longer be decided (never a decidable one)")
    args = ap.parse_args(argv)
    try:
        root = resolve_project_root(args.project)
        if args.finish and args.withdraw:
            raise TakeRequestError("--finish and --withdraw are different endings; choose one")
        if args.finish or args.withdraw:
            if not args.request:
                raise TakeRequestError("--finish and --withdraw need --request")
            if args.finish:
                finish_take(root, args.request, out=sys.stdout)
            else:
                withdraw_take_request(root, args.request, out=sys.stdout)
        else:
            if not args.shot or not args.take:
                raise TakeRequestError("publishing a take request needs --shot and --take")
            publish_take_request(root, args.shot, args.take, out=sys.stdout)
    except RunError as exc:
        print(f"take_request: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

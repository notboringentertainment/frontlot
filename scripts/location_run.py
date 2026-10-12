#!/usr/bin/env python3
"""G10 (light) — give a location with a locked look its approved reference pictures.

    scripts/location_run.py --project <slug> --entity <location-id> --add-image <file> --origin-tool <name> [--label <word>]
    scripts/location_run.py --project <slug> --entity <location-id> --seal
    scripts/location_run.py --project <slug> --entity <location-id> --finish
    scripts/location_run.py --project <slug> --entity <location-id> --list

``--add-image`` files a ``reference_import`` request bound to the location
(the writer signs it: machine-made, no real person). The first picture, or
the one labelled ``establishing``, is the establishing picture; every other
picture is an extra view carrying its label (``aftermath``, ``great-hall``…),
at most three.

``--seal`` finalizes every signed picture into ``canon/visual/objects/``
(the imported generation receipt is the lineage root), writes the location's
draft entry into the pending visual bible and files ONE ``location`` request
bound to that checkpoint. The writer sees all of the location's pictures
together and signs once.

``--finish`` applies the signed ``location`` receipt (entry approved). A
declined seal restores the previous entry and the pictures stay signed, so
the writer can change the set and seal again.

Plan: docs/plans/2026-10-11-location-pictures.md. No AI quality check, no
generation, no config or pipeline change.
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
    REQUEST_DIRNAME, RunError, gate_command, read_request, request_id_for, require_entity_id, resolve_project_root,
    write_decision,
)

STAGE = "visual_bible"
CHECKPOINT = f"checkpoint_{STAGE}.json"
IMAGES_DIRNAME = ".location-images"   # the working list of pictures added per location (not canon)
ESTABLISHING = "establishing"
MAX_EXTRA_VIEWS = 3
MIN_LONG_EDGE = 1024
EXIT_DECLINED = 4
_LABEL_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class LocationRunError(RunError):
    pass


class Declined(LocationRunError):
    pass


def _log(out, msg: str) -> None:
    print(msg, file=out, flush=True)


# ---- the shared visual-bible checkpoint (sheet_run writes it too) ----

def _checkpoint(root: Path) -> dict[str, Any]:
    path = root / CHECKPOINT
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LocationRunError(f"{path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _meta(root: Path) -> dict[str, Any]:
    return dict(_checkpoint(root).get("metadata") or {})


def _run_states(root: Path) -> dict[str, Any]:
    return dict(_meta(root).get("run_state") or {})


def _bible(root: Path) -> Optional[dict[str, Any]]:
    vb = (_checkpoint(root).get("artifacts") or {}).get("visual_bible")
    return json.loads(json.dumps(vb)) if isinstance(vb, dict) else None


def _location_of(vb: Optional[dict], entity_id: str) -> Optional[dict[str, Any]]:
    for e in (vb or {}).get("locations") or []:
        if isinstance(e, dict) and e.get("id") == entity_id:
            return e
    return None


def _with_location(vb: dict, entity_id: str, entry: Optional[dict]) -> dict:
    locs = [e for e in vb.get("locations") or [] if not (isinstance(e, dict) and e.get("id") == entity_id)]
    if entry is not None:
        locs.append(entry)
    vb["locations"] = locs
    return vb


def _write(root: Path, vb: dict[str, Any], *, status: str, entity_id: str, state: Optional[dict],
           prev_entry: Any = ..., clear_prev: bool = False) -> str:
    """One checkpoint write carrying the bible and this location's run state;
    every other key (sheet run state, sheet history) is carried unchanged.
    Returns the digest of the file written (what a request binds)."""
    from lib.checkpoint import checkpoint_digest, write_checkpoint

    meta = _meta(root)
    states = dict(meta.get("run_state") or {})
    if state is None:
        states.pop(entity_id, None)
    else:
        states[entity_id] = state
    meta["run_state"] = states
    revs = dict(meta.get("location_revisions") or {})
    if state is not None and state.get("revision") is not None:
        revs[entity_id] = max(int(revs.get(entity_id, 0)), int(state["revision"]))
    meta["location_revisions"] = revs
    prevs = dict(meta.get("location_prev_entry") or {})
    if prev_entry is not ...:
        prevs[entity_id] = prev_entry
    if clear_prev:
        prevs.pop(entity_id, None)
    meta["location_prev_entry"] = prevs
    path = write_checkpoint(root.parent, root.name, STAGE, status, {"visual_bible": vb},
                            pipeline_type="authored-film", human_approval_required=True, metadata=meta)
    return checkpoint_digest(path)


# ---- the working list of pictures (outside the checkpoint, so adding one never disturbs a pending signature) ----

def _images_path(root: Path, entity_id: str) -> Path:
    return root / IMAGES_DIRNAME / f"{entity_id}.json"


def _images(root: Path, entity_id: str) -> list[dict[str, Any]]:
    path = _images_path(root, entity_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LocationRunError(f"{path} is unreadable: {exc}") from exc
    return [i for i in data.get("images") or [] if isinstance(i, dict)] if isinstance(data, dict) else []


def _save_images(root: Path, entity_id: str, images: list[dict[str, Any]]) -> None:
    from lib.state_io import atomic_write_json

    path = _images_path(root, entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"entity_id": entity_id, "images": images})


def _image_states(root: Path, images: list[dict[str, Any]]) -> list[tuple[dict, str]]:
    return [(img, read_request(root, img["request_id"])[0]) for img in images]


def _require_label(label: Optional[str]) -> Optional[str]:
    if label is None:
        return None
    if not label or len(label) > 40 or set(label) - _LABEL_CHARS:
        raise LocationRunError(f"--label {label!r} must be a short lowercase word (letters, numbers, hyphens)")
    return label


# ---- --add-image ----

def add_image(root: Path, entity_id: str, image: Path, origin_tool: str, label: Optional[str], out) -> dict[str, Any]:
    from lib.reference_import import (
        ORIGIN_IMPORTED_SYNTHETIC, ReferenceImportError, normalize_reference_import, publish_import_request,
        stage_reference_upload,
    )

    label = _require_label(label)
    if not isinstance(origin_tool, str) or not origin_tool.strip():
        raise LocationRunError("--add-image needs --origin-tool <name> (the tool that made the picture, e.g. midjourney)")
    if not image.is_file():
        raise LocationRunError(f"--add-image {image} is not a file")
    live = [(img, st) for img, st in _image_states(root, _images(root, entity_id)) if st in ("pending", "done")]
    labels = [img.get("label") for img, _ in live]
    if label == ESTABLISHING and ESTABLISHING in labels:
        raise LocationRunError(f"{entity_id!r} already has an establishing picture; give this one another label")
    if label is None:
        label = ESTABLISHING if ESTABLISHING not in labels else f"view-{len(live)}"
    if label in labels:
        raise LocationRunError(f"{entity_id!r} already has a picture labelled {label!r}; pick another label")
    if label != ESTABLISHING and sum(1 for x in labels if x != ESTABLISHING) >= MAX_EXTRA_VIEWS:
        raise LocationRunError(f"{entity_id!r} already has {MAX_EXTRA_VIEWS} extra views, the most a location holds")
    try:
        staged = stage_reference_upload(root, image)  # a copy; the writer's original is never consumed
        normalized = normalize_reference_import(root, staged, origin_class=ORIGIN_IMPORTED_SYNTHETIC,
                                                origin_tool=origin_tool.strip(), entity_id=entity_id)
    except ReferenceImportError as exc:
        raise LocationRunError(str(exc)) from exc
    w, h = normalized.width, normalized.height
    if w < h or max(w, h) < MIN_LONG_EDGE:
        normalized.staged_path.unlink(missing_ok=True)
        raise LocationRunError(f"the picture is {w}x{h}; a location picture must be landscape or square with a long edge "
                               f"of at least {MIN_LONG_EDGE} pixels")
    pixel_hash = normalized.normalized_pixel_hash
    if any(img.get("pixel_hash") == pixel_hash for img, _ in live):
        raise LocationRunError(f"this picture is already added to {entity_id!r}")
    request_id = request_id_for("location-image", entity_id, len(_images(root, entity_id)) + 1)
    try:
        publish_import_request(root, root.name, normalized, request_id=request_id)
    except ReferenceImportError as exc:
        raise LocationRunError(str(exc)) from exc
    images = _images(root, entity_id)
    images.append({"request_id": request_id, "pixel_hash": pixel_hash, "label": label, "origin_tool": origin_tool.strip(),
                   "width": w, "height": h})
    _save_images(root, entity_id, images)
    cmd = gate_command(root, request_id)
    _log(out, f"Picture added to {entity_id!r} as {label!r} ({w}x{h}). Waiting for the writer to sign it under Needs you.\n"
              f"Approve in Terminal (y to sign):\n  {cmd}")
    return {"entity_id": entity_id, "status": "pending", "request_id": request_id, "label": label, "command": cmd}


# ---- --seal ----

def seal(root: Path, entity_id: str, out) -> dict[str, Any]:
    from lib.canon_enforcement import location_approval_record
    from lib.look_ingest import active_look_for
    from lib.reference_import import (
        ORIGIN_IMPORTED_SYNTHETIC, ReferenceImportError, finalize_reference_import, imported_image_ref,
        reference_import_receipts, record_entity_id,
    )

    states = _run_states(root)
    if entity_id in states:
        raise LocationRunError(f"{entity_id!r} already has a request waiting; finish it first "
                               f"(location_run.py --entity {entity_id} --finish)")
    if states:
        other = sorted(states)[0]
        raise LocationRunError(f"{other!r} has a visual bible request in flight; it must be finished or declined first, "
                               f"because both share one record")
    look = active_look_for(root, "location", entity_id)
    if look is None:
        raise LocationRunError(f"{entity_id!r} has no locked look; lock the look first")
    vb = _bible(root)
    if vb is None:
        raise LocationRunError("there is no visual bible yet; make a character sheet first so it exists")

    pending, signed = [], []
    for img, st in _image_states(root, _images(root, entity_id)):
        if st == "pending":
            pending.append(img)
        elif st == "done":
            signed.append(img)
    if pending:
        raise LocationRunError(f"{len(pending)} picture(s) for {entity_id!r} still wait for a signature "
                               f"({', '.join(i['label'] for i in pending)}); sign or decline them first")
    est = [i for i in signed if i.get("label") == ESTABLISHING]
    if len(est) != 1:
        raise LocationRunError(f"{entity_id!r} needs exactly one signed establishing picture (found {len(est)}); add one first")
    extras = [i for i in signed if i.get("label") != ESTABLISHING]
    if len(extras) > MAX_EXTRA_VIEWS:
        raise LocationRunError(f"{entity_id!r} has {len(extras)} extra views; a location holds at most {MAX_EXTRA_VIEWS}")

    receipts = reference_import_receipts(root)

    def ref_for(img: dict) -> dict[str, Any]:
        rows = [r for r in receipts if r.get("normalized_pixel_hash") == img["pixel_hash"]
                and r.get("origin_class") == ORIGIN_IMPORTED_SYNTHETIC and record_entity_id(r.get("record")) == entity_id]
        if not rows:
            raise LocationRunError(f"the {img['label']!r} picture has no verified signature bound to {entity_id!r}; refusing")
        try:
            imported = finalize_reference_import(root, rows[-1]["receipt_id"])
            return imported_image_ref(root, imported, role=img["label"])
        except ReferenceImportError as exc:
            raise LocationRunError(f"the {img['label']!r} picture can't be brought in: {exc}") from exc

    prev = _location_of(vb, entity_id)
    meta = _meta(root)
    rev = max(int((meta.get("location_revisions") or {}).get(entity_id, 0)), int((prev or {}).get("sheet_revision") or 0)) + 1
    entry = {
        "id": entity_id,
        "establishing": ref_for(est[0]),
        "angles": [ref_for(i) for i in extras],
        "look_ref": {"entity_kind": "location", "entity_id": entity_id, "look_hash": look.look_hash, "receipt_id": look.receipt_id},
        "sheet_revision": rev,
        "status": "draft",
    }
    request_id = request_id_for("location", entity_id, rev)
    state = {"mode": "location", "request_id": request_id, "expected_kind": "location", "revision": rev}
    digest = _write(root, _with_location(vb, entity_id, entry), status="awaiting_human", entity_id=entity_id, state=state,
                    prev_entry=prev)
    _write_location_request(root, entity_id, entry, vb.get("palette") or {}, request_id, rev, digest, location_approval_record)
    return _pending(root, entity_id, request_id, out,
                    f"{entity_id!r}: {1 + len(extras)} picture(s) sealed as revision {rev}. Waiting for the writer to sign the set.")


def _write_location_request(root, entity_id, entry, palette, request_id, rev, digest, record_fn) -> Path:
    from lib.state_io import atomic_write_json

    d = root / REQUEST_DIRNAME
    d.mkdir(exist_ok=True)
    refs = [entry["establishing"]] + list(entry.get("angles") or [])
    labels = ", ".join(r["role"] for r in refs)
    req = {"request_id": request_id, "project_id": root.name, "stage": STAGE, "scope": f"location:{entity_id}",
           "kind": "location", "entity_id": entity_id, "artifact": None, "approval_record": record_fn(entry, palette),
           "source_checkpoint_digest": digest,
           "summary": f"Approve {entity_id}'s location pictures, revision {rev} ({labels}).",
           "preview_paths": [r["path"] for r in refs]}
    path = d / f"{request_id}.json"
    atomic_write_json(path, req)
    return path


def _pending(root: Path, entity_id: str, request_id: str, out, headline: str) -> dict[str, Any]:
    cmd = gate_command(root, request_id)
    _log(out, f"{headline}\nApprove in Terminal (y to sign):\n  {cmd}")
    return {"entity_id": entity_id, "status": "pending", "request_id": request_id, "command": cmd}


# ---- --finish ----

def finish(root: Path, entity_id: str, out) -> dict[str, Any]:
    state = _run_states(root).get(entity_id)
    if not isinstance(state, dict) or state.get("mode") != "location":
        raise LocationRunError(f"nothing to finish: {entity_id!r} has no sealed set waiting (run --seal first)")
    request_id = str(state.get("request_id") or "")
    where, req = read_request(root, request_id)
    if where == "pending":
        return _pending(root, entity_id, request_id, out, f"request {request_id} is still waiting for the writer")
    if where == "missing":
        return _republish(root, entity_id, state, out)
    if where == "declined":
        return _declined(root, entity_id, state, req or {}, out)
    if where != "done" or (req or {}).get("kind") != "location":
        raise LocationRunError(f"request {request_id} is {where} with kind {(req or {}).get('kind')!r}; refusing to guess")
    return _finish_location(root, entity_id, state, req, out)


def _draft(root: Path, entity_id: str, state: dict) -> tuple[dict, dict, dict]:
    cp = _checkpoint(root)
    vb = json.loads(json.dumps((cp.get("artifacts") or {}).get("visual_bible") or {}))
    entry = _location_of(vb, entity_id)
    if entry is None or entry.get("status") != "draft" or int(entry.get("sheet_revision") or 0) != int(state.get("revision") or -1):
        raise LocationRunError(f"the visual bible no longer holds {entity_id!r} draft revision {state.get('revision')}; refusing")
    return cp, vb, entry


def _republish(root: Path, entity_id: str, state: dict, out) -> dict[str, Any]:
    from lib.canon_enforcement import location_approval_record
    from lib.checkpoint import checkpoint_digest

    cp, vb, entry = _draft(root, entity_id, state)
    if cp.get("status") != "awaiting_human":
        raise LocationRunError(f"the visual bible checkpoint is {cp.get('status')!r}, not awaiting_human; refusing")
    digest = checkpoint_digest(root / CHECKPOINT)
    _write_location_request(root, entity_id, entry, vb.get("palette") or {}, state["request_id"], int(state["revision"]),
                            digest, location_approval_record)
    return _pending(root, entity_id, state["request_id"], out, f"republished {state['request_id']}")


def _declined(root: Path, entity_id: str, state: dict, req: dict, out) -> dict[str, Any]:
    note = str(req.get("declined_note") or "").strip() or "(no note)"
    write_decision(root, stage=STAGE, category="revision", subject=f"location pictures {state['request_id']} declined for {entity_id}",
                   reason=note, selected="declined", user_approved=True)
    vb = _bible(root) or {}
    prev = (_meta(root).get("location_prev_entry") or {}).get(entity_id)
    vb = _with_location(vb, entity_id, dict(prev) if isinstance(prev, dict) else None)
    _write(root, vb, status="in_progress", entity_id=entity_id, state=None, clear_prev=True)
    _log(out, f"declined: {note}\nThe set was withdrawn. The signed pictures are kept; add or change pictures and seal again.")
    raise Declined(note)


def _finish_location(root: Path, entity_id: str, state: dict, req: dict, out) -> dict[str, Any]:
    from lib.canon_enforcement import location_approval_record
    from lib.canonical_json import record_sha256
    from lib.checkpoint import checkpoint_digest
    from lib.receipts import ReceiptError, exact_approval

    request_id = state["request_id"]
    bound = req.get("source_checkpoint_digest")
    path = root / CHECKPOINT
    actual = checkpoint_digest(path) if path.is_file() else None
    cp, vb, entry = _draft(root, entity_id, state)
    if cp.get("status") != "awaiting_human" or not bound or actual != bound:
        raise LocationRunError(f"request {request_id} was signed against a visual bible that has changed since; it can't be "
                               f"applied. Decline-and-reseal is needed")
    record_sha = record_sha256(location_approval_record(entry, vb.get("palette") or {}))
    receipt_id = req.get("approval_receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise LocationRunError(f"done request {request_id} names no approval receipt; refusing")
    try:
        exact_approval(root, receipt_id=receipt_id, kind="location", entity_id=entity_id, record_sha256=record_sha,
                       source_checkpoint_digest=bound)
    except ReceiptError as exc:
        raise LocationRunError(f"request {request_id} is done but its receipt does not verify: {exc}") from exc
    entry["status"] = "approved"
    entry["approval_receipt_id"] = receipt_id
    _write(root, _with_location(vb, entity_id, entry), status="in_progress", entity_id=entity_id, state=None, clear_prev=True)
    try:
        from lib.canon_view import build_view
        build_view(root)
    except Exception:  # noqa: BLE001  (the by-entity view is a convenience; the checkpoint is the record)
        pass
    _log(out, f"approved {entity_id}'s location pictures (revision {state['revision']}).")
    return {"entity_id": entity_id, "status": "approved", "revision": int(state["revision"]), "receipt_id": receipt_id}


# ---- --list ----

def list_images(root: Path, entity_id: str, out) -> dict[str, Any]:
    rows = [{"label": img.get("label"), "state": st, "request_id": img.get("request_id")}
            for img, st in _image_states(root, _images(root, entity_id))]
    entry = _location_of(_bible(root), entity_id)
    _log(out, json.dumps({"entity_id": entity_id, "pictures": rows,
                          "bible_entry": None if entry is None else {"status": entry.get("status"),
                                                                     "revision": entry.get("sheet_revision")}}, indent=2))
    return {"entity_id": entity_id, "pictures": rows}


def run_location(project_root: Path | str, entity_id: str, *, add_image_path: Optional[Path | str] = None,
                 origin_tool: Optional[str] = None, label: Optional[str] = None, do_seal: bool = False,
                 do_finish: bool = False, do_list: bool = False, out=None) -> dict[str, Any]:
    from lib import run_lease

    out = out or sys.stdout
    root = Path(project_root).resolve()
    require_entity_id(entity_id)
    modes = [add_image_path is not None, do_seal, do_finish, do_list]
    if sum(modes) != 1:
        raise LocationRunError("give exactly one of --add-image, --seal, --finish, --list")
    if do_list:
        return list_images(root, entity_id, out)
    from lib.cast_change import effective_cast

    if entity_id not in set(effective_cast(root, project_id=root.name)["location_ids"]):
        raise LocationRunError(f"{entity_id!r} is not a place in the approved trailer cast")
    with run_lease.acquire(root, 60.0):
        if add_image_path is not None:
            from lib.look_ingest import active_look_for

            if active_look_for(root, "location", entity_id) is None:
                raise LocationRunError(f"{entity_id!r} has no locked look; lock the look first")
            return add_image(root, entity_id, Path(add_image_path), str(origin_tool or ""), label, out)
        if do_seal:
            return seal(root, entity_id, out)
        return finish(root, entity_id, out)


def main(argv: Optional[list[str]] = None) -> int:
    from lib.env_loader import load_env
    from lib.run_lease import LeaseHeldError

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--add-image", help="the writer's own picture of the place")
    ap.add_argument("--origin-tool", help="the tool that made --add-image (e.g. midjourney)")
    ap.add_argument("--label", help="establishing, or a short word for an extra view (aftermath, great-hall)")
    ap.add_argument("--seal", action="store_true", help="put the signed pictures up for one signature as a set")
    ap.add_argument("--finish", action="store_true", help="apply the signed set")
    ap.add_argument("--list", action="store_true", help="show the location's pictures and their state")
    a = ap.parse_args(argv)
    load_env()
    try:
        root = resolve_project_root(a.project)
        run_location(root, a.entity, add_image_path=a.add_image, origin_tool=a.origin_tool, label=a.label,
                     do_seal=a.seal, do_finish=a.finish, do_list=a.list)
    except Declined:
        return EXIT_DECLINED
    except (RunError, LeaseHeldError) as exc:
        print(f"location_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""G10 — give a location with a locked look its approved reference pictures, with ONE signature per place.

    scripts/location_run.py --project <slug> --entity <location-id> --add-image <file> --origin-tool <name> [--label <word>]
    scripts/location_run.py --project <slug> --entity <location-id> --remove-image <label>
    scripts/location_run.py --project <slug> --entity <location-id> --seal
    scripts/location_run.py --project <slug> --entity <location-id> --finish
    scripts/location_run.py --project <slug> --entity <location-id> --list

``--add-image`` normalizes the writer's picture and stages it for the place;
nothing is signed yet. The first picture (or the one labelled
``establishing``) is the establishing picture; every other picture is an
extra view with its label (``aftermath``, ``great-hall``…), at most three.

``--seal`` puts the staged set up for ONE signature. The signing screen shows
every picture and the fixed promise that each is machine-made and shows no
real person; the writer's one yes signs that promise for each picture and
approves the set (``location`` receipt, envelope ``attestation_text`` +
``staged_pictures``).

``--finish`` applies the signed set: the pictures move into
``canon/visual/objects/``, each gets its imported generation receipt rooted
in the set signature, and the place's bible entry is written approved. A
declined set keeps the staged pictures so the writer can change the set and
seal again.

Plan: docs/plans/2026-10-11-location-pictures.md (one-signature revision).
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
SETS_DIRNAME = ".location-sets"   # the place's working set of staged pictures (not canon)
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


# ---- the place's working set (outside the checkpoint: staging never disturbs a pending sheet) ----

def _set_path(root: Path, entity_id: str) -> Path:
    return root / SETS_DIRNAME / f"{entity_id}.json"


def _load_set(root: Path, entity_id: str) -> dict[str, Any]:
    path = _set_path(root, entity_id)
    if not path.is_file():
        return {"entity_id": entity_id, "pictures": [], "sealed": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LocationRunError(f"{path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise LocationRunError(f"{path} is not an object")
    data.setdefault("pictures", [])
    data.setdefault("sealed", None)
    return data


def _save_set(root: Path, entity_id: str, data: dict[str, Any]) -> None:
    from lib.state_io import atomic_write_json

    path = _set_path(root, entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def _require_label(label: Optional[str]) -> Optional[str]:
    if label is None:
        return None
    if not label or len(label) > 40 or set(label) - _LABEL_CHARS:
        raise LocationRunError(f"--label {label!r} must be a short lowercase word (letters, numbers, hyphens)")
    return label


def _ordered(pictures: list[dict]) -> list[dict]:
    """Establishing first, then extra views in the order they were added."""
    return [p for p in pictures if p["label"] == ESTABLISHING] + [p for p in pictures if p["label"] != ESTABLISHING]


def set_entry(entity_id: str, pictures: list[dict], look: Any, revision: int,
              refs: Optional[dict[str, dict]] = None) -> dict[str, Any]:
    """The bible entry for a signed set. Without ``refs`` it carries only what
    ``location_approval_record`` reads (asset ids, look, revision) — the gate
    signs that; with ``refs`` (built at finish) it is the full schema entry."""
    pics = _ordered(pictures)
    if not pics or pics[0]["label"] != ESTABLISHING:
        raise LocationRunError(f"{entity_id!r} needs an establishing picture")

    def ref(p: dict) -> dict:
        return (refs or {}).get(p["pixel_hash"]) or {"asset_id": p["pixel_hash"]}

    return {
        "id": entity_id,
        "establishing": ref(pics[0]),
        "angles": [ref(p) for p in pics[1:]],
        "look_ref": {"entity_kind": "location", "entity_id": entity_id, "look_hash": look.look_hash,
                     "receipt_id": look.receipt_id},
        "sheet_revision": revision,
        "status": "draft",
    }


# ---- --add-image / --remove-image ----

def add_image(root: Path, entity_id: str, image: Path, origin_tool: str, label: Optional[str], out) -> dict[str, Any]:
    from lib.reference_import import ORIGIN_IMPORTED_SYNTHETIC, ReferenceImportError, normalize_reference_import, stage_reference_upload

    data = _load_set(root, entity_id)
    if data.get("sealed"):
        raise LocationRunError(f"{entity_id!r}'s set is up for signing; sign or decline it before changing pictures")
    label = _require_label(label)
    if not isinstance(origin_tool, str) or not origin_tool.strip():
        raise LocationRunError("--add-image needs --origin-tool <name> (the tool that made the picture, e.g. midjourney)")
    if not image.is_file():
        raise LocationRunError(f"--add-image {image} is not a file")
    labels = [p["label"] for p in data["pictures"]]
    if label is None:
        label = ESTABLISHING if ESTABLISHING not in labels else f"view-{len(labels)}"
    if label in labels:
        raise LocationRunError(f"{entity_id!r} already has a picture labelled {label!r}; pick another label or remove that one")
    if label != ESTABLISHING and sum(1 for x in labels if x != ESTABLISHING) >= MAX_EXTRA_VIEWS:
        raise LocationRunError(f"{entity_id!r} already has {MAX_EXTRA_VIEWS} extra views, the most a place holds")
    try:
        staged = stage_reference_upload(root, image)  # a copy; the writer's original is never consumed
        normalized = normalize_reference_import(root, staged, origin_class=ORIGIN_IMPORTED_SYNTHETIC,
                                                origin_tool=origin_tool.strip(), entity_id=entity_id)
    except ReferenceImportError as exc:
        raise LocationRunError(str(exc)) from exc
    w, h = normalized.width, normalized.height
    if w < h or max(w, h) < MIN_LONG_EDGE:
        normalized.staged_path.unlink(missing_ok=True)
        raise LocationRunError(f"the picture is {w}x{h}; a place picture must be landscape or square with a long edge "
                               f"of at least {MIN_LONG_EDGE} pixels")
    pixel_hash = normalized.normalized_pixel_hash
    if any(p["pixel_hash"] == pixel_hash for p in data["pictures"]):
        raise LocationRunError(f"this picture is already in {entity_id!r}'s set")
    data["pictures"].append({"pixel_hash": pixel_hash, "label": label, "origin_tool": origin_tool.strip()})
    _save_set(root, entity_id, data)
    _log(out, f"Picture added to {entity_id!r} as {label!r} ({w}x{h}). Nothing to sign yet: when the set is complete, "
              f"put it up for signing (--seal).")
    return {"entity_id": entity_id, "status": "staged", "label": label, "pictures": len(data["pictures"])}


def remove_image(root: Path, entity_id: str, label: str, out) -> dict[str, Any]:
    data = _load_set(root, entity_id)
    if data.get("sealed"):
        raise LocationRunError(f"{entity_id!r}'s set is up for signing; sign or decline it before changing pictures")
    keep = [p for p in data["pictures"] if p["label"] != label]
    if len(keep) == len(data["pictures"]):
        raise LocationRunError(f"{entity_id!r} has no picture labelled {label!r}")
    data["pictures"] = keep
    _save_set(root, entity_id, data)
    _log(out, f"Removed {label!r} from {entity_id!r}'s set.")
    return {"entity_id": entity_id, "status": "staged", "pictures": len(keep)}


# ---- --seal ----

def seal(root: Path, entity_id: str, out) -> dict[str, Any]:
    from lib.canon_enforcement import location_approval_record
    from lib.look_ingest import active_look_for
    from lib.reference_import import SYNTHETIC_ATTESTATION
    from lib.state_io import atomic_write_json

    data = _load_set(root, entity_id)
    if data.get("sealed"):
        rid = data["sealed"]["request_id"]
        where, _ = read_request(root, rid)
        if where == "pending":
            return _pending(root, entity_id, rid, out, f"{entity_id!r}'s set is already up for signing")
        raise LocationRunError(f"{entity_id!r}'s set was {where}; finish it first (--finish)")
    look = active_look_for(root, "location", entity_id)
    if look is None:
        raise LocationRunError(f"{entity_id!r} has no locked look; lock the look first")
    vb = _bible(root)
    if vb is None:
        raise LocationRunError("there is no visual bible yet; make a character sheet first so it exists")
    pics = _ordered(data["pictures"])
    if not pics or pics[0]["label"] != ESTABLISHING:
        raise LocationRunError(f"{entity_id!r} needs an establishing picture; add one first")
    for p in pics:
        if not (root / ".staging" / f"reference-{p['pixel_hash']}.png").is_file():
            raise LocationRunError(f"the {p['label']!r} picture is no longer staged; remove it and add it again")
    prev = _location_of(vb, entity_id)
    revision = max(int(data.get("last_revision") or 0), int((prev or {}).get("sheet_revision") or 0)) + 1
    entry = set_entry(entity_id, pics, look, revision)
    request_id = request_id_for("location-set", entity_id, revision)
    staged = [{"pixel_hash": p["pixel_hash"], "label": p["label"], "origin_tool": p["origin_tool"]} for p in pics]
    req = {
        "request_id": request_id, "project_id": root.name, "stage": STAGE, "scope": f"location:{entity_id}",
        "kind": "location", "entity_id": entity_id, "artifact": None, "revision": revision,
        "approval_record": location_approval_record(entry, vb.get("palette") or {}),
        "envelope": {"attestation_text": SYNTHETIC_ATTESTATION, "staged_pictures": staged},
        "source_checkpoint_digest": None,
        "summary": (f"Approve {entity_id}'s pictures ({', '.join(p['label'] for p in pics)}). Signing also confirms "
                    f"each picture is machine-made and shows no real person."),
        "preview_paths": [f".staging/reference-{p['pixel_hash']}.png" for p in pics],
    }
    d = root / REQUEST_DIRNAME
    d.mkdir(exist_ok=True)
    atomic_write_json(d / f"{request_id}.json", req)
    data["sealed"] = {"request_id": request_id, "revision": revision}
    data["last_revision"] = revision
    _save_set(root, entity_id, data)
    return _pending(root, entity_id, request_id, out, f"{entity_id!r}: {len(pics)} picture(s) up for one signature.")


def _pending(root: Path, entity_id: str, request_id: str, out, headline: str) -> dict[str, Any]:
    cmd = gate_command(root, request_id)
    _log(out, f"{headline}\nApprove in Terminal (y to sign):\n  {cmd}")
    return {"entity_id": entity_id, "status": "pending", "request_id": request_id, "command": cmd}


# ---- --finish ----

def finish(root: Path, entity_id: str, out) -> dict[str, Any]:
    data = _load_set(root, entity_id)
    sealed = data.get("sealed")
    if not sealed:
        raise LocationRunError(f"nothing to finish: {entity_id!r} has no set up for signing (--seal first)")
    rid = sealed["request_id"]
    where, req = read_request(root, rid)
    if where == "pending":
        return _pending(root, entity_id, rid, out, f"{entity_id!r}'s set is still waiting for the writer")
    if where in ("declined", "abandoned", "missing"):
        note = str((req or {}).get("declined_note") or "").strip() or "(no note)"
        if where == "declined":
            write_decision(root, stage=STAGE, category="revision", subject=f"location set {rid} declined for {entity_id}",
                           reason=note, selected="declined", user_approved=True)
        data["sealed"] = None
        _save_set(root, entity_id, data)
        _log(out, f"{entity_id!r}'s set was {where}: {note}\nThe pictures are still staged; change the set and seal again.")
        if where == "declined":
            raise Declined(note)
        return {"entity_id": entity_id, "status": where}
    return _apply(root, entity_id, data, req or {}, out)


def _apply(root: Path, entity_id: str, data: dict, req: dict, out) -> dict[str, Any]:
    from lib.canon_enforcement import location_approval_record
    from lib.canonical_json import record_sha256
    from lib.checkpoint import write_checkpoint
    from lib.look_ingest import active_look_for
    from lib.receipts import ReceiptError, exact_approval, find_generation
    from lib.reference_import import ReferenceImportError, record_imported_generation
    from lib.pathsafe import sha256_file
    from lib.state_io import atomic_move

    sealed = data["sealed"]
    receipt_id = req.get("approval_receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise LocationRunError(f"done request {sealed['request_id']} names no approval receipt; refusing")
    cp = _checkpoint(root)
    others = sorted((cp.get("metadata") or {}).get("run_state") or {})
    if others:
        raise LocationRunError(f"{others[0]!r} has a sheet waiting in the visual bible; finish or decline it first, "
                               f"then finish this place")
    look = active_look_for(root, "location", entity_id)
    vb = _bible(root) or {}
    pics = _ordered(list(req.get("envelope", {}).get("staged_pictures") or []))
    if look is None or not pics:
        raise LocationRunError(f"{entity_id!r}'s look or signed pictures can't be read; refusing")
    plain = set_entry(entity_id, pics, look, int(sealed["revision"]))
    record_sha = record_sha256(location_approval_record(plain, vb.get("palette") or {}))
    try:
        exact_approval(root, receipt_id=receipt_id, kind="location", entity_id=entity_id, record_sha256=record_sha)
    except ReceiptError as exc:
        raise LocationRunError(f"the signed set does not match today's look or palette ({exc}); seal it again") from exc

    refs: dict[str, dict] = {}
    objects = root / "canon" / "visual" / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    for p in pics:
        h = p["pixel_hash"]
        final, staged = objects / f"{h}.png", root / ".staging" / f"reference-{h}.png"
        if not final.exists():
            if not staged.is_file() or sha256_file(staged) != h:
                raise LocationRunError(f"the staged {p['label']!r} picture is missing or changed; refusing")
            atomic_move(staged, final)
        elif sha256_file(final) != h:
            raise LocationRunError(f"{final} does not hash to {h}; refusing")
        staged.unlink(missing_ok=True)
        gen = find_generation(root, h)
        if gen is None:
            try:
                gen = record_imported_generation(root, output_sha256=h, origin_tool=p["origin_tool"],
                                                 attestation_receipt_id=receipt_id)
            except ReferenceImportError as exc:
                raise LocationRunError(f"the {p['label']!r} picture can't be recorded: {exc}") from exc
        att = gen.get("attestation_receipt_id") or receipt_id
        refs[h] = {"asset_id": h, "path": f"canon/visual/objects/{h}.png", "role": p["label"],
                   "provenance": {"generator_kind": "imported", "origin_tool": gen.get("origin_tool") or p["origin_tool"],
                                  "attestation_receipt_id": att, "import_receipt_id": gen["receipt_id"],
                                  "normalized_pixel_hash": h, "generation_receipt_id": gen["receipt_id"]}}
    entry = set_entry(entity_id, pics, look, int(sealed["revision"]), refs=refs)
    entry["status"] = "approved"
    entry["approval_receipt_id"] = receipt_id
    meta = dict(cp.get("metadata") or {})
    write_checkpoint(root.parent, root.name, STAGE, "in_progress", {"visual_bible": _with_location(vb, entity_id, entry)},
                     pipeline_type="authored-film", human_approval_required=True, metadata=meta)
    data["sealed"] = None
    data["pictures"] = []
    _save_set(root, entity_id, data)
    try:
        from lib.canon_view import build_view
        build_view(root)
    except Exception:  # noqa: BLE001  (the by-entity view is a convenience; the checkpoint is the record)
        pass
    _log(out, f"approved {entity_id}'s pictures ({len(pics)}).")
    return {"entity_id": entity_id, "status": "approved", "revision": int(sealed["revision"]), "receipt_id": receipt_id}


# ---- --list ----

def set_state(root: Path, entity_id: str) -> dict[str, Any]:
    """``{pictures, sealed, sealed_state, entry_status}`` for status and the board."""
    data = _load_set(root, entity_id)
    sealed = data.get("sealed")
    where = read_request(root, sealed["request_id"])[0] if sealed else None
    entry = _location_of(_bible(root), entity_id) or {}
    return {"pictures": [p["label"] for p in _ordered(data["pictures"])], "sealed_state": where,
            "entry_status": entry.get("status"), "approved_count": 1 + len(entry.get("angles") or []) if entry else 0}


def run_location(project_root: Path | str, entity_id: str, *, add_image_path: Optional[Path | str] = None,
                 origin_tool: Optional[str] = None, label: Optional[str] = None, remove_label: Optional[str] = None,
                 do_seal: bool = False, do_finish: bool = False, do_list: bool = False, out=None) -> dict[str, Any]:
    from lib import run_lease

    out = out or sys.stdout
    root = Path(project_root).resolve()
    require_entity_id(entity_id)
    modes = [add_image_path is not None, remove_label is not None, do_seal, do_finish, do_list]
    if sum(modes) != 1:
        raise LocationRunError("give exactly one of --add-image, --remove-image, --seal, --finish, --list")
    if do_list:
        state = set_state(root, entity_id)
        _log(out, json.dumps({"entity_id": entity_id, **state}, indent=2))
        return state
    from lib.cast_change import effective_cast

    if entity_id not in set(effective_cast(root, project_id=root.name)["location_ids"]):
        raise LocationRunError(f"{entity_id!r} is not a place in the approved trailer cast")
    with run_lease.acquire(root, 60.0):
        if add_image_path is not None:
            from lib.look_ingest import active_look_for

            if active_look_for(root, "location", entity_id) is None:
                raise LocationRunError(f"{entity_id!r} has no locked look; lock the look first")
            return add_image(root, entity_id, Path(add_image_path), str(origin_tool or ""), label, out)
        if remove_label is not None:
            return remove_image(root, entity_id, remove_label, out)
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
    ap.add_argument("--remove-image", dest="remove_label", help="take the picture with this label out of the set")
    ap.add_argument("--seal", action="store_true", help="put the set up for one signature")
    ap.add_argument("--finish", action="store_true", help="apply the signed set")
    ap.add_argument("--list", action="store_true", help="show the place's set and its state")
    a = ap.parse_args(argv)
    load_env()
    try:
        root = resolve_project_root(a.project)
        run_location(root, a.entity, add_image_path=a.add_image, origin_tool=a.origin_tool, label=a.label,
                     remove_label=a.remove_label, do_seal=a.seal, do_finish=a.finish, do_list=a.list)
    except Declined:
        return EXIT_DECLINED
    except (RunError, LeaseHeldError) as exc:
        print(f"location_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Headshot approval chain and the headshot_ref boundary check (Slice A′ 2–4).

A headshot is approved only through the selection gate in
``scripts/gate_approve.py`` (kind ``headshot``): the handler reads the
pending ``headshot_packet`` from the ``awaiting_human`` checkpoint,
verifies every candidate's bytes, and CONSTRUCTS the record itself
(``headshot_record``) — the agent never authors it. Receipts form a
per-entity monotonic supersession chain (R7#5): ``activate`` with
``supersedes_receipt_id`` naming the current tip replaces it atomically,
``retire`` closes it; ``active_headshots`` accepts only the unique tip.

``verify_headshot_ref`` is what every character-sheet generation call goes
through before upload (R6#4): the ref ``{entity_id, asset_id,
approval_receipt_id}`` must name the active tip's chosen asset; a rejected
candidate keeps its generation receipt as history but can never pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from lib.canonical_json import record_sha256

HEADSHOT_KIND = "headshot"
HEADSHOT_RECORD_FIELDS = (
    "entity_kind", "entity_id", "look_hash", "asset_id", "normalized_pixel_hash", "origin",
    "import_receipt_id", "prompt_recipe_sha256", "candidates_checkpoint_digest",
)


class HeadshotError(RuntimeError):
    """The headshot chain or a headshot_ref fails its contract."""


@dataclass(frozen=True)
class ActiveHeadshot:
    entity_id: str
    look_hash: str
    asset_id: str
    receipt_id: str
    record: dict[str, Any]


def headshot_record(
    *,
    entity_id: str,
    look_hash: str,
    asset_id: str,
    origin: str,
    import_receipt_id: Optional[str],
    prompt_recipe_sha256: Optional[str],
    candidates_checkpoint_digest: str,
) -> dict[str, Any]:
    """The record hashed into a headshot receipt. ``normalized_pixel_hash``
    equals ``asset_id`` because canon objects are the normalized PNG bytes."""
    if origin not in ("generated", "imported_synthetic"):
        raise HeadshotError(f"origin must be generated|imported_synthetic, got {origin!r}")
    if origin == "imported_synthetic" and not import_receipt_id:
        raise HeadshotError("an imported_synthetic hero must cite its import receipt")
    if origin == "generated" and import_receipt_id:
        raise HeadshotError("a generated hero has no import receipt")
    if not isinstance(asset_id, str) or len(asset_id) != 64:
        raise HeadshotError("asset_id must be a sha256")
    if not isinstance(candidates_checkpoint_digest, str) or len(candidates_checkpoint_digest) != 64:
        raise HeadshotError("candidates_checkpoint_digest must be a sha256")
    return {
        "entity_kind": "character",
        "entity_id": entity_id,
        "look_hash": look_hash,
        "asset_id": asset_id,
        "normalized_pixel_hash": asset_id,
        "origin": origin,
        "import_receipt_id": import_receipt_id,
        "prompt_recipe_sha256": prompt_recipe_sha256,
        "candidates_checkpoint_digest": candidates_checkpoint_digest,
    }


def headshot_receipts(project_dir: Path | str, *, project_id: Optional[str] = None) -> list[dict]:
    from lib.receipts import verified_approvals

    return verified_approvals(project_dir, HEADSHOT_KIND, project_id=project_id)


def active_headshots(project_dir: Path | str, *, project_id: Optional[str] = None) -> dict[str, ActiveHeadshot]:
    """Replay the verified headshot receipts into ``entity_id -> ActiveHeadshot``
    (unique tip per entity; any chain violation fails closed)."""
    active: dict[str, ActiveHeadshot] = {}
    for row in headshot_receipts(project_dir, project_id=project_id):
        record = row.get("record") or {}
        entity_id = str(row.get("entity_id"))
        action = row.get("action")
        current = active.get(entity_id)
        supersedes = row.get("supersedes_receipt_id")
        if action == "activate":
            missing = [f for f in HEADSHOT_RECORD_FIELDS if f not in record]
            if missing:
                raise HeadshotError(f"headshot receipt {row.get('receipt_id')} record lacks {missing}")
            if current is None:
                if supersedes is not None:
                    raise HeadshotError(
                        f"headshot receipt {row.get('receipt_id')} supersedes {supersedes} but no "
                        f"headshot is active for {entity_id!r}"
                    )
            elif supersedes != current.receipt_id:
                raise HeadshotError(
                    f"headshot receipt {row.get('receipt_id')} activates a second hero for {entity_id!r} "
                    f"without superseding the active receipt {current.receipt_id} (got {supersedes!r})"
                )
            active[entity_id] = ActiveHeadshot(
                entity_id, str(record.get("look_hash")), str(record.get("asset_id")),
                str(row.get("receipt_id")), dict(record),
            )
        elif action == "retire":
            if current is None or supersedes != current.receipt_id:
                raise HeadshotError(
                    f"headshot receipt {row.get('receipt_id')} retires {supersedes!r} for {entity_id!r} "
                    f"but the active receipt is {current.receipt_id if current else None}"
                )
            del active[entity_id]
        else:
            raise HeadshotError(f"headshot receipt {row.get('receipt_id')} has action {action!r}")
    return active


def verify_headshot_ref(
    project_dir: Path | str, headshot_ref: dict, *, project_id: Optional[str] = None
) -> dict:
    """Boundary check for character-sheet generation: the ref must name the
    ACTIVE headshot tip for its entity — same asset, same receipt — and that
    headshot's look_hash must still be the active look. Returns the
    normalized ref; raises HeadshotError before any upload otherwise."""
    from lib.look_ingest import active_look_for
    from lib.receipts import normalize_headshot_ref

    ref = normalize_headshot_ref(headshot_ref)
    current = active_headshots(project_dir, project_id=project_id).get(ref["entity_id"])
    if current is None:
        raise HeadshotError(f"no approved headshot for character {ref['entity_id']!r}; approve a face first")
    if current.receipt_id != ref["approval_receipt_id"] or current.asset_id != ref["asset_id"]:
        raise HeadshotError(
            f"headshot_ref for {ref['entity_id']!r} names asset {ref['asset_id']} / receipt "
            f"{ref['approval_receipt_id']} but the active headshot is asset {current.asset_id} / receipt "
            f"{current.receipt_id} — rejected or superseded candidates never satisfy a headshot_ref"
        )
    look = active_look_for(project_dir, "character", ref["entity_id"], project_id=project_id)
    if look is None or look.look_hash != current.look_hash:
        raise HeadshotError(
            f"headshot for {ref['entity_id']!r} was approved against look {current.look_hash}, which is "
            f"no longer the active look ({look.look_hash if look else None}); re-approve the headshot"
        )
    return ref


def headshot_request(
    project_dir: Path | str,
    project_id: str,
    entity_id: str,
    *,
    request_id: Optional[str] = None,
    summary: Optional[str] = None,
) -> Path:
    """Write the selection-gate request for one character (mints nothing;
    carries NO approval_record — the handler builds it from the pending
    packet in checkpoint_headshots.json)."""
    project_dir = Path(project_dir)
    request_id = request_id or f"headshot-{entity_id}"[:64]
    request = {
        "request_id": request_id,
        "project_id": project_id,
        "stage": "headshots",
        "scope": f"character:{entity_id}",
        "kind": HEADSHOT_KIND,
        "entity_id": entity_id,
        "artifact": None,
        "approval_record": None,
        "source_checkpoint_digest": None,
        "summary": summary or (
            f"Choose the face for character {entity_id!r} from the pending headshot candidates "
            f"(or reject all with a note to regenerate)."
        ),
        "preview_paths": [],
    }
    req_dir = project_dir / ".gate-requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / f"{request_id}.json"
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return path


def prompt_recipe_sha256(recipe: Optional[dict]) -> Optional[str]:
    return record_sha256(recipe) if isinstance(recipe, dict) else None

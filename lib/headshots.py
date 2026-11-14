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

_HEX64 = "0123456789abcdef"


def _is_hex64(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX64 for c in value)

HEADSHOT_KIND = "headshot"
HEADSHOT_RECORD_FIELDS = (
    "entity_kind", "entity_id", "look_hash", "asset_id", "normalized_pixel_hash", "origin",
    "import_receipt_id", "prompt_recipe_sha256", "candidates_checkpoint_digest",
)
# D20: record 1.1 seals the receipts the gate relied on (generation or import,
# and the hero verdict) so downstream verification reloads them by id.
HEADSHOT_RECORD_VERSION_1_1 = "1.1"
HEADSHOT_RECORD_FIELDS_1_1 = HEADSHOT_RECORD_FIELDS + ("record_version", "generation_receipt_id", "qc_receipt_id")
# 1.2 (authored-film 1.5): additionally seals the selection's override
# authority — either the candidate's cited batch receipt (all three batch
# fields set) or the sealed legacy citation list, or neither for a clean pass.
# The conditional-shape matrix is enforced in headshot_record(); grandfather
# emits NO headshot record at all (its attestation is its record).
HEADSHOT_RECORD_VERSION_1_2 = "1.2"
HEADSHOT_RECORD_FIELDS_1_2 = HEADSHOT_RECORD_FIELDS_1_1 + (
    "qc_override_receipt_id", "qc_override_record_sha256", "field_manifest_sha256", "legacy_citations",
)
HEADSHOT_RECORD_VERSIONS = ("1.0", HEADSHOT_RECORD_VERSION_1_1, HEADSHOT_RECORD_VERSION_1_2)


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
    record_version: str = "1.0",
    generation_receipt_id: Optional[str] = None,
    qc_receipt_id: Optional[str] = None,
    qc_override_receipt_id: Optional[str] = None,
    qc_override_record_sha256: Optional[str] = None,
    field_manifest_sha256: Optional[str] = None,
    legacy_citations: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """The record hashed into a headshot receipt. ``normalized_pixel_hash``
    equals ``asset_id`` because canon objects are the normalized PNG bytes.

    ``record_version`` 1.0 is the pre-D20 record (no extra fields, so legacy
    receipts still verify). 1.1 (authored-film 1.4) additionally seals
    ``generation_receipt_id`` (for an imported hero this IS the import receipt)
    and ``qc_receipt_id`` (the hero verdict the gate relied on)."""
    if record_version not in HEADSHOT_RECORD_VERSIONS:
        raise HeadshotError(f"record_version must be one of {HEADSHOT_RECORD_VERSIONS}, got {record_version!r}")
    if origin not in ("generated", "imported_synthetic"):
        raise HeadshotError(f"origin must be generated|imported_synthetic, got {origin!r}")
    if origin == "imported_synthetic" and not import_receipt_id:
        raise HeadshotError("an imported_synthetic hero must cite its import receipt")
    if origin == "generated" and import_receipt_id:
        raise HeadshotError("a generated hero has no import receipt")
    if not _is_hex64(asset_id):
        raise HeadshotError("asset_id must be a lowercase sha256 hex digest")
    if not _is_hex64(candidates_checkpoint_digest):
        raise HeadshotError("candidates_checkpoint_digest must be a lowercase sha256 hex digest")
    if not _is_hex64(look_hash):
        raise HeadshotError("look_hash must be a lowercase sha256 hex digest")
    record: dict[str, Any] = {
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
    override_args = (qc_override_receipt_id, qc_override_record_sha256, field_manifest_sha256, legacy_citations)
    if record_version != HEADSHOT_RECORD_VERSION_1_2 and any(a is not None for a in override_args):
        raise HeadshotError("override citation fields belong to record_version 1.2 only")
    if record_version == "1.0":
        if generation_receipt_id is not None or qc_receipt_id is not None:
            raise HeadshotError("a 1.0 headshot record carries no generation_receipt_id / qc_receipt_id; use record_version 1.1")
        return record
    if not isinstance(generation_receipt_id, str) or not generation_receipt_id:
        raise HeadshotError(f"a {record_version} headshot record must seal generation_receipt_id (the import receipt for an imported hero)")
    if origin == "imported_synthetic" and generation_receipt_id != import_receipt_id:
        raise HeadshotError("an imported hero's generation_receipt_id must equal its import_receipt_id")
    if not isinstance(qc_receipt_id, str) or not qc_receipt_id:
        raise HeadshotError(f"a {record_version} headshot record must seal qc_receipt_id (the hero verdict the gate relied on)")
    record.update({
        "record_version": record_version,
        "generation_receipt_id": generation_receipt_id,
        "qc_receipt_id": qc_receipt_id,
    })
    if record_version == HEADSHOT_RECORD_VERSION_1_1:
        return record
    # 1.2 — the conditional-shape matrix (r3 #7 / r4 #2 #5 #7):
    #   clean pass:            no batch fields, legacy_citations []
    #   legacy 1.0 coverage:   legacy_citations nonempty, no batch fields
    #   batch-unlocked:        all three batch fields, legacy_citations []
    #   imported selection:    all override fields null/[], like a clean pass
    batch = (qc_override_receipt_id, qc_override_record_sha256, field_manifest_sha256)
    has_batch = [b is not None for b in batch]
    if any(has_batch) and not all(has_batch):
        raise HeadshotError("a 1.2 record's batch citation is all-or-none: qc_override_receipt_id + qc_override_record_sha256 + field_manifest_sha256")
    legacy = list(legacy_citations or [])
    if all(has_batch) and legacy:
        raise HeadshotError("a 1.2 record carries a batch citation OR legacy_citations, never both")
    if origin == "imported_synthetic" and (any(has_batch) or legacy):
        raise HeadshotError("an imported selection carries no override citation fields")
    for c in legacy:
        if not isinstance(c, dict) or set(c) != {"receipt_id", "record_sha256"} \
                or not isinstance(c.get("receipt_id"), str) or not c["receipt_id"] \
                or not _is_hex64(c.get("record_sha256")):
            raise HeadshotError("each legacy citation is exactly {receipt_id, record_sha256 (lowercase sha256 hex)}")
    if legacy != sorted(legacy, key=lambda c: c["receipt_id"]) or len({c["receipt_id"] for c in legacy}) != len(legacy):
        raise HeadshotError("legacy_citations must be unique and sorted by receipt_id")
    if all(has_batch):
        for name, val in (("qc_override_record_sha256", qc_override_record_sha256), ("field_manifest_sha256", field_manifest_sha256)):
            if not _is_hex64(val):
                raise HeadshotError(f"a 1.2 record's {name} must be a lowercase sha256 hex digest")
        if not isinstance(qc_override_receipt_id, str) or not qc_override_receipt_id:
            raise HeadshotError("a 1.2 record's qc_override_receipt_id must be a receipt id")
    record.update({
        "qc_override_receipt_id": qc_override_receipt_id,
        "qc_override_record_sha256": qc_override_record_sha256,
        "field_manifest_sha256": field_manifest_sha256,
        "legacy_citations": legacy,
    })
    return record


def record_version_of(record: dict[str, Any]) -> str:
    """``"1.0"`` for a legacy record (no ``record_version`` field), else the field."""
    v = record.get("record_version")
    return "1.0" if v is None else str(v)


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
            rv = record_version_of(record)
            if rv not in HEADSHOT_RECORD_VERSIONS:
                raise HeadshotError(f"headshot receipt {row.get('receipt_id')} record_version {rv!r} is unknown")
            wanted = {"1.0": HEADSHOT_RECORD_FIELDS,
                      HEADSHOT_RECORD_VERSION_1_1: HEADSHOT_RECORD_FIELDS_1_1,
                      HEADSHOT_RECORD_VERSION_1_2: HEADSHOT_RECORD_FIELDS_1_2}[rv]
            missing = [f for f in wanted if f not in record]
            if missing:
                raise HeadshotError(f"headshot receipt {row.get('receipt_id')} record lacks {missing}")
            if rv != "1.0":
                extras = sorted(set(record) - set(wanted))
                if extras:
                    raise HeadshotError(f"headshot receipt {row.get('receipt_id')} record carries unknown fields {extras}")
                for f in ("asset_id", "normalized_pixel_hash", "look_hash", "candidates_checkpoint_digest"):
                    if not _is_hex64(record.get(f)):
                        raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: {f} must be a lowercase sha256 hex digest")
            if rv == HEADSHOT_RECORD_VERSION_1_2:
                # Replay enforces the 1.2 conditional matrix, not just presence:
                # batch citation all-or-none, exclusive with legacy_citations.
                trio = [record.get("qc_override_receipt_id"), record.get("qc_override_record_sha256"),
                        record.get("field_manifest_sha256")]
                has = [t is not None for t in trio]
                if any(has) and not all(has):
                    raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: 1.2 batch citation is all-or-none")
                if all(has) and record.get("legacy_citations"):
                    raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: 1.2 record carries batch citation AND legacy_citations")
                if record.get("origin") == "imported_synthetic" and (any(has) or record.get("legacy_citations")):
                    raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: an imported 1.2 record carries no override citations")
                for f in ("qc_override_record_sha256", "field_manifest_sha256"):
                    val = record.get(f)
                    if val is not None and not _is_hex64(val):
                        raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: {f} must be a lowercase sha256 hex digest")
                lc = record.get("legacy_citations") or []
                for c in lc:
                    if not isinstance(c, dict) or set(c) != {"receipt_id", "record_sha256"} \
                            or not isinstance(c.get("receipt_id"), str) or not c["receipt_id"] \
                            or not _is_hex64(c.get("record_sha256")):
                        raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: malformed legacy citation")
                if lc != sorted(lc, key=lambda c: c["receipt_id"]) or len({c["receipt_id"] for c in lc}) != len(lc):
                    raise HeadshotError(f"headshot receipt {row.get('receipt_id')}: legacy_citations must be unique and sorted")
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
    _verify_downstream(project_dir, current, look)
    return ref


def _verify_downstream(project_dir: Path | str, current: ActiveHeadshot, look: Any) -> None:
    """D20: under authored-film 1.4 the active headshot must rest on a
    verifiable hero verdict (record 1.1) or a grandfather attestation."""
    from lib.canon_enforcement import _is_hero_qc_manifest
    from lib.headshot_verify import HeadshotVerifyError, verify_active_headshot
    from lib.pipeline_pin import PipelinePinError, _read_marker, pinned_pipeline
    from lib.project_config import ProjectConfigError, load_verified_project_config

    root = Path(project_dir)
    try:
        pin = pinned_pipeline(root, str(_read_marker(root).get("pipeline_type") or "authored-film"))
    except PipelinePinError as exc:
        raise HeadshotError(f"cannot resolve the project's pinned pipeline: {exc}") from exc
    if not _is_hero_qc_manifest(pin):
        return
    try:
        config = load_verified_project_config(root)
        verify_active_headshot(root, current, active_look=look, config=config, pin=pin)
    except (ProjectConfigError, HeadshotVerifyError) as exc:
        raise HeadshotError(str(exc)) from exc


def headshot_request(
    project_dir: Path | str,
    project_id: str,
    entity_id: str,
    *,
    request_id: Optional[str] = None,
    summary: Optional[str] = None,
    source_checkpoint_digest: Optional[str] = None,
) -> Path:
    """Write the selection-gate request for one character (mints nothing;
    carries NO approval_record — the handler builds it from the pending
    packet in checkpoint_headshots.json). ``source_checkpoint_digest`` is
    signed into the receipt by the gate and is what a run binds its finish to."""
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
        "source_checkpoint_digest": source_checkpoint_digest,
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

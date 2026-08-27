"""D19.3 — the ONE character-sheet verifier (Codex R1#11).

Called by ``lib.canon_enforcement`` at checkpoint write, by
``scripts/gate_approve.py`` when constructing a sheet/hero record, and again
inside the gate's pre-commit check. Raises ``CheckpointValidationError`` (the
canon contract error) so every caller fails the same way.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from lib.sheet_qc.policy import SHEET_ROLES
from lib.sheet_qc.verify import MANDATORY_ROLES, QCRequired, require_qc_pass


def verify_character_sheet(
    project_dir: Path | str,
    entry: dict[str, Any],
    *,
    active_look: Any,
    active_headshot: Any,
    receipts_by_sha: dict[str, dict[str, Any]],
    qc_required: bool,
    config: Any = None,
    qc_must_be_present: bool = True,
) -> dict[str, Any]:
    """Lineage, look binding, headshot binding and prompt-recipe fidelity for
    every ImageRef of a character entry; under a QC manifest (1.3) also the
    role set and — when ``qc_must_be_present`` — a passing verdict per role.

    ``qc_must_be_present`` is False for a draft entry still being assembled
    (a run writes the awaiting_human draft only after every role passed, so
    a draft without qc_receipts is legal but an APPROVED entry never is)."""
    from lib.canon_enforcement import (
        _check_lineage, _check_sheet_prompt_recipe, _fail, _receipt_for, _require_look_refs, sheet_roles,
    )

    project_dir = Path(project_dir)
    eid = entry.get("id")
    roles = sheet_roles(entry)
    if qc_required:
        bad = [r for r in roles if r not in SHEET_ROLES]
        if bad:
            _fail(f"character {eid!r} sheet carries legacy roles {bad}; authored-film 1.3 sheets are turnaround + expressions (+ wardrobe).")
        missing = [r for r in MANDATORY_ROLES if r not in roles]
        if missing and entry.get("status") != "draft":
            _fail(f"character {eid!r} sheet is missing mandatory roles {missing}.")
    if active_look is None:
        _fail(f"character {eid!r} has no active look; sheets are built only from ratified looks.")
    if active_headshot is None:
        _fail(f"character {eid!r} has no approved headshot; a face is approved before any sheet.")
    hero = entry.get("hero") or {}
    if hero.get("asset_id") != active_headshot.asset_id:
        _fail(f"character {eid!r} hero {hero.get('asset_id')} is not the approved headshot {active_headshot.asset_id}.")
    wanted = {"entity_id": eid, "asset_id": active_headshot.asset_id, "approval_receipt_id": active_headshot.receipt_id}
    refs = [("hero", hero)] + [(role, (entry.get("sheet") or {}).get(role) or {}) for role in roles]
    for role, ref in refs:
        label = f"character {eid!r} {role}"
        _check_lineage(project_dir, label, ref.get("asset_id"), receipts_by_sha)
        receipt = _receipt_for(ref, receipts_by_sha)
        _require_look_refs(label, ref, receipt, ("character", eid), active_look.look_hash)
        if role != "hero" and receipt.get("headshot_ref") != wanted:
            _fail(
                f"{label}: generation receipt {receipt.get('receipt_id')!r} headshot_ref "
                f"{receipt.get('headshot_ref')!r} does not name the approved hero {wanted} — "
                f"sheets derive only from the approved headshot."
            )
        if role != "hero":
            _check_sheet_prompt_recipe(label, ref, receipt, active_look.look_hash)
    verdicts: dict[str, Any] = {}
    if qc_required and (qc_must_be_present or entry.get("qc_receipts")):
        if config is None:
            _fail(f"character {eid!r}: sheet QC verification needs the verified project config.")
        try:
            verdicts = require_qc_pass(project_dir, entry, config=config, active_look=active_look, active_headshot=active_headshot)
        except QCRequired as exc:
            _fail(f"character {eid!r}: {exc}")
    return verdicts

"""D19.3 — ``require_qc_pass``: does this character entry carry, for every
sheet role, a signed QC verdict that is current, config-bound, reached inside
a cap-compliant signed attempt chain, and passing (or overridden item by item
by a signed ``qc_override`` receipt)?

Raises ``QCRequired`` naming the role and the reasons. Used by the checkpoint
writer, the gate constructor and the gate pre-commit (one verifier, three
callers — Codex R1#11).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from lib.sheet_qc.policy import SHEET_ROLES

MANDATORY_ROLES = ("turnaround", "expressions")


class QCRequired(RuntimeError):
    def __init__(self, role: str, reasons: list[str]) -> None:
        self.role, self.reasons = role, list(reasons)
        super().__init__(f"sheet role {role!r} has no acceptable QC verdict: " + "; ".join(reasons))


def overrides_for(project_dir: Path | str, qc_receipt_id: str, entity_id: str) -> set[str]:
    """Item ids a human has explicitly accepted for ONE verdict (signed
    qc_override receipts bound to that receipt id)."""
    from lib.receipts import verified_approvals

    covered: set[str] = set()
    for r in verified_approvals(project_dir, "qc_override", entity_id=entity_id):
        rec = r.get("record") or {}
        if r.get("qc_receipt_id") == qc_receipt_id and rec.get("qc_receipt_id") == qc_receipt_id:
            covered.update(str(i) for i in rec.get("item_ids") or [])
    return covered


def require_qc_pass(
    project_dir: Path | str, entry: dict[str, Any], *, config: Any, active_look: Any, active_headshot: Any,
) -> dict[str, dict[str, Any]]:
    """Return ``{role: verdict_row}`` for every sheet role or raise QCRequired."""
    from lib import qc_receipts

    project_dir = Path(project_dir)
    qc = config.require_qc()
    sheet = entry.get("sheet") or {}
    roles = sorted(sheet.keys())
    declared = entry.get("qc_receipts") or {}
    out: dict[str, dict[str, Any]] = {}
    for role in roles:
        reasons: list[str] = []
        ref = sheet.get(role) or {}
        rid = declared.get(role)
        if not rid:
            raise QCRequired(role, ["entry.qc_receipts names no verdict for this role"])
        row = qc_receipts.find_verdict_by_id(project_dir, rid)
        if row is None:
            raise QCRequired(role, [f"qc receipt {rid!r} is not a verified verdict in this project's QC chain"])
        if row.get("asset_id") != ref.get("asset_id"):
            reasons.append(f"verdict is for asset {str(row.get('asset_id'))[:12]}, entry has {str(ref.get('asset_id'))[:12]}")
        if row.get("role") != role:
            reasons.append(f"verdict role {row.get('role')!r} != {role!r}")
        if row.get("entity_id") != entry.get("id"):
            reasons.append("verdict names another entity")
        if active_look is None or row.get("look_hash") != active_look.look_hash or row.get("look_receipt_id") != active_look.receipt_id:
            reasons.append("verdict was judged against a look that is not the active look")
        if active_headshot is None or row.get("headshot_receipt_id") != active_headshot.receipt_id \
                or row.get("headshot_asset_id") != active_headshot.asset_id:
            reasons.append("verdict was judged against a headshot that is not the active headshot tip")
        if row.get("policy_bundle_sha256") != qc.policy_bundle_sha256:
            reasons.append("verdict policy bundle is not the one pinned in the signed config")
        if row.get("provider") == "local":
            reasons.append("deterministic pre-check failure; cannot be overridden — regenerate")
        elif row.get("provider") != qc.judge_provider or row.get("model") != qc.judge_model:
            reasons.append(f"verdict judge {row.get('provider')}/{row.get('model')} is not the configured judge")
        # attempt chain (Codex R4#2)
        chain = qc_receipts.attempt_rows(project_dir, str(row.get("attempt_id")))
        started, gen, att = chain["started"], chain["generation"], chain["verdict"]
        if started is None:
            reasons.append("verdict names no signed attempt_started row")
        else:
            if int(started.get("attempt_n") or 0) > qc.max_attempts_per_series:
                reasons.append(f"attempt {started.get('attempt_n')} exceeds the signed cap {qc.max_attempts_per_series}")
            key = started.get("series_key") or {}
            if key.get("judge_provider") != qc.judge_provider or key.get("judge_model") != qc.judge_model \
                    or key.get("policy_bundle_sha256") != qc.policy_bundle_sha256:
                reasons.append("attempt series does not name the configured judge/policy")
            if key.get("entity_id") != entry.get("id") or key.get("role") != role:
                reasons.append("attempt series is for another entity or role")
        gen_receipt = (ref.get("provenance") or {}).get("generation_receipt_id")
        if gen is None or gen.get("asset_id") != ref.get("asset_id") or gen.get("generation_receipt_id") != gen_receipt:
            reasons.append("attempt's generation_attached row does not name this asset and its generation receipt")
        if att is None or att.get("qc_receipt_id") != row.get("receipt_id"):
            reasons.append("attempt's verdict_attached row does not name this verdict")
        if row.get("verdict") != "pass" and row.get("provider") != "local":
            failing = set(row.get("failing_items") or [])
            covered = overrides_for(project_dir, row["receipt_id"], str(entry.get("id")))
            uncovered = sorted(failing - covered)
            if uncovered:
                reasons.append(f"verdict failed {sorted(failing)}; no signed qc_override covers {uncovered}")
        if reasons:
            raise QCRequired(role, reasons)
        out[role] = row
    return out

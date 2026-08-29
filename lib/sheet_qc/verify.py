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


NON_OVERRIDABLE = frozenset({"coverage"})


class SeriesMismatch(RuntimeError):
    pass


def verify_series_against_receipt(series_key: dict[str, Any], gen_receipt: dict[str, Any] | None, *, asset_id: str | None = None) -> None:
    """The attempt series a caller names must be what the sealed generation
    receipt says happened (Codex inspection #1): builder policy from the
    sealed prompt_recipe, generation endpoint/model from the receipt, look and
    headshot from the receipt's look_refs / headshot_ref. Otherwise a caller
    could rotate one series field per candidate and never hit the cap."""
    if not isinstance(gen_receipt, dict):
        raise SeriesMismatch("no verified generation receipt for the judged asset")
    if asset_id is not None and gen_receipt.get("output_sha256") != asset_id:
        raise SeriesMismatch("generation receipt is for another asset")
    if series_key.get("role") == "hero":
        _verify_hero_series(series_key, gen_receipt)
        return
    if gen_receipt.get("generator_kind") == "imported":
        raise SeriesMismatch("an imported receipt can only back a hero series")
    recipe = gen_receipt.get("prompt_recipe") or {}
    if recipe.get("builder_policy_sha256") != series_key.get("builder_policy_sha256"):
        raise SeriesMismatch("series builder_policy_sha256 is not the sealed prompt_recipe's")
    endpoint = gen_receipt.get("model_endpoint")
    if series_key.get("generation_endpoint") != endpoint or series_key.get("generation_model") != endpoint:
        raise SeriesMismatch("series generation endpoint/model is not the receipt's model_endpoint")
    looks = gen_receipt.get("look_refs") or []
    if not any(isinstance(l, dict) and l.get("look_hash") == series_key.get("look_hash")
               and l.get("entity_id") == series_key.get("entity_id") for l in looks):
        raise SeriesMismatch("series look_hash is not among the receipt's look_refs")
    href = gen_receipt.get("headshot_ref") or {}
    if href.get("approval_receipt_id") != series_key.get("headshot_receipt_id") or href.get("entity_id") != series_key.get("entity_id"):
        raise SeriesMismatch("series headshot_receipt_id is not the receipt's headshot_ref")


def _verify_hero_series(series_key: dict[str, Any], gen_receipt: dict[str, Any]) -> None:
    """D20 R1#8: a hero series names NO headshot (the hero is what is being
    chosen). Generated candidate: the receipt carries look_refs for the entity
    and no headshot_ref, and the series' builder policy / endpoint come from
    the sealed receipt. Imported candidate: the receipt is the ``imported``
    generation receipt and the series carries the ``imported`` sentinels."""
    from lib.qc_receipts import IMPORTED_SENTINEL

    if series_key.get("headshot_receipt_id") is not None:
        raise SeriesMismatch("a hero series must have headshot_receipt_id null (no headshot exists yet)")
    if gen_receipt.get("headshot_ref"):
        raise SeriesMismatch("a hero candidate's generation receipt must carry no headshot_ref")
    if gen_receipt.get("generator_kind") == "imported":
        if series_key.get("builder_policy_sha256") != IMPORTED_SENTINEL or \
                series_key.get("generation_endpoint") != IMPORTED_SENTINEL or series_key.get("generation_model") != IMPORTED_SENTINEL:
            raise SeriesMismatch("an imported candidate's series must carry the 'imported' sentinel for builder policy, endpoint and model")
        if gen_receipt.get("prompt_recipe"):
            raise SeriesMismatch("an imported receipt carries no prompt_recipe")
        return
    if series_key.get("builder_policy_sha256") == IMPORTED_SENTINEL:
        raise SeriesMismatch("the 'imported' sentinel is only for an imported candidate")
    recipe = gen_receipt.get("prompt_recipe") or {}
    if recipe.get("builder_policy_sha256") != series_key.get("builder_policy_sha256"):
        raise SeriesMismatch("series builder_policy_sha256 is not the sealed prompt_recipe's")
    endpoint = gen_receipt.get("model_endpoint")
    if series_key.get("generation_endpoint") != endpoint or series_key.get("generation_model") != endpoint:
        raise SeriesMismatch("series generation endpoint/model is not the receipt's model_endpoint")
    looks = gen_receipt.get("look_refs") or []
    if not any(isinstance(l, dict) and l.get("look_hash") == series_key.get("look_hash")
               and l.get("entity_id") == series_key.get("entity_id") for l in looks):
        raise SeriesMismatch("series look_hash is not among the receipt's look_refs")


def raw_response_ok(project_dir: Path | str, row: dict[str, Any]) -> bool:
    """The signed raw_response_asset_id must still name a file whose bytes hash to it."""
    import hashlib

    sha = row.get("raw_response_asset_id")
    if not isinstance(sha, str) or len(sha) != 64:
        return False
    path = Path(project_dir) / "canon" / "qc" / "objects" / f"{sha}.json"
    try:
        if path.is_symlink() or not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == sha
    except OSError:
        return False


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
    receipts_by_sha: dict[str, dict[str, Any]] | None = None,
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
        else:
            if row.get("provider") != qc.judge_provider or row.get("model") != qc.judge_model:
                reasons.append(f"verdict judge {row.get('provider')}/{row.get('model')} is not the configured judge")
            if not row.get("provider_request_id"):
                reasons.append("verdict carries no provider request id")
            if not raw_response_ok(project_dir, row):
                reasons.append("the signed raw provider response is missing or altered (canon/qc/objects)")
        # attempt chain (Codex R4#2)
        chain = qc_receipts.attempt_rows(project_dir, str(row.get("attempt_id")))
        started, gen, att = chain["started"], chain["generation"], chain["verdict"]
        if chain.get("voided") is not None:
            reasons.append("the verdict's attempt was voided; a voided attempt is terminal")
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
            try:
                verify_series_against_receipt(key, receipts_by_sha.get(ref.get("asset_id")) if receipts_by_sha else None,
                                              asset_id=ref.get("asset_id"))
            except SeriesMismatch as exc:
                reasons.append(f"attempt series does not match the sealed generation receipt: {exc}")
        gen_receipt = (ref.get("provenance") or {}).get("generation_receipt_id")
        if gen is None or gen.get("asset_id") != ref.get("asset_id") or gen.get("generation_receipt_id") != gen_receipt:
            reasons.append("attempt's generation_attached row does not name this asset and its generation receipt")
        if att is None or att.get("qc_receipt_id") != row.get("receipt_id"):
            reasons.append("attempt's verdict_attached row does not name this verdict")
        if row.get("verdict") != "pass" and row.get("provider") != "local":
            failing = set(row.get("failing_items") or [])
            if failing & NON_OVERRIDABLE:
                reasons.append(f"verdict failed on {sorted(failing & NON_OVERRIDABLE)} (judge protocol failure); not overridable — re-judge")
            covered = overrides_for(project_dir, row["receipt_id"], str(entry.get("id"))) - NON_OVERRIDABLE
            uncovered = sorted(failing - covered)
            if uncovered:
                reasons.append(f"verdict failed {sorted(failing)}; no signed qc_override covers {uncovered}")
        if reasons:
            raise QCRequired(role, reasons)
        out[role] = row
    return out

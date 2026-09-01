"""D19.3 — ``require_qc_pass``: does this character entry carry, for every
sheet role, a signed QC verdict that is current, config-bound, reached inside
a cap-compliant signed attempt chain, and passing (or overridden item by item
by a signed ``qc_override`` receipt)?

Raises ``QCRequired`` naming the role and the reasons. Used by the checkpoint
writer, the gate constructor and the gate pre-commit (one verifier, three
callers — Codex R1#11).
"""
from __future__ import annotations

import hashlib
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
    qc_override receipts bound to that receipt id). Record-1.1 batch receipts
    never match here: their envelope carries no qc_receipt_id."""
    from lib.receipts import verified_approvals

    covered: set[str] = set()
    for r in verified_approvals(project_dir, "qc_override", entity_id=entity_id):
        rec = r.get("record") or {}
        if r.get("qc_receipt_id") == qc_receipt_id and rec.get("qc_receipt_id") == qc_receipt_id:
            covered.update(str(i) for i in rec.get("item_ids") or [])
    return covered


def batch_row_for(
    project_dir: Path | str, *, override_receipt_id: str, override_record_sha256: str,
    qc_receipt_id: str, asset_id: str, field_manifest_sha256: str, entity_id: str,
) -> Optional[set[str]]:
    """Accepted item ids for ONE candidate under its CITED record-1.1 batch
    receipt — authority is carried, never discovered by scanning. The whole
    binding is enforced: exact receipt id + record hash (``exact_approval``),
    record_version 1.1, manifest equality, the row matching BOTH verdict and
    asset, and the asset appearing in the previewed ``unlocked_asset_ids``.
    Any mismatch returns None (not covered)."""
    from lib.receipts import ReceiptError, exact_approval

    try:
        r = exact_approval(
            project_dir, receipt_id=override_receipt_id, kind="qc_override",
            entity_id=entity_id, record_sha256=override_record_sha256,
        )
    except ReceiptError:
        return None
    rec = r.get("record") or {}
    if rec.get("record_version") != "1.1":
        return None
    if rec.get("field_manifest_sha256") != field_manifest_sha256:
        return None
    if asset_id not in (rec.get("unlocked_asset_ids") or []):
        return None
    for row in rec.get("batch") or []:
        if row.get("qc_receipt_id") == qc_receipt_id and row.get("asset_id") == asset_id:
            return {str(i) for i in row.get("accepted_item_ids") or []}
    return None


def hero_override_field(
    project_dir: Path | str, entity_id: str, look_hash: str, *, qc: Any,
    rejected: set[str],
) -> list[dict[str, Any]]:
    """The SHARED field validator: every ledger row a hero batch waiver may
    cover, used identically at request construction, signer reconstruction,
    and pre-commit. A row enters the field iff its attempt is committed and
    not voided; its verdict resolves, failed, and was judged by a non-local
    provider under the currently signed hero policy; its series is a real
    model generation (never the imported sentinel, never a grandfather
    series); its budget position is within the signed cap; its failing items
    are all overridable; its generation receipt resolves to the asset; the
    pixels are present and re-hash to the asset id; and the asset is not in
    rejected history. Returns rows sorted canonically."""
    from lib import qc_receipts as qr
    from lib.receipts import find_generation

    root = Path(project_dir)
    out: list[dict[str, Any]] = []
    started_rows = qr.hero_attempts_started(root, entity_id, look_hash)
    ordered_ids = [r.get("attempt_id") for r in started_rows]
    seen_assets: set[str] = set()
    for a in started_rows:
        rows = qr.attempt_rows(root, a["attempt_id"])
        if rows["verdict"] is None or rows.get("voided") is not None:
            continue
        v = qr.find_verdict_by_id(root, rows["verdict"]["qc_receipt_id"])
        if v is None or v.get("attempt_id") != a["attempt_id"]:
            continue
        if v.get("verdict") != "fail" or v.get("provider") == "local":
            continue
        key = a.get("series_key") or {}
        if qr.IMPORTED_SENTINEL in (key.get("generation_endpoint"), key.get("builder_policy_sha256"), key.get("generation_model")):
            continue
        if key.get("grandfather"):
            continue
        if v.get("policy_bundle_sha256") != qc.hero_policy_sha256:
            continue
        position = ordered_ids.index(a["attempt_id"]) + 1
        if position > int(qc.max_hero_attempts):
            continue
        if a.get("budget_cap") is not None and int(a["budget_cap"]) > int(qc.max_hero_attempts):
            continue
        failing = {str(i) for i in v.get("failing_items") or []}
        if not failing or failing & NON_OVERRIDABLE:
            continue
        asset_id = str(v.get("asset_id") or "")
        if not asset_id or asset_id in rejected or asset_id in seen_assets:
            continue
        gen_row = rows.get("generation")
        gen = find_generation(root, asset_id)
        if gen is None or gen_row is None or gen_row.get("asset_id") != asset_id:
            continue
        img = root / "canon" / "visual" / "objects" / f"{asset_id}.png"
        try:
            if img.is_symlink() or not img.is_file() or hashlib.sha256(img.read_bytes()).hexdigest() != asset_id:
                continue
        except OSError:
            continue
        seen_assets.add(asset_id)
        out.append({
            "qc_receipt_id": str(v["receipt_id"]), "asset_id": asset_id,
            "failing_items": sorted(failing), "attempt_id": str(a["attempt_id"]),
        })
    out.sort(key=lambda r: (r["qc_receipt_id"], r["asset_id"]))
    return out


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

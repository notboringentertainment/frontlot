"""D20.3 — two verifiers over one rule set for headshots (Codex R1#3, R2#4).

* ``verify_headshot_candidate`` — selection time, a full ImageRef is in hand:
  lineage, look binding, sealed recipe (required for a generated candidate,
  forbidden for an imported one), the import receipt's character binding
  (R1#13) and, under authored-film 1.4, ``require_hero_verdict``: a passing
  (or item-by-item overridden) signed ``hero`` verdict for exactly that
  asset, look, hero policy bundle and judge, reached inside a compliant
  attempt within the hero budget. Callers: checkpoint validation of any
  ``headshot_packet`` (pending and approved), the gate's candidate
  enumeration / enforcement, and the gate's transactional pre-commit check.
* ``verify_active_headshot`` — downstream, from the SIGNED headshot record
  only (no packet in hand): a 1.1 record reloads the receipts it sealed
  (generation or import, hero verdict) by id and re-checks them; a 1.0
  (legacy) record is accepted under 1.4 only through a valid
  ``headshot_grandfather`` attestation of exactly that chain tip (D20.7).
  Caller: ``lib.headshots.verify_headshot_ref`` (every sheet call and every
  sheet verification).

Both raise ``HeadshotVerifyError``; callers translate it into their own
failure type. Nothing here mints anything.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

GRANDFATHER_KIND = "headshot_grandfather"
HERO_ROLE = "hero"
NON_OVERRIDABLE = frozenset({"coverage"})


class HeadshotVerifyError(RuntimeError):
    pass


def _hero_qc(pin: Any) -> bool:
    from lib.canon_enforcement import _is_hero_qc_manifest

    return _is_hero_qc_manifest(pin)


def grandfather_record(
    *, entity_id: str, legacy_headshot_receipt_id: str, asset_id: str, look_hash: str, qc_receipt_id: str,
) -> dict[str, Any]:
    """The record hashed into a headshot_grandfather receipt (D20.7)."""
    for name, value in (("entity_id", entity_id), ("legacy_headshot_receipt_id", legacy_headshot_receipt_id),
                        ("asset_id", asset_id), ("look_hash", look_hash), ("qc_receipt_id", qc_receipt_id)):
        if not isinstance(value, str) or not value:
            raise HeadshotVerifyError(f"grandfather record needs {name}")
    return {
        "entity_kind": "character",
        "entity_id": entity_id,
        "legacy_headshot_receipt_id": legacy_headshot_receipt_id,
        "asset_id": asset_id,
        "look_hash": look_hash,
        "qc_receipt_id": qc_receipt_id,
    }


# ---- the hero verdict rule set (shared by both verifiers and the gate) ----

def require_hero_verdict(
    project_dir: Path | str, *, qc_receipt_id: Any, asset_id: str, entity_id: str, active_look: Any, config: Any,
    gen_receipt: Optional[dict[str, Any]], grandfather: bool = False,
    override_citation: Optional[dict[str, Any]] = None,
    legacy_citations: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Return the verified ``hero`` verdict row or raise HeadshotVerifyError
    listing every reason. ``grandfather`` says whether the attempt must be
    (or must not be) a legacy-hero grandfather attempt. ``override_citation``
    — ``{qc_override_receipt_id, qc_override_record_sha256,
    field_manifest_sha256}`` — is the candidate's carried batch authority:
    when present, coverage comes ONLY from that cited record-1.1 row
    (verified through ``batch_row_for``'s full binding); when absent, only
    from verdict-bound 1.0 overrides. The two are never unioned."""
    from lib import qc_receipts
    from lib.sheet_qc.verify import SeriesMismatch, batch_row_for, overrides_for, raw_response_ok, verify_series_against_receipt

    project_dir = Path(project_dir)
    qc = config.require_hero_qc()
    reasons: list[str] = []
    if not isinstance(qc_receipt_id, str) or not qc_receipt_id:
        raise HeadshotVerifyError(f"candidate {asset_id[:12]} for {entity_id!r} names no hero verdict (qc_receipt_id)")
    row = qc_receipts.find_verdict_by_id(project_dir, qc_receipt_id)
    if row is None:
        raise HeadshotVerifyError(f"qc receipt {qc_receipt_id!r} is not a verified verdict in this project's QC chain")
    if row.get("role") != HERO_ROLE:
        reasons.append(f"verdict role {row.get('role')!r} is not 'hero'")
    if row.get("asset_id") != asset_id:
        reasons.append(f"verdict is for asset {str(row.get('asset_id'))[:12]}, candidate is {asset_id[:12]}")
    if row.get("entity_id") != entity_id or row.get("entity_kind") != "character":
        reasons.append("verdict names another entity")
    if active_look is None or row.get("look_hash") != active_look.look_hash or row.get("look_receipt_id") != active_look.receipt_id:
        reasons.append("verdict was judged against a look that is not the active look")
    if row.get("headshot_asset_id") is not None or row.get("headshot_receipt_id") is not None:
        reasons.append("a hero verdict must name no headshot (none exists yet)")
    if row.get("policy_bundle_sha256") != qc.hero_policy_sha256:
        reasons.append("verdict policy bundle is not the hero bundle pinned in the signed config")
    if row.get("provider") == "local":
        reasons.append("deterministic pre-check failure; cannot be overridden — regenerate")
    else:
        if row.get("provider") != qc.judge_provider or row.get("model") != qc.judge_model:
            reasons.append(f"verdict judge {row.get('provider')}/{row.get('model')} is not the configured judge")
        if not row.get("provider_request_id"):
            reasons.append("verdict carries no provider request id")
        if not raw_response_ok(project_dir, row):
            reasons.append("the signed raw provider response is missing or altered (canon/qc/objects)")
    chain = qc_receipts.attempt_rows(project_dir, str(row.get("attempt_id")))
    started, gen, att = chain["started"], chain["generation"], chain["verdict"]
    if chain.get("voided") is not None:
        reasons.append("the verdict's attempt was voided; a voided attempt is terminal")
    if started is None:
        reasons.append("verdict names no signed attempt_started row")
    else:
        key = started.get("series_key") or {}
        if key.get("judge_provider") != qc.judge_provider or key.get("judge_model") != qc.judge_model \
                or key.get("policy_bundle_sha256") != qc.hero_policy_sha256:
            reasons.append("attempt series does not name the configured judge/hero policy")
        if key.get("entity_id") != entity_id or key.get("role") != HERO_ROLE:
            reasons.append("attempt series is for another entity or role")
        if bool(key.get("grandfather", False)) != bool(grandfather):
            reasons.append("attempt series grandfather flag does not match this use")
        if key.get("look_hash") != (active_look.look_hash if active_look else None):
            reasons.append("attempt series look_hash is not the active look")
        # budget (R1#5/#6): recomputed across every hero series for this entity + look
        budget_rows = qc_receipts.hero_attempts_started(project_dir, entity_id, str(key.get("look_hash")))
        ids = [r.get("attempt_id") for r in budget_rows]
        position = ids.index(started.get("attempt_id")) + 1 if started.get("attempt_id") in ids else None
        if position is None:
            reasons.append("attempt is not in the hero budget ledger for this entity and look")
        elif position > int(qc.max_hero_attempts):
            reasons.append(f"attempt {position} exceeds the signed hero budget {qc.max_hero_attempts}")
        if started.get("budget_cap") is not None and int(started["budget_cap"]) > int(qc.max_hero_attempts):
            reasons.append("attempt was started under a larger hero budget than the signed config allows")
        try:
            verify_series_against_receipt(key, gen_receipt, asset_id=asset_id)
        except SeriesMismatch as exc:
            reasons.append(f"attempt series does not match the sealed generation receipt: {exc}")
    gen_id = gen_receipt.get("receipt_id") if isinstance(gen_receipt, dict) else None
    if gen is None or gen.get("asset_id") != asset_id or gen.get("generation_receipt_id") != gen_id:
        reasons.append("attempt's generation_attached row does not name this asset and its generation receipt")
    if att is None or att.get("qc_receipt_id") != row.get("receipt_id"):
        reasons.append("attempt's verdict_attached row does not name this verdict")
    if row.get("verdict") != "pass" and row.get("provider") != "local":
        failing = set(row.get("failing_items") or [])
        if failing & NON_OVERRIDABLE:
            reasons.append(f"verdict failed on {sorted(failing & NON_OVERRIDABLE)} (judge protocol failure); not overridable — re-judge")
        if override_citation is not None:
            cit = override_citation
            accepted = batch_row_for(
                project_dir,
                override_receipt_id=str(cit.get("qc_override_receipt_id") or ""),
                override_record_sha256=str(cit.get("qc_override_record_sha256") or ""),
                qc_receipt_id=row["receipt_id"], asset_id=asset_id,
                field_manifest_sha256=str(cit.get("field_manifest_sha256") or ""),
                entity_id=entity_id,
            )
            covered = (accepted or set()) - NON_OVERRIDABLE
        elif legacy_citations is not None:
            # Packet 1.2 seals its legacy authority as an exact citation list
            # (post-build inspection #3): resolve those receipts only — later
            # duplicate overrides can neither grow nor invalidate coverage.
            from lib.receipts import ReceiptError, exact_approval

            covered = set()
            for c in legacy_citations:
                try:
                    r = exact_approval(project_dir, receipt_id=str(c.get("receipt_id") or ""), kind="qc_override",
                                       entity_id=entity_id, record_sha256=str(c.get("record_sha256") or ""))
                except ReceiptError:
                    reasons.append(f"sealed legacy citation {c.get('receipt_id')!r} does not resolve to a verified qc_override")
                    continue
                rec = r.get("record") or {}
                if rec.get("qc_receipt_id") != row["receipt_id"]:
                    reasons.append(f"sealed legacy citation {c.get('receipt_id')!r} is bound to another verdict")
                    continue
                covered |= {str(i) for i in rec.get("item_ids") or []}
            covered -= NON_OVERRIDABLE
        else:
            covered = overrides_for(project_dir, row["receipt_id"], entity_id) - NON_OVERRIDABLE
        uncovered = sorted(failing - covered)
        if uncovered:
            reasons.append(f"verdict failed {sorted(failing)}; no signed qc_override covers {uncovered}")
    if reasons:
        raise HeadshotVerifyError(f"hero verdict for {entity_id!r} / {asset_id[:12]} is not acceptable: " + "; ".join(reasons))
    return row


# ---- candidate-time verifier ----

def _receipt_for_asset(project_dir: Path, asset_id: str, receipts_by_sha: Optional[dict[str, dict[str, Any]]],
                       cited_id: Any = None) -> Optional[dict[str, Any]]:
    """The generation receipt a candidate rests on: the one it CITES when it
    cites one (identical pixels may carry several receipts — inspection #6),
    else the latest for the hash."""
    from lib.receipts import find_generation, find_generation_by_id

    if isinstance(cited_id, str) and cited_id:
        return find_generation_by_id(project_dir, cited_id, output_sha256=asset_id)
    if receipts_by_sha is not None:
        return receipts_by_sha.get(asset_id)
    return find_generation(project_dir, asset_id)


def verify_headshot_candidate(
    project_dir: Path | str, entry: dict[str, Any], candidate: dict[str, Any], *, active_look: Any, config: Any, pin: Any,
    receipts_by_sha: Optional[dict[str, dict[str, Any]]] = None, require_verdict: bool = True,
) -> Optional[dict[str, Any]]:
    """Verify one candidate ImageRef of a pending (or the hero of an approved)
    headshot entry. Returns the hero verdict row under 1.4, else None.
    ``require_verdict=False`` runs every check except the candidate's own
    verdict — for the approved hero of a GRANDFATHERED legacy record, whose
    verdict is bound through the attestation (``verify_active_headshot``)."""
    from lib.receipts import normalize_prompt_recipe
    from lib.reference_import import ReferenceImportError, synthetic_import_receipt, verify_lineage

    project_dir = Path(project_dir)
    entity_id = str(entry.get("entity_id"))
    hero_qc = _hero_qc(pin)
    look_ref = entry.get("look_ref") or {}
    if active_look is None:
        raise HeadshotVerifyError(f"character {entity_id!r} has no active look")
    if look_ref.get("look_hash") != active_look.look_hash or look_ref.get("receipt_id") != active_look.receipt_id:
        raise HeadshotVerifyError(f"entry {entity_id!r} look_ref is not the active look {active_look.look_hash[:12]} / {active_look.receipt_id}")
    asset_id = str(candidate.get("asset_id") or "")
    label = f"candidate {asset_id[:12]} of {entity_id!r}"
    provenance = candidate.get("provenance") or {}
    receipt = _receipt_for_asset(project_dir, asset_id, receipts_by_sha, provenance.get("generation_receipt_id"))
    if receipt is None:
        raise HeadshotVerifyError(f"{label} has no verified generation receipt")
    if receipt.get("receipt_id") != provenance.get("generation_receipt_id"):
        raise HeadshotVerifyError(f"{label} cites generation receipt {provenance.get('generation_receipt_id')!r} but the verified receipt is {receipt.get('receipt_id')}")
    kind = provenance.get("generator_kind")
    if kind != receipt.get("generator_kind"):
        raise HeadshotVerifyError(f"{label} claims generator_kind {kind!r} but its receipt says {receipt.get('generator_kind')!r}")
    try:
        verify_lineage(project_dir, asset_id, receipts_by_sha=receipts_by_sha, label=label)
    except ReferenceImportError as exc:
        raise HeadshotVerifyError(f"{label} lineage refused: {exc}") from exc
    recipe = entry.get("prompt_recipe")
    if recipe is not None:
        try:
            recipe = normalize_prompt_recipe(recipe)
        except ValueError as exc:
            raise HeadshotVerifyError(f"{label}: prompt_recipe invalid: {exc}") from exc
        if recipe["look_hash"] != active_look.look_hash:
            raise HeadshotVerifyError(f"{label}: prompt_recipe names look {recipe['look_hash'][:12]}, not the active look")
    if kind == "imported":
        # A pre-D20 import carries no character binding; for a GRANDFATHERED
        # legacy hero (require_verdict=False) the headshot_grandfather receipt
        # is what binds character to asset, so the unbound import is accepted.
        need_binding = hero_qc and require_verdict
        attestation = synthetic_import_receipt(project_dir, asset_id, entity_id=entity_id if need_binding else None,
                                               receipt_id=receipt.get("attestation_receipt_id"))
        if attestation is None or attestation.get("receipt_id") != receipt.get("attestation_receipt_id"):
            raise HeadshotVerifyError(
                f"{label} has no verified imported_synthetic reference_import receipt "
                f"{receipt.get('attestation_receipt_id')!r}" + (f" bound to character {entity_id!r}" if need_binding else "")
            )
        if provenance.get("attestation_receipt_id") != attestation["receipt_id"] or provenance.get("import_receipt_id") != receipt["receipt_id"]:
            raise HeadshotVerifyError(f"{label} provenance does not cite its attestation/import receipts")
        if hero_qc and require_verdict and recipe is not None:
            raise HeadshotVerifyError(f"{label}: an imported candidate carries no prompt_recipe")
    else:
        if kind not in ("model", "local"):
            raise HeadshotVerifyError(f"{label} generator_kind {kind!r} is not model|local|imported")
        wanted = {"entity_kind": "character", "entity_id": entity_id, "look_hash": active_look.look_hash}
        if wanted not in (receipt.get("look_refs") or []):
            raise HeadshotVerifyError(f"{label} was not receipted with look_ref {wanted} — not generated from the active look")
        if receipt.get("headshot_ref"):
            raise HeadshotVerifyError(f"{label} was generated with a headshot_ref; a hero candidate derives from the look alone")
        if hero_qc and recipe is None:
            raise HeadshotVerifyError(f"{label}: a generated candidate under authored-film 1.4 must carry the sealed prompt_recipe")
        if recipe is not None:
            sealed = receipt.get("prompt_recipe")
            if sealed != recipe:
                raise HeadshotVerifyError(f"{label} was receipted with prompt_recipe {sealed} but the packet claims {recipe}")
            prompt = receipt.get("prompt")
            if not isinstance(prompt, str) or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != recipe["rendered_sha256"]:
                raise HeadshotVerifyError(f"{label}: the receipted prompt does not hash to prompt_recipe.rendered_sha256")
    if not hero_qc or not require_verdict:
        return None
    citation = None
    legacy = None
    if candidate.get("qc_override_receipt_id"):
        # A packet-1.2 candidate carries its batch authority; coverage is
        # tested ONLY through the cited record's full binding (r3 #1/#2).
        citation = {"qc_override_receipt_id": candidate.get("qc_override_receipt_id"),
                    "qc_override_record_sha256": candidate.get("qc_override_record_sha256"),
                    "field_manifest_sha256": candidate.get("field_manifest_sha256")}
    elif isinstance(candidate.get("legacy_citations"), list):
        legacy = list(candidate["legacy_citations"])
    return require_hero_verdict(
        project_dir, qc_receipt_id=candidate.get("qc_receipt_id"), asset_id=asset_id, entity_id=entity_id,
        active_look=active_look, config=config, gen_receipt=receipt, override_citation=citation,
        legacy_citations=legacy,
    )


# ---- downstream verifier (signed record only) ----

def grandfather_receipt_for(project_dir: Path | str, current: Any) -> Optional[dict[str, Any]]:
    """The latest verified headshot_grandfather receipt attesting exactly
    ``current`` (the active headshot tip: receipt id, asset, look)."""
    from lib.receipts import verified_approvals

    match = None
    for r in verified_approvals(project_dir, GRANDFATHER_KIND, entity_id=current.entity_id):
        rec = r.get("record") or {}
        if rec.get("legacy_headshot_receipt_id") == current.receipt_id and rec.get("asset_id") == current.asset_id \
                and rec.get("look_hash") == current.look_hash and r.get("attests_receipt_id") == current.receipt_id:
            match = r
    return match


def verify_active_headshot(project_dir: Path | str, current: Any, *, active_look: Any, config: Any, pin: Any) -> Optional[dict[str, Any]]:
    """Under 1.4, prove the ACTIVE headshot (``lib.headshots.ActiveHeadshot``)
    still rests on a verifiable hero verdict: a 1.1 record reloads its sealed
    receipts; a 1.0 record needs a grandfather attestation of this tip.
    Returns the verdict row (None under earlier manifests)."""
    from lib.headshots import prompt_recipe_sha256, record_version_of
    from lib.receipts import verified_generation_receipts
    from lib.reference_import import synthetic_import_receipt

    if not _hero_qc(pin):
        return None
    project_dir = Path(project_dir)
    record = current.record
    entity_id = current.entity_id
    if active_look is None or active_look.look_hash != current.look_hash:
        raise HeadshotVerifyError(f"headshot for {entity_id!r} was approved against a look that is not the active look")
    rv = record_version_of(record)
    if rv == "1.0":
        gf = grandfather_receipt_for(project_dir, current)
        if gf is None:
            raise HeadshotVerifyError(
                f"headshot for {entity_id!r} is a legacy (1.0) record with no headshot_grandfather attestation of receipt "
                f"{current.receipt_id}; run headshot_run.py --grandfather or re-approve with --replace"
            )
        gen = _generation_by_asset(project_dir, current.asset_id)
        return require_hero_verdict(project_dir, qc_receipt_id=(gf.get("record") or {}).get("qc_receipt_id"), asset_id=current.asset_id,
                                    entity_id=entity_id, active_look=active_look, config=config, gen_receipt=gen, grandfather=True)
    gen_id = record.get("generation_receipt_id")
    gen = None
    for row in verified_generation_receipts(project_dir):
        if row.get("receipt_id") == gen_id:
            gen = row
            break
    if gen is None or gen.get("output_sha256") != current.asset_id:
        raise HeadshotVerifyError(f"headshot record for {entity_id!r} seals generation receipt {gen_id!r}, which is not a verified receipt for asset {current.asset_id[:12]}")
    if record.get("origin") == "imported_synthetic":
        if gen.get("generator_kind") != "imported" or record.get("import_receipt_id") != gen_id:
            raise HeadshotVerifyError(f"headshot record for {entity_id!r} is imported_synthetic but its sealed receipt is not the import receipt")
        att = synthetic_import_receipt(project_dir, current.asset_id, entity_id=entity_id, receipt_id=gen.get("attestation_receipt_id"))
        if att is None or att.get("receipt_id") != gen.get("attestation_receipt_id"):
            raise HeadshotVerifyError(f"imported headshot for {entity_id!r} has no attestation bound to that character")
    else:
        wanted = {"entity_kind": "character", "entity_id": entity_id, "look_hash": current.look_hash}
        if wanted not in (gen.get("look_refs") or []):
            raise HeadshotVerifyError(f"headshot for {entity_id!r}: sealed generation receipt does not bind the active look")
        if record.get("prompt_recipe_sha256") != prompt_recipe_sha256(gen.get("prompt_recipe")):
            raise HeadshotVerifyError(f"headshot for {entity_id!r}: sealed recipe hash differs from the generation receipt's prompt_recipe")
    citation = None
    legacy = None
    if record.get("qc_override_receipt_id"):
        # A 1.2 record sealed its batch authority at selection; active
        # verification replays that exact citation (post-build inspection #1).
        citation = {"qc_override_receipt_id": record.get("qc_override_receipt_id"),
                    "qc_override_record_sha256": record.get("qc_override_record_sha256"),
                    "field_manifest_sha256": record.get("field_manifest_sha256")}
    elif record.get("legacy_citations"):
        legacy = list(record["legacy_citations"])
    return require_hero_verdict(project_dir, qc_receipt_id=record.get("qc_receipt_id"), asset_id=current.asset_id, entity_id=entity_id,
                                active_look=active_look, config=config, gen_receipt=gen,
                                override_citation=citation, legacy_citations=legacy)


def _generation_by_asset(project_dir: Path, asset_id: str) -> Optional[dict[str, Any]]:
    from lib.receipts import find_generation

    return find_generation(project_dir, asset_id)


# ---- migration coverage (D20.7, R3#4) ----

def hero_migration_blockers(project_dir: Path | str) -> list[str]:
    """Why this project may NOT be pinned to 1.4 yet: every active hero of a
    cast character must be a 1.1 record or carry a valid grandfather
    attestation whose verdict verifies under the 1.2 config. Empty = clear."""
    from lib.headshots import HeadshotError, active_headshots, record_version_of
    from lib.look_ingest import active_look_for
    from lib.project_config import ProjectConfigError, load_verified_project_config

    project_dir = Path(project_dir)
    proposal = _read_json(project_dir / "checkpoint_proposal.json")
    cast = (((proposal or {}).get("artifacts") or {}).get("proposal_packet") or {}).get("cast") or {}
    char_ids = [str(c) for c in cast.get("character_ids") or []]
    try:
        heads = active_headshots(project_dir)
    except HeadshotError as exc:
        return [f"headshot chain: {exc}"]
    heroes = {cid: heads[cid] for cid in char_ids if cid in heads}
    if not heroes:
        return []
    try:
        config = load_verified_project_config(project_dir)
        config.require_hero_qc()
    except ProjectConfigError as exc:
        return [f"config: {exc}"]

    class _Pin:
        name, version = "authored-film", "1.4"

    out: list[str] = []
    for cid, current in heroes.items():
        # inspection #5: a 1.1 record is verified too (policy/cap/raw-response drift), not trusted by version.
        try:
            verify_active_headshot(project_dir, current, active_look=active_look_for(project_dir, "character", cid), config=config, pin=_Pin)
        except HeadshotVerifyError as exc:
            out.append(str(exc))
    return out


def _read_json(path: Path) -> Optional[dict]:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

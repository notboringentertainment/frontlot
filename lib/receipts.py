"""Approval receipts (PLAN §5) and generation receipts (PLAN §6).

Approval receipts are appended to ``<project_root>/approvals.jsonl`` only
through ``record_human_approval``, which consumes a one-use gate token bound
to the exact record digest. ``find_approval`` verifies signature + ledger
(full tuple ``token_hmac, receipt_id, record_sha256, project_id``) and treats
anything else as absent. Every lookup requires the receipt's ``project_id``
to equal the active project's id (the project directory name unless passed),
so a receipt copied between projects never approves anything.

Crash safety (inspection fix #18): before the token is consumed, the fully
built receipt is written to a write-ahead entry in the gates dir
(``wal/<token_hmac>.json``). The sequence is WAL → consume → append receipt →
append ledger → delete WAL. ``recover_pending_approvals`` replays WAL entries
whose token was already consumed (idempotent on receipt_id) and discards
entries whose token is still pending (nothing happened; the token stays
usable). ``find_approval`` runs recovery on entry.

Generation receipts are appended to ``<project_root>/generation-receipts.jsonl``
(inspection fix #8): each row carries ``project_id``, is HMAC-signed with the
gates key, and is mirrored to the orchestrator-owned
``generation-ledger.jsonl``. ``find_generation`` returns None for any row
that fails signature or ledger verification, so a fabricated row cannot make
an imported image look synthetic. Writing is fatal on failure: every error
propagates.

Paid outputs are crash-safe through a second WAL (``generation-wal/`` in the
gates dir): a tool stages the entry the moment a verified output exists,
then ``complete_generation`` runs receipt → ledger → terminal reservation →
WAL delete. ``recover_generation_wal`` replays whatever is left over and is
run by ``tools.cost_tracker.resume_check`` before any new paid call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from lib import gates
from lib.canonical_json import record_sha256 as _record_sha256
from lib.state_io import append_jsonl, read_jsonl

# Approval transactions in flight in this process (token_hmac). A pre-commit
# check that reads receipts triggers WAL replay; replay must never commit the
# very transaction whose check is still running (round 2 #2).
_in_flight: set[str] = set()

APPROVALS_FILENAME = "approvals.jsonl"
GENERATION_RECEIPTS_FILENAME = "generation-receipts.jsonl"
QC_RECEIPTS_FILENAME = "qc-receipts.jsonl"

APPROVAL_KINDS = frozenset(
    {
        "hero", "sheet", "location", "poster", "storyboard_batch", "config", "artifact_review",
        # Plan D10 (look locks) and Slice A' (headshots, reference import, manifest pins).
        "look_lock", "headshot", "reference_import", "pipeline_migration",
        # D19.4: a human accepting specific failed QC items of ONE verdict.
        "qc_override",
        # D20.7: a human attesting that a LEGACY (pre-QC) hero passed the hero
        # judge; adjunct to the headshot chain, never part of it.
        "headshot_grandfather",
    }
)
HEADSHOT_GRANDFATHER_FIELDS = ("entity_kind", "entity_id", "legacy_headshot_receipt_id", "asset_id", "look_hash", "qc_receipt_id")
ARTIFACT_REVIEW_FIELDS = ("artifact_type", "artifact_version", "artifact_digest", "migration_status")
GENERATOR_KINDS = frozenset({"model", "local", "imported"})

# Signed envelope fields per approval kind (beyond the hashed record). The
# envelope is what supersession chains and invalidation read; it is part of
# the signed receipt, never a plain column. ``action`` is activate|retire.
RECEIPT_ACTIONS = frozenset({"activate", "retire"})
ENVELOPE_FIELDS: dict[str, dict[str, bool]] = {
    # field -> required
    "look_lock": {
        "action": True, "entity_kind": True, "look_hash": True,
        "supersedes_look_hash": False, "promotion_refs": False, "source_ticket_ref": False,
    },
    "headshot": {
        "action": True, "entity_kind": True, "look_hash": True, "supersedes_receipt_id": False,
    },
    "reference_import": {"origin_class": True, "normalized_pixel_hash": True},
    "pipeline_migration": {"supersedes_receipt_id": False},
    # qc_override has two envelope shapes, enforced exactly-one in
    # validate_envelope: record 1.0 (verdict-bound) carries qc_receipt_id;
    # record 1.1 (field-bound batch) carries field_manifest_sha256.
    "qc_override": {"qc_receipt_id": False, "field_manifest_sha256": False},
    "headshot_grandfather": {"attests_receipt_id": True, "entity_kind": True, "look_hash": True},
}

# Record 1.1 (batch) allowlist: a field-bound waiver over the exact candidates
# the human was shown. No future-verdict authority; the manifest digest binds
# the reviewed field, unlocked_asset_ids binds the previewed result.
QC_OVERRIDE_BATCH_FIELDS = frozenset({
    "record_version", "request_id", "batch", "field_manifest_sha256",
    "unlocked_asset_ids", "look_hash", "look_receipt_id",
    "config_approval_receipt_id", "config_sha256", "budget_cap",
    "attempts_spent", "expected_active_headshot_receipt_id", "reason",
})
# Any 1.1-only key in a versionless record marks a hybrid, which is rejected;
# "reason" is shared with 1.0 and excluded.
_QC_OVERRIDE_BATCH_MARKERS = QC_OVERRIDE_BATCH_FIELDS - {"reason"}
_SHA256_HEX_RE = None  # compiled lazily in _is_sha256_hex


def _is_sha256_hex(value: Any) -> bool:
    global _SHA256_HEX_RE
    if _SHA256_HEX_RE is None:
        import re as _re
        _SHA256_HEX_RE = _re.compile(r"^[a-f0-9]{64}$")
    return isinstance(value, str) and bool(_SHA256_HEX_RE.match(value))


def field_manifest_sha256(rows: list[dict]) -> str:
    """Canonical digest of a reviewed candidate field: sorted
    ``{qc_receipt_id, asset_id, failing_items}`` tuples, canonical JSON."""
    canon = sorted(
        (
            {
                "qc_receipt_id": str(r["qc_receipt_id"]),
                "asset_id": str(r["asset_id"]),
                "failing_items": sorted(str(i) for i in (r.get("failing_items") or [])),
            }
            for r in rows
        ),
        key=lambda r: (r["qc_receipt_id"], r["asset_id"]),
    )
    return _record_sha256({"field_manifest_version": "1", "rows": canon})


def _validate_qc_override_batch(record: dict, env: dict) -> None:
    """Record 1.1: strict allowlist, canonical batch, nonempty previewed
    unlock. Reached only when record_version is present."""
    if record.get("record_version") != "1.1":
        raise ValueError(f"qc_override: unknown record_version {record.get('record_version')!r}")
    unknown = sorted(set(record) - QC_OVERRIDE_BATCH_FIELDS)
    if unknown:
        raise ValueError(f"qc_override 1.1: unknown record fields {unknown}")
    missing = sorted(f for f in QC_OVERRIDE_BATCH_FIELDS - {"expected_active_headshot_receipt_id"} if record.get(f) is None)
    if missing:
        raise ValueError(f"qc_override 1.1: record is missing {missing}")
    if env.get("qc_receipt_id") is not None:
        raise ValueError("qc_override 1.1: envelope must carry field_manifest_sha256, not qc_receipt_id")
    if record["field_manifest_sha256"] != env.get("field_manifest_sha256"):
        raise ValueError("qc_override 1.1: record.field_manifest_sha256 must equal the envelope's")
    if not _is_sha256_hex(record["field_manifest_sha256"]):
        raise ValueError("qc_override 1.1: field_manifest_sha256 must be a sha256 hex digest")
    batch = record["batch"]
    if not isinstance(batch, list) or not batch:
        raise ValueError("qc_override 1.1: batch must be a non-empty list")
    seen_rids: set = set()
    seen_assets: set = set()
    prev_key = None
    for row in batch:
        if not isinstance(row, dict) or set(row) != {"qc_receipt_id", "asset_id", "accepted_item_ids"}:
            raise ValueError("qc_override 1.1: each batch row is exactly {qc_receipt_id, asset_id, accepted_item_ids}")
        rid, aid, items = row["qc_receipt_id"], row["asset_id"], row["accepted_item_ids"]
        if not isinstance(rid, str) or not rid or not isinstance(aid, str) or not aid:
            raise ValueError("qc_override 1.1: batch row ids must be non-empty strings")
        if not isinstance(items, list) or not items or not all(isinstance(i, str) and i for i in items):
            raise ValueError("qc_override 1.1: accepted_item_ids must be a non-empty list of item ids")
        if rid in seen_rids or aid in seen_assets:
            raise ValueError("qc_override 1.1: batch rows must have unique qc_receipt_ids and asset_ids")
        seen_rids.add(rid)
        seen_assets.add(aid)
        key = (rid, aid)
        if prev_key is not None and key <= prev_key:
            raise ValueError("qc_override 1.1: batch rows must be in canonical (qc_receipt_id, asset_id) order")
        prev_key = key
    unlocked = record["unlocked_asset_ids"]
    if not isinstance(unlocked, list) or not unlocked or not all(isinstance(a, str) and a for a in unlocked):
        raise ValueError("qc_override 1.1: unlocked_asset_ids must be a non-empty list — a waiver that unlocks nothing cannot be signed")
    if len(set(unlocked)) != len(unlocked):
        raise ValueError("qc_override 1.1: unlocked_asset_ids must not repeat")
    if not set(unlocked) <= seen_assets:
        raise ValueError("qc_override 1.1: unlocked_asset_ids must be a subset of the batch's asset_ids")
    if not isinstance(record["request_id"], str) or not record["request_id"]:
        raise ValueError("qc_override 1.1: request_id is required")
    for f in ("look_hash", "config_sha256"):
        if not _is_sha256_hex(record[f]):
            raise ValueError(f"qc_override 1.1: {f} must be a sha256 hex digest")
    for f in ("look_receipt_id", "config_approval_receipt_id"):
        if not isinstance(record[f], str) or not record[f]:
            raise ValueError(f"qc_override 1.1: {f} is required")
    eah = record.get("expected_active_headshot_receipt_id")
    if eah is not None and (not isinstance(eah, str) or not eah):
        raise ValueError("qc_override 1.1: expected_active_headshot_receipt_id must be null or a receipt id")
    for f in ("budget_cap", "attempts_spent"):
        v = record[f]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError(f"qc_override 1.1: {f} must be a non-negative integer")
    if not isinstance(record.get("reason"), str) or not record["reason"].strip():
        raise ValueError("qc_override 1.1: record.reason is required")
REFERENCE_ORIGIN_CLASSES = frozenset({"imported_synthetic", "casting_inspiration"})
ENTITY_KINDS = frozenset({"character", "location"})


class ReceiptError(RuntimeError):
    """A required verified receipt is absent or does not cover the request."""


class ReceiptChainError(ReceiptError):
    """The project-local receipt file does not reproduce the orchestrator's
    signed receipt chain (a row was deleted, added, altered or reordered).
    Every reader fails closed on this; nothing is derived from a partial
    projection (Slice A inspection #1)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def approvals_path(project_root: Path | str) -> Path:
    return Path(project_root) / APPROVALS_FILENAME


def qc_receipts_path(project_root: Path | str) -> Path:
    return Path(project_root) / QC_RECEIPTS_FILENAME


def stream_path(project_root: Path | str, stream: str) -> Path:
    if stream == "approval":
        return approvals_path(project_root)
    if stream == "generation":
        return generation_receipts_path(project_root)
    if stream == "qc":
        return qc_receipts_path(project_root)
    raise ValueError(f"no project-local projection for stream {stream!r}")


def generation_receipts_path(project_root: Path | str) -> Path:
    return Path(project_root) / GENERATION_RECEIPTS_FILENAME


def project_id_for(project_root: Path | str, project_id: Optional[str] = None) -> str:
    """The project id a receipt must carry: explicit, else the directory name."""
    if project_id:
        return project_id
    return Path(project_root).resolve().name


# ---- Approval receipts ----


def record_human_approval(
    project_root: Path | str,
    project_id: str,
    stage: str,
    scope: str,
    approval_record: dict,
    gate_token: str,
    kind: str,
    *,
    entity_id: Optional[str] = None,
    artifact: Optional[dict] = None,
    source_checkpoint_digest: Optional[str] = None,
    envelope: Optional[dict] = None,
    pre_commit_check: Optional[Callable[[], None]] = None,
) -> dict:
    """Consume ``gate_token`` for ``approval_record`` and append a signed receipt.

    ``pre_commit_check`` (Slice A #14) runs INSIDE the transaction: after the
    one-use token is consumed (the atomic rename) and before anything is
    appended, ledgered or chained. If it raises, the WAL entry is removed and
    the approval is aborted with the token spent — nothing was signed into
    the ledger. The gate handler uses it to re-derive origin uniqueness for a
    reference import under the consumed token.

    ``kind == 'artifact_review'`` requires ``artifact`` with
    ``artifact_type, artifact_version, artifact_digest, migration_status``;
    every other kind requires ``entity_id``. The approved record itself is
    carried in the receipt (``record``) so per-item checks such as
    ``require_storyboard_receipt`` can be answered from the receipt alone.

    ``envelope`` carries the per-kind signed fields listed in
    ``ENVELOPE_FIELDS`` (look_hash, action, supersedes_*, origin_class ...);
    it is validated against the record here so a receipt can never claim a
    look_hash its own record does not hash to.
    """
    if kind not in APPROVAL_KINDS:
        raise ValueError(f"unknown approval kind {kind!r}")
    if kind == "artifact_review":
        if not artifact or any(f not in artifact for f in ARTIFACT_REVIEW_FIELDS):
            raise ValueError(
                "artifact_review receipts require artifact_type, artifact_version, "
                "artifact_digest, migration_status"
            )
    elif not entity_id:
        raise ValueError(f"approval kind {kind!r} requires entity_id")

    digest = _record_sha256(approval_record)
    envelope = validate_envelope(kind, approval_record, digest, entity_id, envelope)
    # Peek first: an unknown/consumed token fails before any WAL entry exists.
    pending = gates.peek_binding(gate_token)
    token_hmac = pending.token_hmac

    receipt: dict[str, Any] = {
        "receipt_id": str(uuid.uuid4()),
        "kind": kind,
        "project_id": project_id,
        "stage": stage,
        "scope": scope,
        "record_sha256": digest,
        "record": approval_record,
        "token_hmac": token_hmac,
        "source_checkpoint_digest": source_checkpoint_digest,
        "approved_at": _now_iso(),
        "user_response": pending.user_response,
    }
    if kind == "artifact_review":
        for field in ARTIFACT_REVIEW_FIELDS:
            receipt[field] = artifact[field]  # type: ignore[index]
    else:
        receipt["entity_id"] = entity_id
    receipt.update(envelope)
    receipt["signature"] = gates.sign_receipt(receipt)

    root = Path(project_root)
    # Round 2 #2: the whole transaction — WAL, token consume, pre-commit
    # validation (origin-uniqueness recheck), local append, ledger, chain,
    # tip — runs under the per-project/per-stream interprocess lock, so two
    # concurrent approvals are serialized and the second one re-derives its
    # checks against the first one's committed rows.
    with gates.receipt_lock(project_id, "approval"):
        gates.wal_write(token_hmac, {
            "token_hmac": token_hmac,
            "project_id": project_id,
            "project_root": str(root.resolve()),
            "receipt": receipt,
        })
        _in_flight.add(token_hmac)
        try:
            binding = gates.consume_gate_token(gate_token, project_id, stage, scope, digest)
            if pre_commit_check is not None:
                try:
                    pre_commit_check()
                except BaseException:
                    gates.wal_delete(token_hmac)
                    raise
        finally:
            _in_flight.discard(token_hmac)
        _commit_approval(root, binding, receipt)
    return receipt


def validate_envelope(
    kind: str,
    record: dict,
    digest: str,
    entity_id: Optional[str],
    envelope: Optional[dict],
) -> dict:
    """Check the signed envelope of an approval kind against its record and
    return the fields to embed. Kinds without an envelope contract accept
    none (an unexpected envelope is an error, not ignored)."""
    spec = ENVELOPE_FIELDS.get(kind)
    env = dict(envelope or {})
    if spec is None:
        if env:
            raise ValueError(f"approval kind {kind!r} takes no envelope, got {sorted(env)}")
        return {}
    unknown = sorted(set(env) - set(spec))
    if unknown:
        raise ValueError(f"approval kind {kind!r} envelope has unknown fields {unknown}")
    missing = sorted(f for f, required in spec.items() if required and env.get(f) is None)
    if missing:
        raise ValueError(f"approval kind {kind!r} envelope is missing {missing}")
    if "action" in spec and env["action"] not in RECEIPT_ACTIONS:
        raise ValueError(f"envelope action must be one of {sorted(RECEIPT_ACTIONS)}")
    if "entity_kind" in spec and env["entity_kind"] not in ENTITY_KINDS:
        raise ValueError(f"envelope entity_kind must be one of {sorted(ENTITY_KINDS)}")
    if kind in {"look_lock", "headshot"}:
        if record.get("entity_id") != entity_id or record.get("entity_kind") != env["entity_kind"]:
            raise ValueError(
                f"{kind} record key ({record.get('entity_kind')!r}, {record.get('entity_id')!r}) "
                f"does not match the receipt key ({env['entity_kind']!r}, {entity_id!r})"
            )
    if kind == "look_lock":
        if env["action"] == "activate" and env["look_hash"] != digest:
            raise ValueError(
                "look_lock activate: envelope look_hash must equal the canonical hash of the "
                "record (the record IS the validated look_spec payload)"
            )
        if env["action"] == "retire" and record.get("look_hash") != env["look_hash"]:
            raise ValueError("look_lock retire: record.look_hash must equal envelope look_hash")
        if env.get("promotion_refs") is not None and not isinstance(env["promotion_refs"], list):
            raise ValueError("look_lock promotion_refs must be a list")
    if kind == "headshot" and record.get("look_hash") != env["look_hash"]:
        raise ValueError("headshot: record.look_hash must equal envelope look_hash")
    if kind == "qc_override":
        has_rid = env.get("qc_receipt_id") is not None
        has_manifest = env.get("field_manifest_sha256") is not None
        if has_rid == has_manifest:
            raise ValueError("qc_override: envelope carries exactly one of qc_receipt_id (record 1.0) or field_manifest_sha256 (record 1.1)")
        if "record_version" in record or (set(record) & _QC_OVERRIDE_BATCH_MARKERS):
            # 1.1 batch branch — strict, versioned, field-bound.
            _validate_qc_override_batch(record, env)
        else:
            # 1.0 verdict-bound branch — preserved exactly, plus a strict
            # hybrid rejection (a versionless record must not smuggle any
            # 1.1-only field).
            leaked = sorted(set(record) & (_QC_OVERRIDE_BATCH_MARKERS - {"qc_receipt_id", "look_hash"}))
            if leaked:
                raise ValueError(f"qc_override 1.0: record carries 1.1-only fields {leaked}; hybrids are refused")
            if not has_rid:
                raise ValueError("qc_override 1.0: envelope must carry qc_receipt_id")
            if record.get("qc_receipt_id") != env["qc_receipt_id"]:
                raise ValueError("qc_override: record.qc_receipt_id must equal envelope qc_receipt_id")
            items = record.get("item_ids")
            if not isinstance(items, list) or not items or not all(isinstance(i, str) and i for i in items):
                raise ValueError("qc_override: record.item_ids must be a non-empty list of item ids")
            if not isinstance(record.get("reason"), str) or not record["reason"].strip():
                raise ValueError("qc_override: record.reason is required")
    if kind == "headshot_grandfather":
        missing_fields = [f for f in HEADSHOT_GRANDFATHER_FIELDS if not record.get(f)]
        if missing_fields or set(record) != set(HEADSHOT_GRANDFATHER_FIELDS):
            raise ValueError(f"headshot_grandfather: record must be exactly {HEADSHOT_GRANDFATHER_FIELDS}")
        if record.get("entity_id") != entity_id or record.get("entity_kind") != env["entity_kind"]:
            raise ValueError("headshot_grandfather: record key does not match the receipt key")
        if record.get("legacy_headshot_receipt_id") != env["attests_receipt_id"]:
            raise ValueError("headshot_grandfather: record.legacy_headshot_receipt_id must equal envelope attests_receipt_id")
        if record.get("look_hash") != env["look_hash"]:
            raise ValueError("headshot_grandfather: record.look_hash must equal envelope look_hash")
    if kind == "reference_import":
        if env["origin_class"] not in REFERENCE_ORIGIN_CLASSES:
            raise ValueError(f"origin_class must be one of {sorted(REFERENCE_ORIGIN_CLASSES)}")
        for field in ("origin_class", "normalized_pixel_hash"):
            if record.get(field) != env[field]:
                raise ValueError(f"reference_import: record.{field} must equal envelope {field}")
        if not isinstance(env["normalized_pixel_hash"], str) or len(env["normalized_pixel_hash"]) != 64:
            raise ValueError("reference_import: normalized_pixel_hash must be a sha256 hex digest")
    out = {k: env.get(k) for k in spec}
    if kind == "qc_override":
        # exactly-one shape: never embed the absent alternative as a null —
        # a 1.0 envelope keeps its historical bytes ({qc_receipt_id} only).
        out = {k: v for k, v in out.items() if v is not None}
    return out


def _rows_for_commit(root: Path, stream: str, receipt: dict) -> tuple[list[dict], bool]:
    """``(chained rows, already_present)`` for a commit of ``receipt``. The
    one tolerated divergence is a crash between the local append and the
    chain append of THIS receipt (it is the unchained last row); anything
    else fails closed."""
    rid = receipt["receipt_id"]
    try:
        rows = chained_rows(root, stream, project_id=receipt["project_id"])
    except ReceiptChainError:
        path = stream_path(root, stream)
        raw = list(read_jsonl(path))
        tail = raw[-1] if raw else None
        if (
            isinstance(tail, dict) and tail.get("receipt_id") == rid
            and gates.receipt_digest(tail) == gates.receipt_digest(receipt)
        ):
            try:
                gates.verify_local_projection(receipt["project_id"], stream, raw[:-1])
            except gates.ReceiptChainError as exc:
                raise ReceiptChainError(str(exc)) from exc
            return raw[:-1], True
        raise
    return rows, any(r.get("receipt_id") == rid for r in rows)


def _commit_approval(root: Path, binding: gates.GateBinding, receipt: dict) -> None:
    """Idempotent tail of an approval: receipt row, ledger row, chain row, WAL removal."""
    rid = receipt["receipt_id"]
    with gates.receipt_lock(receipt["project_id"], "approval"):
        _, present = _rows_for_commit(root, "approval", receipt)
        if not present:
            append_jsonl(approvals_path(root), receipt)
        if not gates.ledger_has(rid, receipt["record_sha256"], receipt["project_id"], binding.token_hmac):
            gates.ledger_append(binding, rid)
        gates.chain_append(receipt["project_id"], "approval", receipt)
        gates.wal_delete(binding.token_hmac)


def chained_rows(project_root: Path | str, stream: str, *, project_id: Optional[str] = None) -> list[dict]:
    """Every row of the project-local receipt file for ``stream``
    (``approval`` → approvals.jsonl, ``generation`` → generation-receipts.jsonl)
    after proving the file reproduces the orchestrator's signed chain for
    this project root→tip exactly. Raises ReceiptChainError on the first
    divergence; a foreign ``project_id`` row is an extra row."""
    expected_project = project_id_for(project_root, project_id)
    path = stream_path(project_root, stream)
    rows = list(read_jsonl(path))
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReceiptChainError(f"{expected_project}/{stream}: local receipt file row {i} is not an object")
        if row.get("project_id") != expected_project:
            raise ReceiptChainError(
                f"{expected_project}/{stream}: local receipt file row {i} ({row.get('receipt_id')!r}) "
                f"carries project_id {row.get('project_id')!r} — not this project's receipt"
            )
    try:
        gates.verify_local_projection(expected_project, stream, rows)
    except gates.ReceiptChainError as exc:
        raise ReceiptChainError(str(exc)) from exc
    return rows


def recover_pending_approvals(project_root: Path | str) -> list[dict]:
    """Replay WAL entries for ``project_root`` whose token was consumed but
    whose receipt/ledger rows may be missing. Returns the recovered receipts.
    Entries whose token is still pending are dropped (the approval never
    started; the token remains valid)."""
    root = Path(project_root).resolve()
    recovered: list[dict] = []
    for entry in gates.wal_entries():
        token_hmac = entry.get("token_hmac")
        if not isinstance(token_hmac, str) or entry.get("project_root") != str(root):
            continue
        if token_hmac in _in_flight:
            continue  # its pre-commit check is still running; it commits (or aborts) itself
        binding = gates.consumed_binding(token_hmac)
        if binding is None:
            if gates.is_pending(token_hmac):
                gates.wal_delete(token_hmac)
            continue
        receipt = entry.get("receipt")
        if not isinstance(receipt, dict) or not gates.verify_receipt_signature(receipt):
            continue
        _commit_approval(root, binding, receipt)
        recovered.append(receipt)
    return recovered


def find_approval(
    project_root: Path | str,
    kind: str,
    entity_id: Optional[str] = None,
    record_sha256: Optional[str] = None,
    *,
    project_id: Optional[str] = None,
) -> Optional[dict]:
    """Return the latest verified receipt matching the filters, or None.

    Rows lacking a valid signature or full-tuple ledger entry, or carrying a
    ``project_id`` other than this project's, are ignored as forged.
    """
    recover_pending_approvals(project_root)
    match: Optional[dict] = None
    for row in chained_rows(project_root, "approval", project_id=project_id):
        if row.get("kind") != kind:
            continue
        if entity_id is not None and row.get("entity_id") != entity_id:
            continue
        if record_sha256 is not None and row.get("record_sha256") != record_sha256:
            continue
        if not gates.verify_receipt(row):
            continue
        match = row
    return match


def verified_approvals(
    project_root: Path | str,
    kind: str,
    *,
    entity_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> list[dict]:
    """Every verified receipt of ``kind`` for this project, in ledger order.
    Supersession chains (looks, headshots, manifest pins) are replayed over
    this list; forged or foreign rows are invisible."""
    recover_pending_approvals(project_root)
    out: list[dict] = []
    for row in chained_rows(project_root, "approval", project_id=project_id):
        if row.get("kind") != kind:
            continue
        if entity_id is not None and row.get("entity_id") != entity_id:
            continue
        if not gates.verify_receipt(row):
            continue
        if row.get("record_sha256") != _record_sha256(row.get("record")):
            continue
        out.append(row)
    return out


_UNSET: Any = object()


def exact_approval(
    project_root: Path | str,
    *,
    receipt_id: str,
    kind: str,
    entity_id: Optional[str],
    record_sha256: str,
    source_checkpoint_digest: Any = _UNSET,
    project_id: Optional[str] = None,
) -> dict:
    """The verified receipt with exactly ``receipt_id`` — an explicit-id
    lookup, never latest-wins. Raises ReceiptError when the id is absent
    (unverified, forged, foreign, other kind) or when any supplied field
    differs from the row. ``source_checkpoint_digest`` is a sentinel: omitted
    → not compared (canon enforcement, legacy ``None``-digest receipts);
    passed explicitly (``None`` included) → must equal the receipt's field
    (the gate's enriched-marker check, ``_finish_sheet``)."""
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ReceiptError(f"exact_approval needs a receipt_id (got {receipt_id!r})")
    rows = [r for r in verified_approvals(project_root, kind, entity_id=entity_id, project_id=project_id)
            if r.get("receipt_id") == receipt_id]
    if not rows:
        raise ReceiptError(f"no verified {kind} receipt {receipt_id!r} for entity {entity_id!r}")
    if len(rows) > 1:
        raise ReceiptError(f"receipt id {receipt_id!r} names {len(rows)} verified {kind} rows; refusing to guess")
    row = rows[0]
    if row.get("entity_id") != entity_id:
        raise ReceiptError(f"receipt {receipt_id} is for entity {row.get('entity_id')!r}, not {entity_id!r}")
    if row.get("record_sha256") != record_sha256:
        raise ReceiptError(
            f"receipt {receipt_id} signed record_sha256 {row.get('record_sha256')}, not {record_sha256}"
        )
    if source_checkpoint_digest is not _UNSET and row.get("source_checkpoint_digest") != source_checkpoint_digest:
        raise ReceiptError(
            f"receipt {receipt_id} was signed for source_checkpoint_digest {row.get('source_checkpoint_digest')!r}, "
            f"not {source_checkpoint_digest!r}"
        )
    return row


def require_storyboard_receipt(
    project_root: Path | str,
    shot_id: str,
    frame_sha256: str,
    *,
    project_id: Optional[str] = None,
) -> dict:
    """The verified ``storyboard_batch`` receipt whose approved record maps
    ``shot_id`` to exactly ``frame_sha256``. Raises ReceiptError otherwise.
    Tools call this in preflight: no approved frame for the shot, no spend."""
    recover_pending_approvals(project_root)
    expected_project = project_id_for(project_root, project_id)
    rows = [
        r for r in chained_rows(project_root, "approval", project_id=project_id)
        if r.get("kind") == "storyboard_batch"
    ]
    for row in reversed(rows):
        record = row.get("record")
        if not isinstance(record, dict):
            continue
        frames = record.get("storyboard_frames")
        if not isinstance(frames, dict) or frames.get(shot_id) != frame_sha256:
            continue
        if row.get("record_sha256") != _record_sha256(record):
            continue
        if not gates.verify_receipt(row):
            continue
        return row
    raise ReceiptError(
        f"no verified storyboard_batch approval receipt covers shot {shot_id!r} "
        f"with frame sha256 {frame_sha256} in project {expected_project!r} — "
        f"the human approves the storyboard batch before any shot spend."
    )


# ---- Generation receipts ----


def _build_generation_receipt(
    project_root: Path | str,
    *,
    execution_id: str,
    tool: str,
    normalized_inputs_hash: str,
    output_sha256: str,
    cost_usd: float,
    started_at: str,
    finished_at: str,
    model_endpoint: Optional[str] = None,
    provider_request_id: Optional[str] = None,
    generator_kind: str = "model",
    local_tool: Optional[str] = None,
    local_tool_version: Optional[str] = None,
    parameters_hash: Optional[str] = None,
    input_asset_ids: Optional[list[str]] = None,
    prompt: Optional[str] = None,
    seed: Optional[int] = None,
    references_applied: Optional[list[dict]] = None,
    origin_tool: Optional[str] = None,
    attestation_receipt_id: Optional[str] = None,
    look_refs: Optional[list[dict]] = None,
    headshot_ref: Optional[dict] = None,
    import_receipt_id: Optional[str] = None,
    normalized_pixel_hash: Optional[str] = None,
    prompt_recipe: Optional[dict] = None,
) -> dict:
    """Validate the fields and return a signed (not yet persisted) receipt.

    ``generator_kind == 'imported'`` (Slice A') is an attested import of an
    image generated elsewhere: it requires ``origin_tool`` and the
    ``attestation_receipt_id`` of the reference_import approval receipt, and
    can carry no inputs or references (it is a lineage root). ``look_refs``,
    ``headshot_ref`` and ``prompt_recipe`` (the builder recipe whose
    ``rendered_sha256`` is the hash of ``prompt``) are bound in by governed
    visual tools; ``import_receipt_id`` / ``normalized_pixel_hash`` are the
    import provenance a tool passes through for an imported image (the
    pixel hash IS the content address, so it must equal ``output_sha256``).
    """
    if generator_kind not in GENERATOR_KINDS:
        raise ValueError(f"generator_kind must be one of {sorted(GENERATOR_KINDS)}")
    if generator_kind == "model" and not model_endpoint:
        raise ValueError("model generations require model_endpoint")
    if generator_kind == "local" and not local_tool:
        raise ValueError("local generations require local_tool")
    if generator_kind == "imported":
        if not origin_tool or not attestation_receipt_id:
            raise ValueError("imported generations require origin_tool and attestation_receipt_id")
        if input_asset_ids or references_applied:
            raise ValueError("an imported image is a lineage root: no input_asset_ids or references_applied")
    elif origin_tool or attestation_receipt_id:
        raise ValueError("origin_tool/attestation_receipt_id are only valid for generator_kind 'imported'")
    if not output_sha256:
        raise ValueError("output_sha256 is required")
    if references_applied is not None:
        references_applied = [normalize_reference(r) for r in references_applied]
    if look_refs is not None:
        look_refs = [normalize_look_ref(r) for r in look_refs]
    if headshot_ref is not None:
        headshot_ref = normalize_headshot_ref(headshot_ref)
    if prompt_recipe is not None:
        prompt_recipe = normalize_prompt_recipe(prompt_recipe)
    if import_receipt_id is not None and (not isinstance(import_receipt_id, str) or not import_receipt_id):
        raise ValueError("import_receipt_id must be a non-empty string")
    if normalized_pixel_hash is not None:
        if not _is_sha256(normalized_pixel_hash):
            raise ValueError("normalized_pixel_hash must be a 64-hex sha256")
        if normalized_pixel_hash != output_sha256:
            raise ValueError(
                "normalized_pixel_hash must equal output_sha256 (canon objects are the normalized PNG bytes)"
            )
    if not _is_sha256(output_sha256):
        raise ValueError("output_sha256 must be a 64-hex sha256")
    _validate_parents(project_root, output_sha256, input_asset_ids, references_applied)

    receipt = {
        "receipt_id": str(uuid.uuid4()),
        "project_id": project_id_for(project_root),
        "execution_id": execution_id,
        "tool": tool,
        "generator_kind": generator_kind,
        "model_endpoint": model_endpoint,
        "provider_request_id": provider_request_id,
        "local_tool": local_tool,
        "local_tool_version": local_tool_version,
        "normalized_inputs_hash": normalized_inputs_hash,
        "parameters_hash": parameters_hash,
        "input_asset_ids": list(input_asset_ids or []),
        "prompt": prompt,
        "seed": seed,
        "references_applied": references_applied,
        "origin_tool": origin_tool,
        "attestation_receipt_id": attestation_receipt_id,
        "look_refs": look_refs,
        "headshot_ref": headshot_ref,
        "prompt_recipe": prompt_recipe,
        "import_receipt_id": import_receipt_id,
        "normalized_pixel_hash": normalized_pixel_hash,
        "output_sha256": output_sha256,
        "cost_usd": round(float(cost_usd), 6),
        "started_at": started_at,
        "finished_at": finished_at,
    }
    receipt["signature"] = gates.sign_receipt(receipt)
    return receipt


REFERENCE_FIELDS = ("asset_id", "path", "role", "visual_bible_entity_id", "shot_id")


def normalize_reference(ref: dict) -> dict:
    """The comparable form of one ``references_applied`` item: only the
    contract fields, ``None``/missing dropped, so a tool-recorded list and a
    manifest-recorded list compare by content."""
    if not isinstance(ref, dict):
        raise ValueError(f"reference must be an object, got {type(ref).__name__}")
    out = {k: ref[k] for k in REFERENCE_FIELDS if ref.get(k) is not None}
    if not out.get("asset_id") or not out.get("role"):
        raise ValueError(f"reference needs asset_id and role: {ref!r}")
    return out


LOOK_REF_FIELDS = ("entity_kind", "entity_id", "look_hash")
HEADSHOT_REF_FIELDS = ("entity_id", "asset_id", "approval_receipt_id")


def normalize_look_ref(ref: dict) -> dict:
    """Comparable form of one ``look_refs`` item: exactly the contract fields."""
    if not isinstance(ref, dict):
        raise ValueError(f"look_ref must be an object, got {type(ref).__name__}")
    out = {k: ref.get(k) for k in LOOK_REF_FIELDS}
    if out["entity_kind"] not in ENTITY_KINDS or not out["entity_id"]:
        raise ValueError(f"look_ref needs entity_kind (character|location) and entity_id: {ref!r}")
    if not isinstance(out["look_hash"], str) or len(out["look_hash"]) != 64:
        raise ValueError(f"look_ref needs a sha256 look_hash: {ref!r}")
    return out


PROMPT_RECIPE_FIELDS = ("look_hash", "builder_version", "fields_used", "rendered_sha256")
PROMPT_RECIPE_OPTIONAL_FIELDS = ("builder_policy_sha256",)  # D19.6; None on pre-1.3 receipts
_HEX64 = frozenset("0123456789abcdef")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def normalize_prompt_recipe(recipe: dict) -> dict:
    """Comparable form of a builder ``prompt_recipe``: exactly
    ``{look_hash, builder_version, fields_used[], rendered_sha256}``."""
    if not isinstance(recipe, dict):
        raise ValueError(f"prompt_recipe must be an object, got {type(recipe).__name__}")
    out = {k: recipe.get(k) for k in PROMPT_RECIPE_FIELDS}
    if not _is_sha256(out["look_hash"]) or not _is_sha256(out["rendered_sha256"]):
        raise ValueError(f"prompt_recipe needs 64-hex look_hash and rendered_sha256: {recipe!r}")
    if not isinstance(out["builder_version"], str) or not out["builder_version"]:
        raise ValueError(f"prompt_recipe needs a non-empty builder_version: {recipe!r}")
    fields = out["fields_used"]
    if not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields):
        raise ValueError(f"prompt_recipe.fields_used must be a list of field names: {recipe!r}")
    out["fields_used"] = list(fields)
    for k in PROMPT_RECIPE_OPTIONAL_FIELDS:
        v = recipe.get(k)
        if v is None:
            continue  # pre-1.3 recipes stay byte-identical
        if not _is_sha256(v):
            raise ValueError(f"prompt_recipe.{k} must be a 64-hex sha256 when present: {recipe!r}")
        out[k] = v
    return out


def normalize_headshot_ref(ref: dict) -> dict:
    """Comparable form of a ``headshot_ref``: {entity_id, asset_id, approval_receipt_id}."""
    if not isinstance(ref, dict):
        raise ValueError(f"headshot_ref must be an object, got {type(ref).__name__}")
    out = {k: ref.get(k) for k in HEADSHOT_REF_FIELDS}
    if not all(isinstance(v, str) and v for v in out.values()):
        raise ValueError(f"headshot_ref needs entity_id, asset_id, approval_receipt_id: {ref!r}")
    if len(out["asset_id"]) != 64:
        raise ValueError(f"headshot_ref asset_id must be a sha256: {ref!r}")
    return out


PROVENANCE_FIELDS = (
    "generator_kind", "model_endpoint", "local_tool", "local_tool_version", "normalized_inputs_hash",
    "parameters_hash", "input_asset_ids", "prompt", "seed", "references_applied", "origin_tool",
    "attestation_receipt_id", "look_refs", "headshot_ref", "prompt_recipe", "import_receipt_id",
    "normalized_pixel_hash", "output_sha256",
)


def provenance_of(receipt: dict) -> dict:
    """The immutable provenance a generation receipt binds to its output hash
    (everything but ids, cost, timestamps and the signature)."""
    return {k: receipt.get(k) for k in PROVENANCE_FIELDS}


def _validate_parents(
    project_root: Path | str,
    output_sha256: str,
    input_asset_ids: Optional[list[str]],
    references_applied: Optional[list[dict]],
) -> None:
    """Every parent named by a new receipt must be a 64-hex hash (always)
    with a verified generation receipt in this project (Slice A inspection
    #5): lineage can never point at a fabricated or unreceipted ancestor.
    The existence check applies to look-governed projects (a manifest pin
    that declares ``look_lock``); a legacy 1.1 project keeps citing its
    unreceipted references, as the Slice A contract leaves legacy untouched
    — its lineage is still refused by ``verify_lineage`` wherever governance
    reads it."""
    parents: list[Any] = list(input_asset_ids or []) + [r.get("asset_id") for r in (references_applied or [])]
    if not parents:
        return
    for parent in parents:
        if not _is_sha256(parent):
            raise ValueError(f"parent asset id {parent!r} is not a 64-hex sha256")
    from lib.look_ingest import project_look_governed

    if not project_look_governed(project_root):
        return
    receipted = {r["output_sha256"] for r in verified_generation_receipts(project_root)}
    missing = sorted(p for p in set(parents) if p not in receipted)
    if missing:
        raise ValueError(
            f"parent assets {missing} have no verified generation receipt in this project — "
            f"every ancestor must be receipted before a derived output can cite it"
        )


def _commit_generation(root: Path, receipt: dict) -> None:
    """Idempotent tail of a generation: receipt row, ledger row, chain row.
    Provenance is immutable per output hash: a second receipt for an
    already-receipted output is refused unless its provenance is identical."""
    rid = receipt["receipt_id"]
    with gates.receipt_lock(receipt["project_id"], "generation"):
        rows, present = _rows_for_commit(root, "generation", receipt)
        if not present:
            for row in rows:
                if row.get("output_sha256") == receipt["output_sha256"] and gates.verify_generation_receipt(row):
                    if provenance_of(row) != provenance_of(receipt):
                        raise ValueError(
                            f"output {receipt['output_sha256']} already has generation receipt "
                            f"{row['receipt_id']} with different provenance — provenance is immutable per "
                            f"output hash; a receipt can never relabel an existing asset's lineage"
                        )
            append_jsonl(generation_receipts_path(root), receipt)
        if not gates.generation_ledger_has(rid, receipt["output_sha256"], receipt["project_id"]):
            gates.generation_ledger_append(receipt)
        gates.chain_append(receipt["project_id"], "generation", receipt)


def record_generation(project_root: Path | str, **fields: Any) -> dict:
    """Append a signed, ledgered generation receipt. See ``_build_generation_receipt``
    for the fields; ``prompt``, ``seed`` and ``references_applied`` (the ordered
    list of references the tool actually uploaded) are carried in the signed
    record so enforcement can compare the artifact against them."""
    project_id = project_id_for(project_root)
    with gates.receipt_lock(project_id, "generation"):
        receipt = _build_generation_receipt(project_root, **fields)
        _commit_generation(Path(project_root), receipt)
    return receipt


def verified_generation_receipts(
    project_root: Path | str, *, project_id: Optional[str] = None
) -> list[dict]:
    """Every generation receipt row that passes chain + signature + ledger +
    project checks (chain divergence raises ReceiptChainError)."""
    return [
        row for row in chained_rows(project_root, "generation", project_id=project_id)
        if gates.verify_generation_receipt(row)
    ]


def find_generation(
    project_root: Path | str, output_sha256: str, *, project_id: Optional[str] = None
) -> Optional[dict]:
    """Latest verified generation receipt for ``output_sha256``; None for
    missing or forged rows."""
    match = None
    for row in verified_generation_receipts(project_root, project_id=project_id):
        if row.get("output_sha256") == output_sha256:
            match = row
    return match


def find_generation_by_id(
    project_root: Path | str, receipt_id: str, *, output_sha256: Optional[str] = None, project_id: Optional[str] = None
) -> Optional[dict]:
    """The verified generation receipt with ``receipt_id`` (and, when given,
    for ``output_sha256``). Identical pixels may carry more than one receipt
    (inspection D20 #6); a candidate that CITES a receipt is resolved by that
    id, never by latest-for-hash."""
    for row in verified_generation_receipts(project_root, project_id=project_id):
        if row.get("receipt_id") == receipt_id and (output_sha256 is None or row.get("output_sha256") == output_sha256):
            return row
    return None


def _signed_generation_row(
    project_root: Path | str, execution_id: str, output_sha256: str
) -> Optional[dict]:
    """A receipt row for (execution_id, output_sha256) with a valid signature,
    ledgered or not — used by WAL replay to finish a half-written receipt."""
    expected_project = project_id_for(project_root)
    match = None
    for row in read_jsonl(generation_receipts_path(project_root)):
        if not isinstance(row, dict) or row.get("project_id") != expected_project:
            continue
        if row.get("execution_id") != execution_id or row.get("output_sha256") != output_sha256:
            continue
        if gates.verify_receipt_signature(row):
            match = row
    return match


# ---- Generation write-ahead log (crash-safe paid completion) ----


class GenerationWalError(ReceiptError):
    """A staged paid output cannot be completed (output missing or altered).
    ``recovered`` carries the receipts of entries that DID replay before the
    failure was raised (recover_generation_wal finishes what it can)."""

    def __init__(self, message: str, *, recovered: Optional[list[dict]] = None) -> None:
        super().__init__(message)
        self.recovered = list(recovered or [])


def stage_generation_wal(
    project_root: Path | str,
    *,
    execution_id: str,
    outputs: list[dict],
    receipt: dict,
    reservation_id: Optional[str] = None,
    actual_usd: float = 0.0,
) -> Path:
    """Write the WAL entry for a paid output that has landed in staging.

    ``outputs`` items: ``{"staging_path", "output_path", "output_sha256"}``
    (``output_sha256`` is the hash the final file will have). ``receipt``
    holds every ``_build_generation_receipt`` field except ``execution_id``,
    ``output_sha256`` and ``finished_at``. Written BEFORE the file is moved
    into place, so a crash at any later step is replayable.
    """
    root = Path(project_root).resolve()
    for out in outputs:
        if not out.get("output_sha256") or not out.get("output_path"):
            raise ValueError("every WAL output needs output_path and output_sha256")
    entry = {
        "execution_id": execution_id,
        "project_root": str(root),
        "project_id": project_id_for(root),
        "reservation_id": reservation_id,
        "actual_usd": round(float(actual_usd), 6),
        "outputs": [
            {
                "staging_path": str(o["staging_path"]) if o.get("staging_path") else None,
                "output_path": str(o["output_path"]),
                "output_sha256": o["output_sha256"],
            }
            for o in outputs
        ],
        "receipt": dict(receipt),
        "staged_at": _now_iso(),
    }
    return gates.generation_wal_write(execution_id, entry)


def _ensure_wal_output(root: Path, out: dict) -> None:
    """The output named by a WAL entry must exist with its recorded hash; a
    file still in staging (crash before the move) is moved into place."""
    import hashlib

    from lib.pathsafe import (
        PathSafetyError,
        reencode_png_bytes,
        sha256_file,
        validate_output_parent,
    )
    from lib.state_io import atomic_move, atomic_write_bytes

    sha = out["output_sha256"]
    try:
        output_path = validate_output_parent(out["output_path"], root)
    except PathSafetyError as exc:
        raise GenerationWalError(f"WAL output path rejected: {exc}") from exc
    if output_path.is_file():
        actual = sha256_file(output_path)
        if actual != sha:
            raise GenerationWalError(
                f"staged output {output_path} hashes to {actual}, WAL recorded {sha} — "
                f"the paid output was altered after generation"
            )
        return
    staging = Path(out["staging_path"]) if out.get("staging_path") else None
    if staging is not None and staging.is_file():
        if sha256_file(staging) == sha:
            atomic_move(staging, output_path)
            return
        if output_path.suffix.lower() == ".png":
            data = reencode_png_bytes(staging)
            if hashlib.sha256(data).hexdigest() == sha:
                atomic_write_bytes(output_path, data)
                staging.unlink(missing_ok=True)
                return
    raise GenerationWalError(
        f"paid output {output_path} (sha256 {sha}) is missing and no staging copy "
        f"matches — the provider was paid for a file that cannot be receipted; "
        f"recover it (scripts/reconcile_paid_calls.py) before any new paid call"
    )


def replay_generation_entry(project_root: Path | str, entry: dict, *, tracker: Any = None) -> list[dict]:
    """Complete one WAL entry idempotently: output present → receipt (skip if a
    signed row exists) → ledger (skip if present) → terminal reservation
    (skip if already terminal) → delete WAL. Returns the receipts."""
    from tools.cost_tracker import TERMINAL_STATES, CostTracker, load_reservations, reconcile_paid_call

    root = Path(project_root).resolve()
    execution_id = entry["execution_id"]
    fields = dict(entry.get("receipt") or {})
    receipts: list[dict] = []
    seen: set[str] = set()
    for out in entry.get("outputs") or []:
        _ensure_wal_output(root, out)
        sha = out["output_sha256"]
        if sha in seen:
            continue
        seen.add(sha)
        receipt = _signed_generation_row(root, execution_id, sha)
        if receipt is None:
            receipt = _build_generation_receipt(
                root, execution_id=execution_id, output_sha256=sha,
                finished_at=fields.pop("finished_at", None) or _now_iso(), **fields,
            )
        _commit_generation(root, receipt)
        receipts.append(receipt)
    reservation_id = entry.get("reservation_id")
    if reservation_id:
        reservation = load_reservations(root).get(reservation_id)
        if reservation is None:
            raise GenerationWalError(f"WAL entry {execution_id} names unknown reservation {reservation_id}")
        if reservation.get("state") not in TERMINAL_STATES:
            if tracker is None:
                tracker = CostTracker(cost_log_path=root / "cost_log.json")
            reconcile_paid_call(root, reservation_id, float(entry.get("actual_usd") or 0.0), "completed", tracker)
    gates.generation_wal_delete(execution_id)
    return receipts


def complete_generation(project_root: Path | str, execution_id: str, *, tracker: Any = None) -> list[dict]:
    """Finish the paid call staged under ``execution_id`` (see ``stage_generation_wal``)."""
    entry = gates.generation_wal_read(execution_id)
    if entry is None:
        raise GenerationWalError(f"no generation WAL entry for execution {execution_id}")
    return replay_generation_entry(project_root, entry, tracker=tracker)


def recover_generation_wal(project_root: Path | str) -> list[dict]:
    """Replay every incomplete WAL entry for ``project_root``. Entries whose
    output is present are completed and removed; an entry whose output is
    missing or altered raises GenerationWalError (after the others are
    replayed) so callers block new paid calls until it is recovered."""
    root = Path(project_root).resolve()
    recovered: list[dict] = []
    problems: list[str] = []
    for entry in gates.generation_wal_entries():
        if entry.get("project_root") != str(root) or not entry.get("execution_id"):
            continue
        try:
            recovered.extend(replay_generation_entry(root, entry))
        except GenerationWalError as exc:
            problems.append(str(exc))
    if problems:
        raise GenerationWalError("; ".join(problems), recovered=recovered)
    return recovered

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
from typing import Any, Optional

from lib import gates
from lib.canonical_json import record_sha256 as _record_sha256
from lib.state_io import append_jsonl, read_jsonl

APPROVALS_FILENAME = "approvals.jsonl"
GENERATION_RECEIPTS_FILENAME = "generation-receipts.jsonl"

APPROVAL_KINDS = frozenset(
    {
        "hero", "sheet", "location", "poster", "storyboard_batch", "config", "artifact_review",
        # Plan D10 (look locks) and Slice A' (headshots, reference import, manifest pins).
        "look_lock", "headshot", "reference_import", "pipeline_migration",
    }
)
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
}
REFERENCE_ORIGIN_CLASSES = frozenset({"imported_synthetic", "casting_inspiration"})
ENTITY_KINDS = frozenset({"character", "location"})


class ReceiptError(RuntimeError):
    """A required verified receipt is absent or does not cover the request."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def approvals_path(project_root: Path | str) -> Path:
    return Path(project_root) / APPROVALS_FILENAME


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
) -> dict:
    """Consume ``gate_token`` for ``approval_record`` and append a signed receipt.

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
    gates.wal_write(token_hmac, {
        "token_hmac": token_hmac,
        "project_id": project_id,
        "project_root": str(root.resolve()),
        "receipt": receipt,
    })
    binding = gates.consume_gate_token(gate_token, project_id, stage, scope, digest)
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
    if kind == "reference_import":
        if env["origin_class"] not in REFERENCE_ORIGIN_CLASSES:
            raise ValueError(f"origin_class must be one of {sorted(REFERENCE_ORIGIN_CLASSES)}")
        for field in ("origin_class", "normalized_pixel_hash"):
            if record.get(field) != env[field]:
                raise ValueError(f"reference_import: record.{field} must equal envelope {field}")
        if not isinstance(env["normalized_pixel_hash"], str) or len(env["normalized_pixel_hash"]) != 64:
            raise ValueError("reference_import: normalized_pixel_hash must be a sha256 hex digest")
    return {k: env.get(k) for k in spec}


def _commit_approval(root: Path, binding: gates.GateBinding, receipt: dict) -> None:
    """Idempotent tail of an approval: receipt row, ledger row, WAL removal."""
    rid = receipt["receipt_id"]
    if not any(
        isinstance(r, dict) and r.get("receipt_id") == rid
        for r in read_jsonl(approvals_path(root))
    ):
        append_jsonl(approvals_path(root), receipt)
    if not gates.ledger_has(rid, receipt["record_sha256"], receipt["project_id"], binding.token_hmac):
        gates.ledger_append(binding, rid)
    gates.wal_delete(binding.token_hmac)


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
    expected_project = project_id_for(project_root, project_id)
    match: Optional[dict] = None
    for row in read_jsonl(approvals_path(project_root)):
        if not isinstance(row, dict) or row.get("kind") != kind:
            continue
        if row.get("project_id") != expected_project:
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
    expected_project = project_id_for(project_root, project_id)
    out: list[dict] = []
    for row in read_jsonl(approvals_path(project_root)):
        if not isinstance(row, dict) or row.get("kind") != kind:
            continue
        if row.get("project_id") != expected_project:
            continue
        if entity_id is not None and row.get("entity_id") != entity_id:
            continue
        if not gates.verify_receipt(row):
            continue
        if row.get("record_sha256") != _record_sha256(row.get("record")):
            continue
        out.append(row)
    return out


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
        r for r in read_jsonl(approvals_path(project_root))
        if isinstance(r, dict) and r.get("kind") == "storyboard_batch"
        and r.get("project_id") == expected_project
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


def _commit_generation(root: Path, receipt: dict) -> None:
    """Idempotent tail of a generation: receipt row, then ledger row."""
    rid = receipt["receipt_id"]
    if not any(
        isinstance(r, dict) and r.get("receipt_id") == rid
        for r in read_jsonl(generation_receipts_path(root))
    ):
        append_jsonl(generation_receipts_path(root), receipt)
    if not gates.generation_ledger_has(rid, receipt["output_sha256"], receipt["project_id"]):
        gates.generation_ledger_append(receipt)


def record_generation(project_root: Path | str, **fields: Any) -> dict:
    """Append a signed, ledgered generation receipt. See ``_build_generation_receipt``
    for the fields; ``prompt``, ``seed`` and ``references_applied`` (the ordered
    list of references the tool actually uploaded) are carried in the signed
    record so enforcement can compare the artifact against them."""
    receipt = _build_generation_receipt(project_root, **fields)
    _commit_generation(Path(project_root), receipt)
    return receipt


def verified_generation_receipts(
    project_root: Path | str, *, project_id: Optional[str] = None
) -> list[dict]:
    """Every generation receipt row that passes signature + ledger + project checks."""
    expected_project = project_id_for(project_root, project_id)
    out = []
    for row in read_jsonl(generation_receipts_path(project_root)):
        if not isinstance(row, dict) or row.get("project_id") != expected_project:
            continue
        if gates.verify_generation_receipt(row):
            out.append(row)
    return out


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

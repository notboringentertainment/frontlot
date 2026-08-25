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
    {"hero", "sheet", "location", "poster", "storyboard_batch", "config", "artifact_review"}
)
ARTIFACT_REVIEW_FIELDS = ("artifact_type", "artifact_version", "artifact_digest", "migration_status")
GENERATOR_KINDS = frozenset({"model", "local"})


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
) -> dict:
    """Consume ``gate_token`` for ``approval_record`` and append a signed receipt.

    ``kind == 'artifact_review'`` requires ``artifact`` with
    ``artifact_type, artifact_version, artifact_digest, migration_status``;
    every other kind requires ``entity_id``. The approved record itself is
    carried in the receipt (``record``) so per-item checks such as
    ``require_storyboard_receipt`` can be answered from the receipt alone.
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
) -> dict:
    """Validate the fields and return a signed (not yet persisted) receipt."""
    if generator_kind not in GENERATOR_KINDS:
        raise ValueError(f"generator_kind must be one of {sorted(GENERATOR_KINDS)}")
    if generator_kind == "model" and not model_endpoint:
        raise ValueError("model generations require model_endpoint")
    if generator_kind == "local" and not local_tool:
        raise ValueError("local generations require local_tool")
    if not output_sha256:
        raise ValueError("output_sha256 is required")
    if references_applied is not None:
        references_applied = [normalize_reference(r) for r in references_applied]

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

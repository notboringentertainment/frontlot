"""D19.2/D19.5 — the signed QC stream.

One project-local file, ``qc-receipts.jsonl``, mirrored in the orchestrator
qc-ledger and chained under stream ``qc`` exactly like approvals and
generation receipts (signature + ledger + chain, fail closed on divergence).
Four immutable row kinds (Codex R4#1):

* ``attempt_started``     {attempt_id, series_key, series_sha256, attempt_n, generation_reservation_id}
* ``generation_attached`` {attempt_id, generation_receipt_id, asset_id}
* ``verdict_attached``    {attempt_id, qc_receipt_id, reused}
* ``verdict``             the qc_verdict artifact (schema qc_verdict 1.0) + envelope

Only ``attempt_started`` rows count toward the per-series cap; a verdict is
unique per ``tuple_sha256`` (one evaluation per pixels+look+headshot+policy+
judge). Nothing here mints approvals; a verdict is evidence the gate verifier
(lib.sheet_verify) reads, never an authority on its own.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib import gates
from lib.canonical_json import record_sha256
from lib.receipts import ReceiptChainError, _rows_for_commit, chained_rows, project_id_for, qc_receipts_path
from lib.state_io import append_jsonl

STREAM = "qc"
ROW_KINDS = ("verdict", "attempt_started", "generation_attached", "verdict_attached", "attempt_voided")
SERIES_FIELDS = (
    "project_id", "entity_kind", "entity_id", "role", "look_hash", "headshot_receipt_id",
    "policy_bundle_sha256", "builder_policy_sha256", "generation_endpoint", "generation_model",
    "judge_provider", "judge_model",
)
TUPLE_FIELDS = (
    "project_id", "entity_kind", "entity_id", "role", "asset_id", "look_hash", "look_receipt_id",
    "headshot_asset_id", "headshot_receipt_id", "policy_bundle_sha256", "provider", "model",
)


class QCReceiptError(RuntimeError):
    pass


class AttemptCapExceeded(QCReceiptError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def series_sha256(series_key: dict[str, Any]) -> str:
    missing = [f for f in SERIES_FIELDS if f not in series_key]
    if missing:
        raise QCReceiptError(f"series_key is missing {missing}")
    return record_sha256({f: series_key[f] for f in SERIES_FIELDS})


def tuple_sha256(evaluation: dict[str, Any]) -> str:
    missing = [f for f in TUPLE_FIELDS if f not in evaluation]
    if missing:
        raise QCReceiptError(f"evaluation tuple is missing {missing}")
    return record_sha256({f: evaluation[f] for f in TUPLE_FIELDS})


# ---- reading ----

def verified_qc_rows(project_root: Path | str, *, project_id: Optional[str] = None) -> list[dict]:
    """Every QC row that passes chain + signature + ledger + project checks
    (chain divergence raises ReceiptChainError). Typed and separate from the
    generation index (Codex R1#5)."""
    return [row for row in chained_rows(project_root, STREAM, project_id=project_id) if gates.verify_qc_row(row)]


def rows_of_kind(project_root: Path | str, kind: str, *, project_id: Optional[str] = None) -> list[dict]:
    return [r for r in verified_qc_rows(project_root, project_id=project_id) if r.get("kind") == kind]


def find_verdict(project_root: Path | str, tuple_sha: str) -> Optional[dict]:
    for row in rows_of_kind(project_root, "verdict"):
        if row.get("tuple_sha256") == tuple_sha:
            return row  # unique per tuple; first is the only
    return None


def find_verdict_by_id(project_root: Path | str, receipt_id: str) -> Optional[dict]:
    for row in rows_of_kind(project_root, "verdict"):
        if row.get("receipt_id") == receipt_id:
            return row
    return None


def attempt_rows(project_root: Path | str, attempt_id: str) -> dict[str, Optional[dict]]:
    """``{started, generation, verdict}`` rows for one attempt (None when absent)."""
    out: dict[str, Optional[dict]] = {"started": None, "generation": None, "verdict": None, "voided": None}
    for row in verified_qc_rows(project_root):
        if row.get("attempt_id") != attempt_id:
            continue
        k = row.get("kind")
        if k == "attempt_started" and out["started"] is None:
            out["started"] = row
        elif k == "generation_attached" and out["generation"] is None:
            out["generation"] = row
        elif k == "verdict_attached" and out["verdict"] is None:
            out["verdict"] = row
        elif k == "attempt_voided" and out["voided"] is None:
            out["voided"] = row
    return out


def attempts_started(project_root: Path | str, series_sha: str) -> list[dict]:
    return [r for r in rows_of_kind(project_root, "attempt_started") if r.get("series_sha256") == series_sha]


def open_attempts(project_root: Path | str, series_sha: str) -> list[dict]:
    """attempt_started rows in the series with no verdict_attached yet."""
    closed = {r.get("attempt_id") for r in rows_of_kind(project_root, "verdict_attached")}
    closed |= {r.get("attempt_id") for r in rows_of_kind(project_root, "attempt_voided")}
    return [r for r in attempts_started(project_root, series_sha) if r.get("attempt_id") not in closed]


# ---- writing ----

def _commit(root: Path, row: dict) -> dict:
    pid = row["project_id"]
    with gates.receipt_lock(pid, STREAM):
        _, present = _rows_for_commit(root, STREAM, row)
        if not present:
            append_jsonl(qc_receipts_path(root), row)
        binding = gates.qc_binding(row)
        if not gates.qc_ledger_has(row["receipt_id"], row["kind"], binding, pid):
            gates.qc_ledger_append(row)
        gates.chain_append(pid, STREAM, row)
    return row


def _signed(kind: str, project_id: str, fields: dict[str, Any]) -> dict:
    if kind not in ROW_KINDS:
        raise QCReceiptError(f"unknown QC row kind {kind!r}")
    row = {"receipt_id": str(uuid.uuid4()), "kind": kind, "project_id": project_id, "recorded_at": _now_iso()}
    row.update(fields)
    row["signature"] = gates.sign_receipt(row)
    return row


def start_attempt(
    project_root: Path | str, series_key: dict[str, Any], *, max_attempts: int,
    generation_reservation_id: Optional[str] = None,
) -> dict:
    """Append an immutable ``attempt_started`` row under the QC lock.
    ``attempt_n`` = 1 + the number of attempt_started rows already in the
    series (whatever happened to them). Refuses when the cap is reached.
    Refuses while another attempt in the series is still open (a run must
    finish or void it first) so two runners cannot interleave."""
    root = Path(project_root)
    pid = project_id_for(root)
    key = dict(series_key, project_id=pid)
    sha = series_sha256(key)
    with gates.receipt_lock(pid, STREAM):
        if open_attempts(root, sha):
            raise QCReceiptError(f"series {sha[:12]} has an open attempt; resume or void it before starting another")
        n = len(attempts_started(root, sha)) + 1
        if n > int(max_attempts):
            raise AttemptCapExceeded(
                f"series {sha[:12]} already has {n - 1} attempt(s); cap is {max_attempts} — "
                f"fix the builder (new builder_policy) or approve a qc_override"
            )
        row = _signed("attempt_started", pid, {
            "attempt_id": str(uuid.uuid4()), "series_key": {f: key[f] for f in SERIES_FIELDS},
            "series_sha256": sha, "attempt_n": n, "generation_reservation_id": generation_reservation_id,
        })
        return _commit(root, row)


def attach_generation(project_root: Path | str, attempt_id: str, *, generation_receipt_id: str, asset_id: str) -> dict:
    root = Path(project_root)
    pid = project_id_for(root)
    with gates.receipt_lock(pid, STREAM):
        rows = attempt_rows(root, attempt_id)
        if rows["started"] is None:
            raise QCReceiptError(f"no attempt_started row for {attempt_id}")
        if rows["voided"] is not None:
            raise QCReceiptError(f"attempt {attempt_id} is voided; it is terminal")
        if rows["generation"] is not None:
            if rows["generation"].get("generation_receipt_id") == generation_receipt_id:
                return rows["generation"]
            raise QCReceiptError(f"attempt {attempt_id} already has a generation attached")
        return _commit(root, _signed("generation_attached", pid, {
            "attempt_id": attempt_id, "generation_receipt_id": generation_receipt_id, "asset_id": asset_id}))


def attach_verdict(project_root: Path | str, attempt_id: str, *, qc_receipt_id: str, reused: bool = False) -> dict:
    root = Path(project_root)
    pid = project_id_for(root)
    with gates.receipt_lock(pid, STREAM):
        rows = attempt_rows(root, attempt_id)
        if rows["started"] is None:
            raise QCReceiptError(f"no attempt_started row for {attempt_id}")
        if rows["voided"] is not None:
            raise QCReceiptError(f"attempt {attempt_id} is voided; it is terminal")
        if rows["verdict"] is not None:
            if rows["verdict"].get("qc_receipt_id") == qc_receipt_id:
                return rows["verdict"]
            raise QCReceiptError(f"attempt {attempt_id} already has a verdict attached")
        return _commit(root, _signed("verdict_attached", pid, {
            "attempt_id": attempt_id, "qc_receipt_id": qc_receipt_id, "reused": bool(reused)}))


def void_attempt(project_root: Path | str, attempt_id: str, *, reason: str) -> dict:
    """Immutable close of an attempt whose generation definitively produced
    no asset (reservation failed). It still counts toward the cap."""
    root = Path(project_root)
    pid = project_id_for(root)
    with gates.receipt_lock(pid, STREAM):
        rows = attempt_rows(root, attempt_id)
        if rows["started"] is None:
            raise QCReceiptError(f"no attempt_started row for {attempt_id}")
        if rows["verdict"] is not None or rows["generation"] is not None:
            raise QCReceiptError(f"attempt {attempt_id} has a generation or verdict attached; it cannot be voided")
        if rows["voided"] is not None:
            return rows["voided"]
        return _commit(root, _signed("attempt_voided", pid, {"attempt_id": attempt_id, "reason": str(reason)[:400]}))


def record_verdict(project_root: Path | str, verdict: dict[str, Any]) -> dict:
    """Validate against qc_verdict 1.0, sign, ledger, chain. One verdict per
    ``tuple_sha256``: a second commit for the same tuple returns the existing
    row if identical, else refuses (no newest-wins; Codex R1#8)."""
    from schemas.artifacts import validate_artifact

    root = Path(project_root)
    pid = project_id_for(root)
    v = dict(verdict)
    v.setdefault("version", "1.0")
    v["project_id"] = pid
    if v.get("tuple_sha256") != tuple_sha256(v):
        raise QCReceiptError("verdict.tuple_sha256 does not equal the hash of its own evaluation tuple")
    validate_artifact("qc_verdict", v)
    with gates.receipt_lock(pid, STREAM):
        existing = find_verdict(root, v["tuple_sha256"])
        if existing is not None:
            same = all(existing.get(k) == v.get(k) for k in ("verdict", "items", "attempt_id"))
            if same:
                return existing
            raise QCReceiptError(
                f"tuple {v['tuple_sha256'][:12]} already has verdict {existing['receipt_id']}; "
                f"re-judging needs a new policy bundle or a qc_override"
            )
        row = _signed("verdict", pid, v)
        return _commit(root, row)

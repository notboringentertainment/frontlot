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

D21 (R1#6, R2#4): both hashed shapes are VERSIONED and ENTITY-KIND AWARE.

* a character series hashes ``SERIES_FIELDS`` exactly as D19/D20 signed them —
  no version key is hashed into it, so every character series and hero series
  keeps its bytes (``tests/lib/test_d21_location_series.py`` pins the hashes);
* a LOCATION series (schema ``1.1``) hashes those fields plus its own
  ``series_version``, the signed ``look_receipt_id`` it is bound to, and —
  for an ``angle`` — the ``establishing_asset_id`` of the approved plate it is
  judged beside (R1#8);
* the verdict reuse tuple is versioned the same way: a character tuple is the
  frozen ``TUPLE_FIELDS``; a location tuple adds ``tuple_version`` and, for an
  ``angle``, ``establishing_asset_id``, so an angle judged beside plate A can
  never be reused beside plate B.

``ROLES_BY_ENTITY_KIND`` is the strict matrix: a role belongs to exactly one
entity kind, at both the series and the tuple.
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
# D20: present only on hero attempts that judge a LEGACY (1.3) hero for the
# grandfather attestation; hashed into the series when present.
SERIES_OPTIONAL_FIELDS = ("grandfather",)
HERO_ROLE = "hero"
IMPORTED_SENTINEL = "imported"  # builder policy / endpoint / model of an imported candidate's series
TUPLE_FIELDS = (
    "project_id", "entity_kind", "entity_id", "role", "asset_id", "look_hash", "look_receipt_id",
    "headshot_asset_id", "headshot_receipt_id", "policy_bundle_sha256", "provider", "model",
)

# ---- D21: the versioned, entity-kind-aware schemas ----

CHARACTER_KIND = "character"
LOCATION_KIND = "location"
SHEET_ROLES = ("turnaround", "expressions", "wardrobe")
CHARACTER_ROLES = SHEET_ROLES + (HERO_ROLE,)
ESTABLISHING_ROLE = "establishing"
ANGLE_ROLE = "angle"
LOCATION_ROLES = (ESTABLISHING_ROLE, ANGLE_ROLE)
# The strict matrix. A role belongs to exactly one entity kind; a series or a
# verdict tuple that pairs them differently is refused, never hashed.
ROLES_BY_ENTITY_KIND = {CHARACTER_KIND: CHARACTER_ROLES, LOCATION_KIND: LOCATION_ROLES}

SERIES_VERSION_FIELD = "series_version"
# 1.0 is the D19/D20 shape and is IMPLICIT: it is never hashed, so no character
# or hero series moves a byte. 1.1 is the location shape and hashes its version.
SERIES_VERSION_CHARACTER = "1.0"
SERIES_VERSION_LOCATION = "1.1"
LOCATION_SERIES_FIELDS = SERIES_FIELDS + ("look_receipt_id",)
ANCHOR_FIELD = "establishing_asset_id"

TUPLE_VERSION_FIELD = "tuple_version"
TUPLE_VERSION_CHARACTER = "1.0"   # implicit, never hashed (byte-frozen)
TUPLE_VERSION_LOCATION = "1.1"


class QCReceiptError(RuntimeError):
    pass


class AttemptCapExceeded(QCReceiptError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def kind_and_role(record: dict[str, Any], what: str) -> tuple[str, str]:
    """The (entity_kind, role) of a series key or evaluation tuple, checked
    against the strict matrix. Raises rather than hashing a pair that has no
    meaning (D21 R1#6)."""
    kind, role = record.get("entity_kind"), record.get("role")
    allowed = ROLES_BY_ENTITY_KIND.get(kind) if isinstance(kind, str) else None
    if allowed is None:
        raise QCReceiptError(
            f"{what} entity_kind {kind!r} is not one of {sorted(ROLES_BY_ENTITY_KIND)}")
    if role not in allowed:
        raise QCReceiptError(f"{what} role {role!r} is not a {kind} role; {kind} roles are {list(allowed)}")
    return kind, role


def series_version_for(entity_kind: str, role: str) -> str:
    return SERIES_VERSION_LOCATION if entity_kind == LOCATION_KIND else SERIES_VERSION_CHARACTER


def series_fields_for(entity_kind: str, role: str) -> tuple[str, ...]:
    """The hashed field names of one (entity_kind, role) series schema."""
    if entity_kind != LOCATION_KIND:
        return SERIES_FIELDS
    return LOCATION_SERIES_FIELDS + ((ANCHOR_FIELD,) if role == ANGLE_ROLE else ())


def _check_location_shape(record: dict[str, Any], role: str, what: str) -> None:
    """Value-level rules both location shapes share: no headshot (a place has
    none), and the anchor exactly where it belongs."""
    if record.get("headshot_receipt_id") is not None:
        raise QCReceiptError(f"a location {what} names no headshot: headshot_receipt_id must be present and null")
    if "headshot_asset_id" in record and record.get("headshot_asset_id") is not None:
        raise QCReceiptError(f"a location {what} names no headshot: headshot_asset_id must be null")
    anchor = record.get(ANCHOR_FIELD)
    if role == ANGLE_ROLE:
        if not _is_sha256(anchor):
            raise QCReceiptError(
                f"an angle {what} must bind {ANCHOR_FIELD} (the sha256 of the approved establishing "
                f"plate it is judged beside); got {anchor!r}")
    elif anchor is not None:
        raise QCReceiptError(f"only an angle {what} binds {ANCHOR_FIELD}")


def series_fields(series_key: dict[str, Any]) -> dict[str, Any]:
    """The hashed subset of a series key: every field of the schema its
    (entity_kind, role) selects, plus any SERIES_OPTIONAL_FIELD that is
    present. A location series additionally carries its ``series_version``,
    authored here and never by a caller."""
    kind, role = kind_and_role(series_key, "series_key")
    fields = series_fields_for(kind, role)
    missing = [f for f in fields if f not in series_key]
    if missing:
        raise QCReceiptError(f"series_key is missing {missing}")
    version = series_version_for(kind, role)
    declared = series_key.get(SERIES_VERSION_FIELD)
    if declared is not None and str(declared) != version:
        raise QCReceiptError(
            f"series_key declares {SERIES_VERSION_FIELD} {declared!r}; a {kind} {role} series is {version}")
    out = {f: series_key[f] for f in fields}
    for f in SERIES_OPTIONAL_FIELDS:
        if f in series_key:
            out[f] = series_key[f]
    if kind == LOCATION_KIND:
        _check_location_shape(series_key, role, "series")
        if not str(series_key.get("look_receipt_id") or "").strip():
            raise QCReceiptError("a location series must name the signed look receipt it is bound to")
        out[SERIES_VERSION_FIELD] = version
    return out


def series_sha256(series_key: dict[str, Any]) -> str:
    return record_sha256(series_fields(series_key))


def hero_budget_key(project_id: str, entity_id: str, look_hash: str) -> dict[str, str]:
    """D20 R1#5/#6: the ONE hero allowance is counted per (project, entity,
    look) across every subordinate series (builder/model/judge rotations
    open a new series for reuse semantics but never a new allowance)."""
    return {"project_id": project_id, "entity_id": entity_id, "look_hash": look_hash}


def hero_attempts_started(project_root: Path | str, entity_id: str, look_hash: str, *, project_id: Optional[str] = None) -> list[dict]:
    """Every attempt_started row of role ``hero`` for this entity + look,
    whatever series it belongs to (voided or not: all count)."""
    out = []
    for r in rows_of_kind(project_root, "attempt_started", project_id=project_id):
        key = r.get("series_key") or {}
        if key.get("role") == HERO_ROLE and key.get("entity_id") == entity_id and key.get("look_hash") == look_hash:
            out.append(r)
    return out


def location_budget_key(project_id: str, entity_id: str, look_hash: str) -> dict[str, str]:
    """D21 D2: the ONE location allowance is counted per (project, entity,
    look) across every slot — the establishing plate and every angle — and
    across every series those slots open (a new anchor or a rotated judge opens
    a new series for reuse semantics, never a new allowance)."""
    return {"project_id": project_id, "entity_id": entity_id, "look_hash": look_hash}


def location_attempts_started(
    project_root: Path | str, entity_id: str, look_hash: str, *, project_id: Optional[str] = None
) -> list[dict]:
    """Every attempt_started row of a LOCATION series for this entity + look,
    whatever slot or series it belongs to (voided or not: all count)."""
    out = []
    for r in rows_of_kind(project_root, "attempt_started", project_id=project_id):
        key = r.get("series_key") or {}
        if key.get("entity_kind") == LOCATION_KIND and key.get("role") in LOCATION_ROLES \
                and key.get("entity_id") == entity_id and key.get("look_hash") == look_hash:
            out.append(r)
    return out


def tuple_version_for(entity_kind: str, role: str) -> str:
    return TUPLE_VERSION_LOCATION if entity_kind == LOCATION_KIND else TUPLE_VERSION_CHARACTER


def tuple_fields_for(entity_kind: str, role: str) -> tuple[str, ...]:
    """The hashed field names of one (entity_kind, role) verdict tuple."""
    if entity_kind != LOCATION_KIND:
        return TUPLE_FIELDS
    return TUPLE_FIELDS + ((ANCHOR_FIELD,) if role == ANGLE_ROLE else ())


def tuple_sha256(evaluation: dict[str, Any]) -> str:
    """The reuse identity of one evaluation. A character tuple is exactly the
    D19/D20 bytes; a location tuple hashes its ``tuple_version`` and, for an
    ``angle``, the establishing plate it was judged beside — so the same pixels
    beside a different plate are a different evaluation (R2#4)."""
    kind, role = kind_and_role(evaluation, "evaluation tuple")
    fields = tuple_fields_for(kind, role)
    missing = [f for f in fields if f not in evaluation]
    if missing:
        raise QCReceiptError(f"evaluation tuple is missing {missing}")
    payload = {f: evaluation[f] for f in fields}
    if kind == LOCATION_KIND:
        _check_location_shape(evaluation, role, "verdict")
        payload[TUPLE_VERSION_FIELD] = tuple_version_for(kind, role)
    return record_sha256(payload)


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
    budget_key: Optional[dict[str, str]] = None, budget_cap: Optional[int] = None,
) -> dict:
    """Append an immutable ``attempt_started`` row under the QC lock.
    ``attempt_n`` = 1 + the number of attempt_started rows already in the
    series (whatever happened to them). Refuses when the cap is reached.
    Refuses while another attempt in the series is still open (a run must
    finish or void it first) so two runners cannot interleave.

    D20: a ``hero`` attempt must also name the hero budget (``budget_key`` =
    ``hero_budget_key(...)``, ``budget_cap`` = the signed
    ``qc.max_hero_attempts``); the row records both and is refused when the
    budget count across every series would exceed the cap.

    D21: a LOCATION attempt names the location budget the same way
    (``location_budget_key(...)``, ``budget_cap`` = the signed
    ``qc.max_location_attempts``) — one allowance per entity per look, shared
    by the establishing slot and every angle slot."""
    root = Path(project_root)
    pid = project_id_for(root)
    key = dict(series_key, project_id=pid)
    sha = series_sha256(key)
    is_hero = key.get("role") == HERO_ROLE
    is_location = key.get("entity_kind") == LOCATION_KIND
    if is_hero or is_location:
        label = "hero" if is_hero else "location"
        signed_cap = "qc.max_hero_attempts" if is_hero else "qc.max_location_attempts"
        if budget_key is None or budget_cap is None:
            raise QCReceiptError(f"a {label} attempt must name budget_key and budget_cap (the signed {signed_cap})")
        if is_hero and key.get("headshot_receipt_id") is not None:
            raise QCReceiptError("a hero attempt has no headshot yet: series headshot_receipt_id must be null")
        make_key = hero_budget_key if is_hero else location_budget_key
        expected = make_key(pid, str(key["entity_id"]), str(key["look_hash"]))
        if dict(budget_key) != expected:
            raise QCReceiptError(f"budget_key {budget_key} is not the series' own {expected}")
    elif budget_key is not None or budget_cap is not None:
        raise QCReceiptError("only hero and location attempts carry a budget key")
    with gates.receipt_lock(pid, STREAM):
        if open_attempts(root, sha):
            raise QCReceiptError(f"series {sha[:12]} has an open attempt; resume or void it before starting another")
        n = len(attempts_started(root, sha)) + 1
        if n > int(max_attempts):
            raise AttemptCapExceeded(
                f"series {sha[:12]} already has {n - 1} attempt(s); cap is {max_attempts} — "
                f"fix the builder (new builder_policy) or approve a qc_override"
            )
        fields: dict[str, Any] = {
            "attempt_id": str(uuid.uuid4()), "series_key": series_fields(key),
            "series_sha256": sha, "attempt_n": n, "generation_reservation_id": generation_reservation_id,
        }
        if is_hero or is_location:
            started = hero_attempts_started if is_hero else location_attempts_started
            used = len(started(root, str(key["entity_id"]), str(key["look_hash"])))
            if used + 1 > int(budget_cap):
                raise AttemptCapExceeded(
                    f"{'hero' if is_hero else 'location'} budget for {key['entity_id']!r} under look "
                    f"{str(key['look_hash'])[:12]} is spent ({used} of {budget_cap} attempts across every "
                    f"series); the budget is the limit"
                )
            fields["budget_key"] = dict(budget_key)
            fields["budget_cap"] = int(budget_cap)
            fields["budget_n"] = used + 1
        row = _signed("attempt_started", pid, fields)
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

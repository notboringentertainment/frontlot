"""D21 step 0 — the transaction discipline every runner shares on
``checkpoint_visual_bible.json``.

Sheets (``scripts/sheet_run.py``) and locations (``scripts/location_run.py``)
write entries into the SAME checkpoint, so the rules that keep a signature
meaningful cannot live in either command:

* ONE checkpoint writer, and the order is never reversed — write, take the
  digest of what was written, then publish the request bound to that digest.
* The ``--finish`` sequence is fixed: the bound digest must still be current,
  the receipt must verify with ``exact_approval``, the entry must re-verify,
  and only then does it flip to ``approved`` in a single write.
* ``--abandon`` retires a done request whose checkpoint went stale after
  signing; every step is idempotent, so a crash is completed by re-running.
* A declined request restores the entry the draft replaced and remembers the
  rejected assets, which are never reused.
* Mutual single-flight across BOTH run-state keys (``run_state`` for sheets,
  ``location_run_state`` for locations). Blocking is by OWNER IDENTITY —
  ``(runner_kind, entity_id, run_id, request_id)``: a runner resumes exactly
  its own transaction, and every foreign owner blocks. A blanket "any
  non-empty state blocks" rule would deadlock legitimate resumes.
* The lease comes FIRST. ``lib.run_common.hold_lease`` needs a loaded config,
  but the config is itself an authoritative read that must happen under the
  lease. ``hold_lease`` here takes the lease on a provisional wall time and
  ``retime_lease`` tightens it once the signed config is known.

``scripts/headshot_run.py`` is deliberately NOT on this layer (D8): different
stage, different checkpoint, and no shared state to drift.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from lib.run_common import REQUEST_DIRNAME, RunError, gate_command, read_request, request_paths, write_decision

STAGE = "visual_bible"
PIPELINE_TYPE = "authored-film"
ABANDONED_DIRNAME = "abandoned"
DEFAULT_WALL_TIME_MINUTES = 60.0
# Request kinds that compete for THIS checkpoint. A qc_override is deliberately
# absent: a blocked run publishes one and then resumes past it.
GOVERNED_REQUEST_KINDS = frozenset({"sheet", "location", "reference_import"})
_HEX = frozenset("0123456789abcdef")


class VisualEntryError(RunError):
    """A visual-bible transaction cannot proceed; the message names why."""


class Declined(VisualEntryError):
    """The human declined the request; the draft was withdrawn."""


@dataclass(frozen=True)
class RunnerKind:
    """Everything that differs between the runners sharing this checkpoint."""

    name: str  # also the approval kind and the run state's ``mode``
    module: str  # the command that owns it, for the messages a human acts on
    run_state_key: str  # metadata key holding this runner's transactions
    collection: str  # the visual_bible list its entries live in
    revision_field: str  # the entry field carrying the revision
    prev_entry_key: str  # metadata key: the entry a draft replaced
    rejected_key: str  # metadata key: assets and verdicts the writer rejected
    revisions_key: str  # metadata key: the highest revision issued per entity


SHEET = RunnerKind(
    name="sheet", module="sheet_run", run_state_key="run_state", collection="characters",
    revision_field="sheet_revision", prev_entry_key="sheet_prev_entry",
    rejected_key="rejected_sheets", revisions_key="sheet_revisions",
)
LOCATION = RunnerKind(
    name="location", module="location_run", run_state_key="location_run_state", collection="locations",
    revision_field="location_revision", prev_entry_key="location_prev_entry",
    rejected_key="rejected_locations", revisions_key="location_revisions",
)
RUNNER_KINDS: dict[str, RunnerKind] = {r.name: r for r in (LOCATION, SHEET)}


@dataclass(frozen=True)
class Owner:
    """Who a recorded transaction belongs to (plan R2#8)."""

    runner_kind: str
    entity_id: str
    run_id: str
    request_id: str


@dataclass
class Tx:
    """One runner's transaction on one entity's entry."""

    root: Path
    project_id: str
    runner: RunnerKind
    entity_id: str
    out: Any = None


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def log(tx: Tx, msg: str) -> None:
    import sys

    print(msg, file=tx.out or sys.stdout)


def _is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


# ---- lease first ----

@contextmanager
def hold_lease(root: Path, minutes: float = DEFAULT_WALL_TIME_MINUTES) -> Iterator[Any]:
    """Take the project run lease BEFORE anything authoritative is read.

    The signed config names the wall time, but reading the config is itself an
    authoritative read; the lease is therefore taken on a provisional wall time
    and ``retime_lease`` tightens it once the config is verified.
    """
    from lib import run_lease

    with run_lease.acquire(root, float(minutes)) as lease:
        yield lease


def retime_lease(lease: Any, minutes: float) -> None:
    """Replace the provisional wall time with the one the signed config names."""
    minutes = float(minutes)
    if float(lease.record.get("wall_time_minutes") or 0) == minutes:
        return
    acquired = datetime.fromisoformat(str(lease.record["acquired_at"]))
    lease.record["wall_time_minutes"] = minutes
    lease.record["expires_at"] = (acquired + timedelta(minutes=minutes)).isoformat()
    lease.heartbeat()


# ---- reading the checkpoint ----

def checkpoint(root: Path) -> dict[str, Any]:
    path = root / f"checkpoint_{STAGE}.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VisualEntryError(f"{path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def metadata(root: Path) -> dict[str, Any]:
    return dict(checkpoint(root).get("metadata") or {})


def bible(root: Path) -> Optional[dict[str, Any]]:
    vb = (checkpoint(root).get("artifacts") or {}).get(STAGE)
    return json.loads(json.dumps(vb)) if isinstance(vb, dict) else None


def run_state(root: Path, runner: RunnerKind, entity_id: str) -> Optional[dict[str, Any]]:
    state = (metadata(root).get(runner.run_state_key) or {}).get(entity_id)
    return dict(state) if isinstance(state, dict) else None


def rejected(root: Path, runner: RunnerKind, entity_id: str) -> set[str]:
    return {str(x) for x in (metadata(root).get(runner.rejected_key) or {}).get(entity_id) or []}


def entry_of(vb: Optional[dict], runner: RunnerKind, entity_id: str) -> Optional[dict[str, Any]]:
    for e in (vb or {}).get(runner.collection) or []:
        if isinstance(e, dict) and e.get("id") == entity_id:
            return e
    return None


def with_entry(vb: dict, runner: RunnerKind, entity_id: str, entry: Optional[dict]) -> dict:
    """``vb`` with this entity's entry replaced by ``entry`` (dropped when None)."""
    kept = [e for e in vb.get(runner.collection) or [] if not (isinstance(e, dict) and e.get("id") == entity_id)]
    if entry is not None:
        kept.append(entry)
    vb[runner.collection] = kept
    return vb


def restore_prev(tx: Tx, vb: dict) -> dict:
    """Put back the entry the draft replaced (recorded at publish), or drop the draft."""
    prev = (metadata(tx.root).get(tx.runner.prev_entry_key) or {}).get(tx.entity_id)
    return with_entry(vb, tx.runner, tx.entity_id, dict(prev) if isinstance(prev, dict) else None)


def next_revision(tx: Tx, prev: Optional[dict]) -> int:
    issued = int((metadata(tx.root).get(tx.runner.revisions_key) or {}).get(tx.entity_id, 0))
    return max(issued, int((prev or {}).get(tx.runner.revision_field) or 0)) + 1


# ---- owner identity and single flight ----

def transaction_owners(root: Path) -> list[Owner]:
    """Every transaction recorded on this checkpoint, under either key."""
    meta = metadata(root)
    owners: list[Owner] = []
    for runner in sorted(RUNNER_KINDS.values(), key=lambda r: r.name):
        states = meta.get(runner.run_state_key)
        if not isinstance(states, dict):
            continue
        for entity_id in sorted(states):
            state = states[entity_id] if isinstance(states[entity_id], dict) else {}
            owners.append(Owner(runner.name, str(entity_id), str(state.get("run_id") or ""),
                                str(state.get("request_id") or "")))
    return owners


def foreign_owners(root: Path, runner: RunnerKind, entity_id: str) -> list[Owner]:
    """Transactions this run may not step over: any other runner, any other entity."""
    return [o for o in transaction_owners(root) if (o.runner_kind, o.entity_id) != (runner.name, entity_id)]


def is_governed_request(req: Any) -> bool:
    """True when a request competes for this checkpoint.

    BOTH halves are required: a governed kind AND this stage. Kind alone is not
    enough — a character reference_import is published at the ``look_lock``
    stage (``lib/reference_import.py``) and never touches
    ``checkpoint_visual_bible.json``, so it must not block a sheet. The location
    imports Step 6 adds are published at ``visual_bible``, so they will match.
    """
    return isinstance(req, dict) and req.get("stage") == STAGE and req.get("kind") in GOVERNED_REQUEST_KINDS


def pending_requests(root: Path) -> list[dict[str, Any]]:
    """Governed requests waiting for a human at this stage. Fails closed: an
    unreadable request file could be one that ought to block."""
    found: list[dict[str, Any]] = []
    for path in sorted((root / REQUEST_DIRNAME).glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            req = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VisualEntryError(f"pending request {path} is unreadable: {exc}; it may be one that must block this run — "
                                   f"repair or remove it") from exc
        if is_governed_request(req):
            found.append(req)
    return found


def require_single_flight(root: Path, runner: RunnerKind, entity_id: str) -> None:
    """Refuse to start unless this run owns every governed thing in flight."""
    foreign = foreign_owners(root, runner, entity_id)
    if foreign:
        o = foreign[0]
        other = RUNNER_KINDS[o.runner_kind]
        raise VisualEntryError(f"{o.entity_id!r} has a {o.runner_kind} request in flight; finish or decline {o.entity_id} first "
                               f"({other.module}.py --entity {o.entity_id} --finish)")
    owned = {o.request_id for o in transaction_owners(root) if o.request_id}
    for req in pending_requests(root):
        rid = str(req.get("request_id") or "")
        if rid not in owned:
            raise VisualEntryError(f"request {rid} ({req.get('kind')}) is pending at the {STAGE} gate and no run state owns it; "
                                   f"sign or decline it first")


# ---- the single checkpoint writer ----

def write(tx: Tx, vb: dict[str, Any], *, status: str, run_state: Optional[dict[str, Optional[dict]]] = None,
          prev_entry: Optional[dict[str, Optional[dict]]] = None, clear_prev: Optional[list[str]] = None,
          rejected: Optional[dict[str, list[str]]] = None) -> str:
    """ONE checkpoint write carrying the bible and this runner's state change;
    returns the digest of the file written (what a request binds)."""
    from lib.checkpoint import checkpoint_digest, write_checkpoint

    runner = tx.runner
    meta = metadata(tx.root)
    states = dict(meta.get(runner.run_state_key) or {})
    revs = dict(meta.get(runner.revisions_key) or {})
    for entity, state in (run_state or {}).items():
        if state is None:
            states.pop(entity, None)
        else:
            states[entity] = state
            if state.get("revision") is not None:
                revs[entity] = max(int(revs.get(entity, 0)), int(state["revision"]))
    meta[runner.run_state_key] = states
    meta[runner.revisions_key] = revs
    prevs = dict(meta.get(runner.prev_entry_key) or {})
    for entity, entry in (prev_entry or {}).items():
        prevs[entity] = entry
    for entity in clear_prev or []:
        prevs.pop(entity, None)
    meta[runner.prev_entry_key] = prevs
    if rejected:
        hist = dict(meta.get(runner.rejected_key) or {})
        for entity, ids in rejected.items():
            hist[entity] = sorted(set(hist.get(entity) or []) | {str(i) for i in ids})
        meta[runner.rejected_key] = hist
    path = write_checkpoint(tx.root.parent, tx.root.name, STAGE, status, {STAGE: vb},
                            pipeline_type=PIPELINE_TYPE, human_approval_required=True, metadata=meta)
    return checkpoint_digest(path)


def publish(tx: Tx, vb: dict[str, Any], *, status: str, state: dict[str, Any],
            write_request: Callable[[str], Any], prev_entry: Optional[dict[str, Optional[dict]]] = None) -> str:
    """The order that must never be reversed: checkpoint (entry + run state)
    first, then the request bound to the digest of what was just written."""
    digest = write(tx, vb, status=status, run_state={tx.entity_id: state}, prev_entry=prev_entry)
    write_request(digest)
    return digest


def refresh_canon_view(root: Path) -> None:
    """Best effort: the projection is rebuilt from the checkpoint, never authoritative."""
    try:
        from lib.canon_view import build_view

        build_view(root)
    except Exception:  # noqa: BLE001
        pass


def pending(tx: Tx, request_id: str, headline: str = "") -> dict[str, Any]:
    cmd = gate_command(tx.root, request_id)
    log(tx, (f"{headline}\n" if headline else "") + f"Approve in Terminal (y to sign):\n  {cmd}")
    return {"entity_id": tx.entity_id, "status": "pending", "request_id": request_id, "command": cmd}


# ---- resume: where the gate left the request ----

def resume(tx: Tx, state: dict, *, on_declined: Callable[[dict, dict], dict],
           on_republish: Callable[[dict], dict], on_finish: Callable[[dict, dict], dict]) -> dict[str, Any]:
    """With run state present, every invocation resumes; the request's state
    decides which way. Generation flags are ignored by then."""
    request_id, mode = str(state.get("request_id") or ""), state.get("mode")
    if mode == "abandoning":
        return complete_abandon(tx, state)
    if mode != tx.runner.name or not request_id:
        raise VisualEntryError(f"run state for {tx.entity_id!r} is malformed: {state}")
    where, req = read_request(tx.root, request_id)
    if where == "pending":
        return pending(tx, request_id, f"request {request_id} is still waiting for the writer")
    if where == "declined":
        return on_declined(state, req or {})
    if where == "missing":
        return on_republish(state)
    if req is None or req.get("kind") != state.get("expected_kind"):
        raise VisualEntryError(f"run state names request {request_id} ({state.get('expected_kind')}); the done request has another kind "
                               f"({(req or {}).get('kind')!r}) — refusing to guess")
    return on_finish(state, req)


def decline(tx: Tx, state: dict, req: dict, *, rejections: Callable[[Optional[dict]], list[str]]) -> dict[str, Any]:
    """Restore the entry the draft replaced and remember what the writer rejected."""
    note = str(req.get("declined_note") or "").strip() or "(no note)"
    write_decision(tx.root, stage=STAGE, category="revision",
                   subject=f"{tx.runner.name} {state['request_id']} declined for {tx.entity_id}",
                   reason=note, selected="declined", user_approved=True)
    vb = bible(tx.root) or {}
    rejected_ids = rejections(entry_of(vb, tx.runner, tx.entity_id))
    vb = restore_prev(tx, vb)
    write(tx, vb, status="in_progress", run_state={tx.entity_id: None}, clear_prev=[tx.entity_id],
          rejected={tx.entity_id: rejected_ids})
    log(tx, f"declined: {note}\nThe draft was withdrawn; the rejected views are never reused. Re-run {tx.runner.module} to regenerate "
            f"(revision {int(state.get('revision') or 0) + 1}).")
    raise Declined(note)


def load_draft(tx: Tx, state: dict) -> tuple[dict, dict, dict]:
    """``(checkpoint, bible, draft entry)`` for the revision the run state names."""
    cp = checkpoint(tx.root)
    vb = json.loads(json.dumps((cp.get("artifacts") or {}).get(STAGE) or {}))
    entry = entry_of(vb, tx.runner, tx.entity_id)
    if (entry is None or entry.get("status") != "draft"
            or int(entry.get(tx.runner.revision_field) or 0) != int(state.get("revision") or -1)):
        found = "no entry" if entry is None else f"status {entry.get('status')!r} revision {entry.get(tx.runner.revision_field)}"
        raise VisualEntryError(f"run state names {tx.entity_id!r} draft revision {state.get('revision')} but the checkpoint "
                               f"carries {found}; refusing")
    return cp, vb, entry


def republish(tx: Tx, state: dict, *, verify_entry: Callable[[dict], None],
              write_request: Callable[[dict, dict, str], Any]) -> dict[str, Any]:
    """The checkpoint-before-request crash window: revalidate, then republish
    under the SAME id, bound to a freshly taken digest."""
    request_id = state["request_id"]
    cp, vb, entry = load_draft(tx, state)
    if cp.get("status") != "awaiting_human":
        raise VisualEntryError(f"run state names request {request_id} but the {STAGE} checkpoint is {cp.get('status')!r}, "
                               f"not awaiting_human; refusing")
    verify_entry(entry)
    publish(tx, vb, status="awaiting_human", state=state, write_request=lambda digest: write_request(vb, entry, digest))
    return pending(tx, request_id, f"republished {request_id}")


# ---- finish ----

def bound_digest(req: dict) -> str:
    bound = req.get("source_checkpoint_digest")
    if not _is_sha256_hex(bound):
        raise VisualEntryError(f"done request {req.get('request_id')} carries no 64-hex source_checkpoint_digest; "
                               f"it was not published by a run command — refusing")
    return bound


def require_checkpoint_current(tx: Tx, request_id: str, bound: str) -> dict[str, Any]:
    """Nothing may have rewritten the checkpoint since the request was published."""
    from lib.checkpoint import checkpoint_digest

    cp = checkpoint(tx.root)
    path = tx.root / f"checkpoint_{STAGE}.json"
    actual = checkpoint_digest(path) if path.is_file() else None
    if cp.get("status") != "awaiting_human" or actual != bound:
        raise VisualEntryError(
            f"request {request_id} is signed against checkpoint digest {bound[:12]}… but the {STAGE} checkpoint is now "
            f"{cp.get('status')!r} with digest {(actual or 'none')[:12]}…; it changed after signing. The receipt cannot be applied: "
            f"run {tx.runner.module}.py --project {tx.project_id} --entity {tx.entity_id} --abandon to retire this request, then re-run"
        )
    return cp


def receipt_id_for_done(tx: Tx, req: dict, record_sha: str, bound: str) -> str:
    """The receipt the gate minted for this done request: its enriched
    ``approval_receipt_id``; for a legacy marker without one, the unique
    verified receipt matching the reconstructed tuple (refuse on 0 or >1)."""
    from lib.receipts import verified_approvals

    rid = req.get("approval_receipt_id")
    if rid is not None:
        if not isinstance(rid, str) or not rid:
            raise VisualEntryError(f"done request {req.get('request_id')} carries a malformed approval_receipt_id {rid!r}")
        return rid
    rows = [r for r in verified_approvals(tx.root, tx.runner.name, entity_id=tx.entity_id)
            if r.get("record_sha256") == record_sha and r.get("source_checkpoint_digest") == bound]
    if len(rows) != 1:
        raise VisualEntryError(f"done request {req.get('request_id')} names no approval_receipt_id and {len(rows)} verified "
                               f"{tx.runner.name} receipts match its record + checkpoint digest; refusing to guess")
    return str(rows[0]["receipt_id"])


def finish(tx: Tx, state: dict, req: dict, *, approval_record: Callable[[dict, dict], dict],
           verify_entry: Callable[[dict], None], next_hint: str = "") -> dict[str, Any]:
    """Flip the draft to approved, bound to the exact receipt the gate minted.
    Fail closed on every check; the flip is one write."""
    from lib.canonical_json import record_sha256
    from lib.receipts import ReceiptError, exact_approval

    request_id = state["request_id"]
    bound = bound_digest(req)  # 1
    require_checkpoint_current(tx, request_id, bound)  # 2
    _, vb, entry = load_draft(tx, state)  # 3
    record_sha = record_sha256(approval_record(entry, vb.get("palette") or {}))
    receipt_id = receipt_id_for_done(tx, req, record_sha, bound)
    try:  # 4
        exact_approval(tx.root, receipt_id=receipt_id, kind=tx.runner.name, entity_id=tx.entity_id,
                       record_sha256=record_sha, source_checkpoint_digest=bound)
    except ReceiptError as exc:
        raise VisualEntryError(f"request {request_id} is done but its receipt does not verify: {exc}") from exc
    verify_entry(entry)  # 5
    entry["status"] = "approved"  # 6
    entry["approval_receipt_id"] = receipt_id
    write(tx, with_entry(vb, tx.runner, tx.entity_id, entry), status="in_progress",
          run_state={tx.entity_id: None}, clear_prev=[tx.entity_id])
    refresh_canon_view(tx.root)  # 7
    rev = int(state["revision"])
    log(tx, f"approved {tx.entity_id} rev {rev} receipt {receipt_id}" + (f"\n{next_hint}" if next_hint else ""))  # 8
    return {"entity_id": tx.entity_id, "status": "approved", "revision": rev, "receipt_id": receipt_id}


# ---- abandon: a done request whose checkpoint went stale after signing ----

def abandon(tx: Tx, state: dict, *, approval_record: Callable[[dict, dict], dict]) -> dict[str, Any]:
    from lib.canonical_json import record_sha256
    from lib.checkpoint import checkpoint_digest

    if state.get("mode") == "abandoning":
        return complete_abandon(tx, state)
    request_id = str(state.get("request_id") or "")
    if state.get("mode") != tx.runner.name or not request_id:
        raise VisualEntryError(f"run state for {tx.entity_id!r} is malformed: {state}")
    where, req = read_request(tx.root, request_id)
    if where != "done" or req is None:
        raise VisualEntryError(f"--abandon applies only to a done request whose checkpoint went stale; {request_id} is {where} "
                               f"(a pending request is declined at the gate; a declined or missing one is handled by "
                               f"re-running {tx.runner.module})")
    bound = bound_digest(req)
    cp = checkpoint(tx.root)
    path = tx.root / f"checkpoint_{STAGE}.json"
    if cp.get("status") == "awaiting_human" and path.is_file() and checkpoint_digest(path) == bound:
        raise VisualEntryError(f"request {request_id} is not stale: the checkpoint still matches its digest; run --finish instead")
    # a. reconcile: the current entry must still be exactly the draft this run published
    vb = bible(tx.root) or {}
    entry = entry_of(vb, tx.runner, tx.entity_id)
    expected = record_sha256(req.get("approval_record") or {})
    if (entry is None or entry.get("status") != "draft"
            or int(entry.get(tx.runner.revision_field) or 0) != int(state.get("revision") or -1)
            or record_sha256(approval_record(entry, vb.get("palette") or {})) != expected):
        raise VisualEntryError(f"the {STAGE} entry for {tx.entity_id!r} is no longer the draft request {request_id} published "
                               f"(a newer edit exists); reconcile it by hand — nothing was overwritten and the run state is kept")
    # b. one write: restore the previous entry AND mark the transition
    new_state = {"mode": "abandoning", "request_id": request_id, "receipt_id": req.get("approval_receipt_id"),
                 "revision": state.get("revision"), "run_id": state.get("run_id")}
    write(tx, restore_prev(tx, vb), status="awaiting_human", run_state={tx.entity_id: new_state})
    return complete_abandon(tx, new_state)


def _decision_logged(root: Path, subject: str) -> bool:
    path = root / "decision_log.json"
    if not path.is_file():
        return False
    try:
        log_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(isinstance(d, dict) and d.get("subject") == subject for d in log_data.get("decisions") or [])


def _clear_abandon_state(tx: Tx) -> None:
    """Step e: clear the run state; the checkpoint returns to in_progress."""
    write(tx, bible(tx.root) or {}, status="in_progress", run_state={tx.entity_id: None}, clear_prev=[tx.entity_id])


def complete_abandon(tx: Tx, state: dict) -> dict[str, Any]:
    """Steps c–e, each idempotent: a crash anywhere is completed by re-running."""
    from lib.state_io import atomic_move

    request_id = str(state.get("request_id") or "")
    subject = f"abandon {tx.runner.name} {request_id} for {tx.entity_id}"
    if not _decision_logged(tx.root, subject):  # c
        write_decision(tx.root, stage=STAGE, category="revision", subject=subject, selected="abandoned", user_approved=True,
                       reason=f"stale checkpoint digest; receipt {state.get('receipt_id')} stays in the ledger unused")
    paths = request_paths(tx.root, request_id)  # d
    archive = tx.root / REQUEST_DIRNAME / ABANDONED_DIRNAME / f"{request_id}.json"
    if paths["done"].is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        atomic_move(paths["done"], archive)
    elif not archive.is_file():
        raise VisualEntryError(f"request {request_id} is neither done nor abandoned; refusing to clear its run state")
    _clear_abandon_state(tx)  # e
    log(tx, f"abandoned {request_id} for {tx.entity_id!r}: the draft was withdrawn and the marker archived under "
            f"{REQUEST_DIRNAME}/{ABANDONED_DIRNAME}/. Re-run {tx.runner.module} to publish revision "
            f"{int(state.get('revision') or 0) + 1}.")
    return {"entity_id": tx.entity_id, "status": "abandoned", "request_id": request_id}

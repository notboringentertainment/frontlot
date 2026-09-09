"""sheet_run finish plan (D3, gate side): the gate decides under the approval
receipt lock, binds sheet requests to the visual_bible checkpoint digest,
enriches the pending file with the receipt id before moving it to done/,
recovers a crashed sheet commit from the single matching receipt, and makes
decline crash-safe. Invented names only."""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

from lib import gates
from lib.checkpoint import checkpoint_digest
from lib.state_io import read_jsonl
from scripts import gate_approve
from scripts.gate_approve import GateHandlerError
from tests.lib.look_lock_helpers import CHAR, PROJECT, activate_look, approve_request, character_look, decline_request
from tests.lib.test_d19_sheet_verify import (  # noqa: F401 (fixtures)
    _bible, _entry, _judge, _persist, _request, _sheet_asset, world,
)
from tests.lib.test_gate_approve_selection import (  # noqa: F401 (fixtures)
    _headshot_request, _pending_checkpoint, candidate, human_tty, root,
)
from tests.lib.test_authored_film_contract import gates_dir, project  # noqa: F401 (fixtures)
from tests.lib.test_per_entity_flow import write
from tests.tools.test_sheet_judge import _all

REQ_DIR = ".gate-requests"


def _vb_path(w) -> Path:
    return w["project"] / "checkpoint_visual_bible.json"


def _ready_sheet(w, seed="1"):
    """A passing two-role draft written awaiting_human plus its pending sheet
    request bound to the checkpoint digest. Returns (entry, req)."""
    t, tg = _sheet_asset(w, "turnaround", f"t{seed}")
    e, eg = _sheet_asset(w, "expressions", f"e{seed}")
    qt, _ = _judge(w, "turnaround", t, tg, _all("turnaround"))
    qe, _ = _judge(w, "expressions", e, eg, _all("expressions", head_height="no"))
    entry = _entry(w, {"turnaround": t, "expressions": e}, {"turnaround": qt, "expressions": qe})
    write(w["pipeline"], "visual_bible", {"visual_bible": _bible(entry)}, status="awaiting_human")
    req = _request(entry)
    req["source_checkpoint_digest"] = checkpoint_digest(_vb_path(w))
    return entry, _persist(w["project"], req)


def _rewrite_checkpoint(w, entry):
    """Rewrite the awaiting_human checkpoint (same bible, fresh timestamp → new digest)."""
    before = checkpoint_digest(_vb_path(w))
    cp = json.loads(_vb_path(w).read_text())
    cp["metadata"] = dict(cp.get("metadata") or {}, nonce=os.urandom(4).hex())
    _vb_path(w).write_text(json.dumps(cp, indent=2))
    assert checkpoint_digest(_vb_path(w)) != before


def _states(root: Path, rid: str) -> list[str]:
    base = root / REQ_DIR
    return [s for s, p in (("pending", base / f"{rid}.json"), ("done", base / "done" / f"{rid}.json"),
                           ("declined", base / "declined" / f"{rid}.json")) if p.is_file()]


def _receipts(root: Path, kind: str = "sheet") -> list[dict]:
    """Receipts of ``kind`` (the world fixture already holds config/look/headshot receipts)."""
    return [r for r in read_jsonl(root / "approvals.jsonl") if r.get("kind") == kind]


# ---- digest binding ----

def test_sheet_request_digest_tampering_is_refused_at_the_gate(world):
    w = world
    entry, req = _ready_sheet(w)
    good = req["source_checkpoint_digest"]
    for bad in ("0" * 64, None, "abc"):
        req["source_checkpoint_digest"] = bad
        _persist(w["project"], req)
        with pytest.raises(GateHandlerError, match="source_checkpoint_digest"):
            approve_request(req, w["project"])
    assert not _receipts(w["project"])
    req["source_checkpoint_digest"] = good
    _persist(w["project"], req)
    receipt = approve_request(req, w["project"])
    assert receipt["source_checkpoint_digest"] == good
    done = json.loads((w["project"] / REQ_DIR / "done" / f"{req['request_id']}.json").read_text())
    assert done["approval_receipt_id"] == receipt["receipt_id"]
    assert _states(w["project"], req["request_id"]) == ["done"]


def test_checkpoint_mutated_between_construct_and_commit_is_refused(world, monkeypatch):
    w = world
    entry, req = _ready_sheet(w)
    shown = gate_approve.construct(w["project"], req)
    real = gate_approve.record_human_approval

    def mutate_then_commit(*a, **kw):
        _rewrite_checkpoint(w, entry)  # inside the transaction: after construct, before commit
        return real(*a, **kw)

    monkeypatch.setattr(gate_approve, "record_human_approval", mutate_then_commit)
    with gates.handler_context(), pytest.raises(GateHandlerError, match="changed while approving|digest"):
        gate_approve._decide(req, w["project"], shown=shown, note=None)
    assert not _receipts(w["project"])
    assert _states(w["project"], req["request_id"]) == ["pending"]


def test_headshot_request_digest_must_equal_the_pending_checkpoint(root, human_tty):
    c = character_look()
    activate_look(root, c)
    _pending_checkpoint(root, c, [candidate(root, c, "c0")])
    req = _headshot_request(root)
    req["source_checkpoint_digest"] = "0" * 64
    (root / REQ_DIR / f"{req['request_id']}.json").write_text(json.dumps(req))
    with pytest.raises(GateHandlerError, match="source_checkpoint_digest"):
        approve_request(req, root, selection=1)
    req["source_checkpoint_digest"] = checkpoint_digest(root / "checkpoint_headshots.json")
    (root / REQ_DIR / f"{req['request_id']}.json").write_text(json.dumps(req))
    receipt = approve_request(req, root, selection=1)
    assert receipt["source_checkpoint_digest"] == req["source_checkpoint_digest"]


# ---- reload under the lock ----

def test_in_memory_request_must_match_the_pending_file(world):
    w = world
    entry, req = _ready_sheet(w)
    on_disk = dict(req, scope=f"character:{CHAR}-other")
    _persist(w["project"], on_disk)
    with pytest.raises(GateHandlerError, match="changed on disk"):
        approve_request(req, w["project"])
    assert not _receipts(w["project"])


def test_qc_override_under_the_lock_keeps_the_typed_reason(world):
    w = world
    t, tg = _sheet_asset(w, "turnaround", "t9")
    qt, vt = _judge(w, "turnaround", t, tg, _all("turnaround", shoes_visible="no"))
    assert vt == "fail"
    oreq = {"request_id": "override-lock-1", "project_id": PROJECT, "stage": "visual_bible", "scope": f"character:{CHAR}",
            "kind": "qc_override", "entity_id": CHAR, "summary": "s", "qc_receipt_id": qt, "item_ids": ["shoes_visible"],
            "reason": "REPLACE with your reason"}
    _persist(w["project"], oreq)
    typed = dict(oreq, reason="judge wrong: the shoes are plainly visible")  # what main() overlays at the prompt
    receipt = approve_request(typed, w["project"])
    assert receipt["record"]["reason"] == "judge wrong: the shoes are plainly visible"
    done = json.loads((w["project"] / REQ_DIR / "done" / "override-lock-1.json").read_text())
    assert done["approval_receipt_id"] == receipt["receipt_id"]


# ---- crash windows ----

def test_crash_after_commit_before_rewrite_recovers_the_single_sheet_receipt(world, monkeypatch):
    w = world
    entry, req = _ready_sheet(w)

    def boom(*a, **kw):
        raise OSError("simulated crash before the pending rewrite")

    real_write = gate_approve.atomic_write_json
    monkeypatch.setattr(gate_approve, "atomic_write_json", boom)
    with pytest.raises(OSError):
        approve_request(req, w["project"])
    assert len(_receipts(w["project"])) == 1 and _states(w["project"], req["request_id"]) == ["pending"]
    monkeypatch.setattr(gate_approve, "atomic_write_json", real_write)
    minted = gates.mint_gate_token

    def no_mint(*a, **kw):
        raise AssertionError("recovery must reuse the receipt, not mint")

    monkeypatch.setattr(gates, "mint_gate_token", no_mint)
    receipt = approve_request(req, w["project"])
    assert receipt["receipt_id"] == _receipts(w["project"])[0]["receipt_id"]
    assert len(_receipts(w["project"])) == 1
    assert _states(w["project"], req["request_id"]) == ["done"]
    done = json.loads((w["project"] / REQ_DIR / "done" / f"{req['request_id']}.json").read_text())
    assert done["approval_receipt_id"] == receipt["receipt_id"]
    monkeypatch.setattr(gates, "mint_gate_token", minted)


def test_sheet_recovery_refuses_when_the_checkpoint_moved_on(world, monkeypatch):
    w = world
    entry, req = _ready_sheet(w)
    real_write = gate_approve.atomic_write_json
    monkeypatch.setattr(gate_approve, "atomic_write_json", lambda *a, **k: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        approve_request(req, w["project"])
    monkeypatch.setattr(gate_approve, "atomic_write_json", real_write)
    _rewrite_checkpoint(w, entry)
    with pytest.raises(GateHandlerError, match="source_checkpoint_digest"):
        approve_request(req, w["project"])
    assert len(_receipts(w["project"])) == 1 and _states(w["project"], req["request_id"]) == ["pending"]


def test_enriched_pending_marker_with_wrong_receipt_id_is_refused(world):
    w = world
    entry, req = _ready_sheet(w)
    _persist(w["project"], dict(req, approval_receipt_id="not-a-receipt"))
    with pytest.raises(GateHandlerError, match="approval_receipt_id"):
        approve_request(req, w["project"])
    assert not _receipts(w["project"])
    assert _states(w["project"], req["request_id"]) == ["pending"]


def test_enriched_pending_marker_with_the_right_receipt_completes_the_move(world, monkeypatch):
    w = world
    entry, req = _ready_sheet(w)
    real_move = gate_approve.atomic_move
    monkeypatch.setattr(gate_approve, "atomic_move", lambda *a, **k: (_ for _ in ()).throw(OSError("crash after rewrite")))
    with pytest.raises(OSError):
        approve_request(req, w["project"])
    monkeypatch.setattr(gate_approve, "atomic_move", real_move)
    pending = json.loads((w["project"] / REQ_DIR / f"{req['request_id']}.json").read_text())
    rid = pending["approval_receipt_id"]
    assert rid == _receipts(w["project"])[0]["receipt_id"]
    monkeypatch.setattr(gates, "mint_gate_token", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no mint")))
    receipt = approve_request(req, w["project"])
    assert receipt["receipt_id"] == rid and len(_receipts(w["project"])) == 1
    assert _states(w["project"], req["request_id"]) == ["done"]


def test_non_sheet_kind_with_an_existing_matching_receipt_is_refused_not_reused(root, human_tty):
    c = character_look()
    activate_look(root, c)
    _pending_checkpoint(root, c, [candidate(root, c, "c0")])
    digest = checkpoint_digest(root / "checkpoint_headshots.json")
    req = _headshot_request(root)
    req["source_checkpoint_digest"] = digest
    (root / REQ_DIR / f"{req['request_id']}.json").write_text(json.dumps(req))
    first = approve_request(req, root, selection=1)
    again = dict(req, request_id="headshot-again")
    (root / REQ_DIR / "headshot-again.json").write_text(json.dumps(again))
    with pytest.raises(GateHandlerError, match=first["receipt_id"]):
        approve_request(again, root, selection=1)
    assert [r["receipt_id"] for r in _receipts(root, "headshot")] == [first["receipt_id"]]
    assert _states(root, "headshot-again") == ["pending"]


# ---- decline ----

def test_decline_is_crash_safe_and_a_committed_decline_cannot_be_approved(world, monkeypatch):
    w = world
    entry, req = _ready_sheet(w)
    real_move = gate_approve.atomic_move
    monkeypatch.setattr(gate_approve, "atomic_move", lambda *a, **k: (_ for _ in ()).throw(OSError("crash after rewrite")))
    with pytest.raises(OSError):
        decline_request(req, w["project"], note="wrong jaw")
    monkeypatch.setattr(gate_approve, "atomic_move", real_move)
    assert _states(w["project"], req["request_id"]) == ["pending"]
    pending = json.loads((w["project"] / REQ_DIR / f"{req['request_id']}.json").read_text())
    assert pending["declined_note"] == "wrong jaw"
    # the committed decline is completed before any display or approval
    with pytest.raises(GateHandlerError, match="declined"):
        approve_request(req, w["project"])
    assert not _receipts(w["project"])
    assert _states(w["project"], req["request_id"]) == ["declined"]
    assert json.loads((w["project"] / REQ_DIR / "declined" / f"{req['request_id']}.json").read_text())["declined_note"] == "wrong jaw"


def test_load_request_completes_a_committed_decline(world):
    w = world
    entry, req = _ready_sheet(w)
    path = w["project"] / REQ_DIR / f"{req['request_id']}.json"
    _persist(w["project"], dict(req, declined_note=None))
    with pytest.raises(GateHandlerError, match="declined"):
        gate_approve.load_request(path)
    assert _states(w["project"], req["request_id"]) == ["declined"]


def test_plain_decline_moves_one_file(world):
    w = world
    entry, req = _ready_sheet(w)
    decline_request(req, w["project"], note="no")
    assert _states(w["project"], req["request_id"]) == ["declined"]
    with pytest.raises(GateHandlerError, match="no pending request"):
        approve_request(req, w["project"])


# ---- cross-process races ----

class _Tty:
    def isatty(self):
        return True

    def readline(self):
        return ""


def _child(args):
    action, req, project, pipeline, gates_dir = args
    os.environ["OPENMONTAGE_GATES_DIR"] = gates_dir
    os.environ["OPENMONTAGE_TEST_MODE"] = "1"
    import lib.events as events_mod
    import lib.paths as paths_mod
    events_mod.PROJECTS_DIR = paths_mod.PROJECTS_DIR = Path(pipeline)
    sys.stdin = _Tty()
    try:
        if action == "approve":
            return ("approved", approve_request(req, Path(project))["receipt_id"])
        decline_request(req, Path(project), note="race decline")
        return ("declined", None)
    except GateHandlerError as exc:
        return ("refused", str(exc))


def _race(w, req, actions):
    args = [(a, req, str(w["project"]), str(w["pipeline"]), os.environ["OPENMONTAGE_GATES_DIR"]) for a in actions]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(actions)) as pool:
        return pool.map(_child, args)


def test_two_processes_deciding_one_sheet_request_yield_one_receipt(world):
    w = world
    entry, req = _ready_sheet(w)
    outcomes = _race(w, req, ["approve", "approve"])
    kinds = sorted(k for k, _ in outcomes)
    assert kinds == ["approved", "refused"], outcomes
    assert len(_receipts(w["project"])) == 1
    assert _states(w["project"], req["request_id"]) == ["done"]
    done = json.loads((w["project"] / REQ_DIR / "done" / f"{req['request_id']}.json").read_text())
    assert done["approval_receipt_id"] == _receipts(w["project"])[0]["receipt_id"]


def test_approve_vs_decline_race_ends_in_exactly_one_state(world):
    w = world
    entry, req = _ready_sheet(w)
    outcomes = _race(w, req, ["approve", "decline"])
    states = _states(w["project"], req["request_id"])
    assert len(states) == 1 and states[0] in ("done", "declined"), (outcomes, states)
    winners = [k for k, _ in outcomes if k != "refused"]
    assert len(winners) == 1, outcomes
    assert (states == ["done"]) == (winners == ["approved"])
    assert len(_receipts(w["project"])) == (1 if states == ["done"] else 0)

"""D21 step 0: the shared visual-entry transaction layer. Invented ids only.

Covers what the extraction ADDS on top of the sheet suite (which is the
regression net for the extraction itself): owner identity across both
run-state keys, foreign-owner blocking vs own-owner resume, governed pending
requests, and lease-first ordering.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta

import pytest

from lib import run_lease, visual_entry as ve
from lib.checkpoint import read_checkpoint
from scripts import sheet_run
from tests.lib.look_lock_helpers import CHAR, CHAR2, PROJECT
from tests.lib.test_authored_film_contract import gates_dir, project  # noqa: F401
from tests.lib.test_d19_sheet_verify import world  # noqa: F401
from tests.lib.test_sheet_run import PALETTE, FakeGen, _publish
from tests.tools.test_sheet_judge import FakeAdapter, _all

LOC = "loc-alpha"


def _tx(w, runner, entity_id):
    return ve.Tx(root=w["project"], project_id=PROJECT, runner=runner, entity_id=entity_id, out=io.StringIO())


EMPTY_BIBLE = {
    "version": "1.1", "project_slug": PROJECT, "palette": {"hues": list(PALETTE)},
    "generator_defaults": {"image_model": "bytedance/seedream/v5/pro/text-to-image",
                           "edit_model": "bytedance/seedream/v5/pro/edit"},
    "characters": [], "locations": [],
}


def _seed_location_state(w, entity_id: str, state: dict) -> None:
    """A location transaction on the shared checkpoint, written by the layer."""
    ve.write(_tx(w, ve.LOCATION, entity_id), ve.bible(w["project"]) or dict(EMPTY_BIBLE),
             status="in_progress", run_state={entity_id: state})


# ---- owner identity ----

def test_a_published_transaction_records_its_owner_identity(world):
    w = world
    res, _, _, _ = _publish(w)
    owners = ve.transaction_owners(w["project"])
    assert len(owners) == 1
    owner = owners[0]
    assert owner.runner_kind == "sheet" and owner.entity_id == CHAR and owner.request_id == res["request_id"]
    assert owner.run_id and isinstance(owner.run_id, str)
    # the run id is durable: it is what the checkpoint carries, not a fresh value
    assert read_checkpoint(w["pipeline"], PROJECT, "visual_bible")["metadata"]["run_state"][CHAR]["run_id"] == owner.run_id


def test_own_owner_resumes_while_another_entity_blocks(world):
    w = world
    _publish(w)
    ve.require_single_flight(w["project"], ve.SHEET, CHAR)  # its own transaction is never foreign
    with pytest.raises(ve.VisualEntryError, match=f"finish or decline {CHAR} first"):
        ve.require_single_flight(w["project"], ve.SHEET, CHAR2)


def test_a_location_transaction_blocks_a_sheet_run(world):
    w = world
    _seed_location_state(w, LOC, {"mode": "location", "request_id": f"location-{LOC}-1", "run_id": "abc123", "revision": 1})
    with pytest.raises(sheet_run.SheetRunError, match=f"{LOC!r} has a location request in flight"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, generate=FakeGen(),
                            judge_adapter=FakeAdapter(_all("turnaround")), out=io.StringIO())
    with pytest.raises(ve.VisualEntryError, match=f"location_run.py --entity {LOC} --finish"):
        ve.require_single_flight(w["project"], ve.SHEET, CHAR)


def test_a_sheet_transaction_blocks_a_location_runner(world):
    w = world
    _publish(w)
    with pytest.raises(ve.VisualEntryError, match=f"{CHAR!r} has a sheet request in flight"):
        ve.require_single_flight(w["project"], ve.LOCATION, LOC)


def test_the_same_entity_under_the_other_key_is_still_foreign(world):
    w = world
    _seed_location_state(w, CHAR, {"mode": "location", "request_id": "location-x-1", "run_id": "r1"})
    with pytest.raises(ve.VisualEntryError, match="has a location request in flight"):
        ve.require_single_flight(w["project"], ve.SHEET, CHAR)


# ---- governed pending requests ----

def _write_pending(w, request_id: str, kind: str, entity_id: str, stage: str = "visual_bible") -> None:
    d = w["project"] / ".gate-requests"
    d.mkdir(exist_ok=True)
    (d / f"{request_id}.json").write_text(json.dumps(
        {"request_id": request_id, "project_id": PROJECT, "stage": stage, "kind": kind, "entity_id": entity_id}))


def test_a_pending_governed_request_blocks_a_run_that_does_not_own_it(world):
    w = world
    _write_pending(w, f"location-{LOC}-1", "location", LOC)
    with pytest.raises(ve.VisualEntryError, match=f"location-{LOC}-1 .location. is pending at the visual_bible gate"):
        ve.require_single_flight(w["project"], ve.SHEET, CHAR)


def test_a_pending_override_or_another_stage_never_blocks(world):
    w = world
    _write_pending(w, f"override-{CHAR}-turnaround-1", "qc_override", CHAR)
    _write_pending(w, f"headshot-{CHAR2}-1", "headshot", CHAR2, stage="headshots")
    ve.require_single_flight(w["project"], ve.SHEET, CHAR)


def test_an_import_blocks_only_at_this_stage(world):
    w = world
    # a character reference_import lives at the look_lock stage and never touches
    # checkpoint_visual_bible, so it must not block a sheet
    _write_pending(w, f"import-{CHAR2}-1", "reference_import", CHAR2, stage="look_lock")
    ve.require_single_flight(w["project"], ve.SHEET, CHAR)
    assert ve.pending_requests(w["project"]) == []
    # a location import (Step 6) is published at this stage and does block
    _write_pending(w, f"import-{LOC}-establishing-1", "reference_import", LOC)
    with pytest.raises(ve.VisualEntryError, match=f"import-{LOC}-establishing-1 .reference_import. is pending"):
        ve.require_single_flight(w["project"], ve.SHEET, CHAR)


def test_an_unreadable_pending_request_fails_closed(world):
    w = world
    d = w["project"] / ".gate-requests"
    d.mkdir(exist_ok=True)
    (d / "location-loc-alpha-1.json").write_text("{ this is not json")
    with pytest.raises(ve.VisualEntryError, match="location-loc-alpha-1.json is unreadable"):
        ve.require_single_flight(w["project"], ve.SHEET, CHAR)


def test_a_run_is_not_blocked_by_the_request_its_own_transaction_owns(world):
    w = world
    _publish(w)  # leaves a pending sheet request AND the run state that owns it
    ve.require_single_flight(w["project"], ve.SHEET, CHAR)


# ---- both run-state keys are written independently ----

def test_each_runner_writes_only_its_own_state_key(world):
    w = world
    _publish(w)
    ve.write(_tx(w, ve.LOCATION, LOC), ve.bible(w["project"]) or {}, status="in_progress",
             run_state={LOC: {"mode": "location", "request_id": "location-loc-alpha-1", "run_id": "r9", "revision": 1}})
    meta = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")["metadata"]
    assert set(meta["run_state"]) == {CHAR} and set(meta["location_run_state"]) == {LOC}
    assert meta["location_revisions"] == {LOC: 1} and meta["sheet_revisions"] == {CHAR: 1}


# ---- lease-first ordering ----

def test_the_lease_is_held_before_the_config_is_read(world, monkeypatch):
    w = world
    import lib.project_config as pc

    seen: list[bool] = []
    real = pc.load_verified_project_config

    def spy(root, *a, **kw):
        seen.append(run_lease.lease_path(root).is_file())
        return real(root, *a, **kw)

    monkeypatch.setattr(pc, "load_verified_project_config", spy)
    _publish(w)
    # the FIRST read is the run's own; before the reordering it happened outside the lease
    assert seen and seen[0] is True and all(seen)


def test_a_config_refusal_still_releases_the_lease(world):
    w = world
    with pytest.raises(sheet_run.SheetRunError, match="exceeds the signed cap"):
        sheet_run.run_sheet(w["project"], CHAR, palette=PALETTE, max_attempts=99, generate=FakeGen(),
                            judge_adapter=FakeAdapter(_all("turnaround")), out=io.StringIO())
    assert not run_lease.lease_path(w["project"]).is_file()


def test_a_held_lease_is_a_clean_refusal_not_a_traceback(world, capsys):
    w = world
    with run_lease.acquire(w["project"], 60):
        code = sheet_run.main(["--project", PROJECT, "--entity", CHAR, "--palette", ",".join(PALETTE)])
    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("sheet_run: ") and "run lease" in err and "Traceback" not in err


def test_retime_lease_replaces_the_provisional_wall_time(tmp_path):
    with ve.hold_lease(tmp_path) as lease:
        assert lease.record["wall_time_minutes"] == ve.DEFAULT_WALL_TIME_MINUTES
        ve.retime_lease(lease, 5)
        record = json.loads(run_lease.lease_path(tmp_path).read_text())
        assert record["wall_time_minutes"] == 5.0
        expected = datetime.fromisoformat(record["acquired_at"]) + timedelta(minutes=5)
        assert datetime.fromisoformat(record["expires_at"]) == expected
    assert not run_lease.lease_path(tmp_path).is_file()

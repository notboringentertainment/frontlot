"""G10 (light): scripts/location_run.py — the writer's own pictures of a place,
signed one by one, sealed and signed as a set. Invented names only."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from lib.checkpoint import read_checkpoint
from scripts import gate_approve, location_run, sheet_run
from tests.lib.look_lock_helpers import CHAR, LOC, PROJECT, approve_request, decline_request
from tests.lib.test_authored_film_contract import gates_dir, project  # noqa: F401
from tests.lib.test_d19_sheet_verify import world  # noqa: F401
from tests.lib.test_sheet_run import _publish


def _picture(tmp_path, name, size=(1536, 1024)):
    d = hashlib.sha256(name.encode()).digest()
    path = tmp_path / f"{name}.png"
    Image.new("RGB", size, (d[0], d[1], d[2])).save(path, "PNG")
    return path


def _req(w, request_id):
    return json.loads((w["project"] / ".gate-requests" / f"{request_id}.json").read_text())


def _bible_ready(w):
    """A visual bible exists once a character sheet has been through; locations live beside it."""
    res, req, _, _ = _publish(w)
    approve_request(req, w["project"])
    sheet_run.run_sheet(w["project"], CHAR, palette=["moss"], out=io.StringIO(), finish=True)


def _run(w, **kw):
    return location_run.run_location(w["project"], LOC, out=io.StringIO(), **kw)


def _add_signed(w, tmp_path, name, label=None, size=(1536, 1024)):
    res = _run(w, add_image_path=_picture(tmp_path, name, size), origin_tool="imagetool", label=label)
    approve_request(_req(w, res["request_id"]), w["project"])
    return res


def _location(w):
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    return cp, next(e for e in cp["artifacts"]["visual_bible"]["locations"] if e["id"] == LOC)


def test_add_sign_seal_sign_finish(world, tmp_path):
    w = world
    _bible_ready(w)
    first = _add_signed(w, tmp_path, "front")
    assert first["label"] == "establishing"
    _add_signed(w, tmp_path, "after", label="aftermath")
    sealed = _run(w, do_seal=True)
    cp, entry = _location(w)
    assert cp["status"] == "awaiting_human" and entry["status"] == "draft"
    assert [a["role"] for a in entry["angles"]] == ["aftermath"] and entry["establishing"]["role"] == "establishing"
    assert entry["establishing"]["provenance"]["generator_kind"] == "imported"
    assert (w["project"] / entry["establishing"]["path"]).is_file()
    req = _req(w, sealed["request_id"])
    assert req["kind"] == "location" and req["source_checkpoint_digest"]
    receipt = approve_request(req, w["project"])
    done = _run(w, do_finish=True)
    assert done["status"] == "approved" and done["receipt_id"] == receipt["receipt_id"]
    cp, entry = _location(w)
    assert cp["status"] == "in_progress" and entry["status"] == "approved"
    assert cp["metadata"]["run_state"] == {}
    # the character entry made earlier is untouched
    assert cp["artifacts"]["visual_bible"]["characters"][0]["status"] == "approved"


def test_one_picture_is_enough(world, tmp_path):
    w = world
    _bible_ready(w)
    _add_signed(w, tmp_path, "only")
    approve_request(_req(w, _run(w, do_seal=True)["request_id"]), w["project"])
    _run(w, do_finish=True)
    _, entry = _location(w)
    assert entry["status"] == "approved" and entry["angles"] == []


def test_seal_refuses_unsigned_or_missing_establishing(world, tmp_path):
    w = world
    _bible_ready(w)
    with pytest.raises(location_run.LocationRunError, match="establishing"):
        _run(w, do_seal=True)
    _run(w, add_image_path=_picture(tmp_path, "front"), origin_tool="imagetool")
    with pytest.raises(location_run.LocationRunError, match="still wait"):
        _run(w, do_seal=True)


def test_add_refusals(world, tmp_path):
    w = world
    _bible_ready(w)
    with pytest.raises(location_run.LocationRunError, match="landscape"):
        _run(w, add_image_path=_picture(tmp_path, "tall", (1024, 1536)), origin_tool="imagetool")
    with pytest.raises(location_run.LocationRunError, match="landscape"):
        _run(w, add_image_path=_picture(tmp_path, "small", (800, 600)), origin_tool="imagetool")
    _add_signed(w, tmp_path, "front")
    with pytest.raises(location_run.LocationRunError, match="already has an establishing"):
        _run(w, add_image_path=_picture(tmp_path, "front2"), origin_tool="imagetool", label="establishing")
    _add_signed(w, tmp_path, "a", label="one")
    with pytest.raises(location_run.LocationRunError, match="labelled 'one'"):
        _run(w, add_image_path=_picture(tmp_path, "b"), origin_tool="imagetool", label="one")
    _add_signed(w, tmp_path, "c", label="two")
    _add_signed(w, tmp_path, "d", label="three")
    with pytest.raises(location_run.LocationRunError, match="extra views"):
        _run(w, add_image_path=_picture(tmp_path, "e"), origin_tool="imagetool", label="four")
    with pytest.raises(location_run.LocationRunError, match="lowercase"):
        _run(w, add_image_path=_picture(tmp_path, "f"), origin_tool="imagetool", label="Bad Label")


def test_declined_set_restores_and_reseals(world, tmp_path):
    w = world
    _bible_ready(w)
    _add_signed(w, tmp_path, "front")
    first = _run(w, do_seal=True)
    decline_request(_req(w, first["request_id"]), w["project"], note="wrong time of day")
    with pytest.raises(location_run.Declined):
        _run(w, do_finish=True)
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    assert not cp["artifacts"]["visual_bible"].get("locations") and cp["metadata"]["run_state"] == {}
    _add_signed(w, tmp_path, "after", label="aftermath")
    second = _run(w, do_seal=True)
    assert second["request_id"] != first["request_id"]
    approve_request(_req(w, second["request_id"]), w["project"])
    assert _run(w, do_finish=True)["revision"] == 2


def test_location_and_sheet_requests_never_overlap(world, tmp_path):
    w = world
    _bible_ready(w)
    _add_signed(w, tmp_path, "front")
    _run(w, do_seal=True)
    with pytest.raises(sheet_run.SheetRunError, match=LOC):
        _publish(w)
    approve_request(_req(w, f"location-{LOC}-1"), w["project"])
    _run(w, do_finish=True)
    res, req, _, _ = _publish(w)  # the sheet can go now
    with pytest.raises(location_run.LocationRunError, match="in flight"):
        _run(w, do_seal=True)


def test_gate_refuses_a_set_whose_bible_changed(world, tmp_path):
    w = world
    _bible_ready(w)
    _add_signed(w, tmp_path, "front")
    req = _req(w, _run(w, do_seal=True)["request_id"])
    req["source_checkpoint_digest"] = "0" * 64
    with pytest.raises(gate_approve.GateHandlerError, match="changed since"):
        approve_request(req, w["project"])


def test_needs_a_locked_look_and_a_cast_place(world, tmp_path):
    w = world
    with pytest.raises(location_run.LocationRunError, match="trailer cast"):
        location_run.run_location(w["project"], "not-in-cast", add_image_path=_picture(tmp_path, "x"),
                                  origin_tool="imagetool", out=io.StringIO())


def test_pinned_manifest_is_untouched():
    """Editing the pinned manifest's bytes would force a migration signature (Codex R1#1)."""
    from lib.pipeline_loader import manifest_digest

    assert manifest_digest("authored-film@1.5") == "881e1f023e153331d6ce8aeaabc1510d794524eba3e20316ae59a50ca79b1913"


def test_status_and_board_show_the_place(world, tmp_path):
    from backlot.state import _canon_summary, _gate_rows
    from scripts.frontlot_status import _location_lines

    w = world
    looks = [{"entity_kind": "location", "entity_id": LOC}]
    assert "No location pictures yet" in _location_lines(w["project"], looks, {})[0]
    _bible_ready(w)
    pending = _run(w, add_image_path=_picture(tmp_path, "front"), origin_tool="imagetool")
    assert "waiting for Ben's signature" in _location_lines(w["project"], looks, {})[0]
    row = next(r for r in _gate_rows(w["project"], PROJECT)["requests"] if r["request_id"] == pending["request_id"])
    assert row["bound_entity"] == LOC and row["bound_kind"] == "location"
    approve_request(_req(w, pending["request_id"]), w["project"])
    assert "signed but not sealed" in _location_lines(w["project"], looks, {})[0]
    _add_signed(w, tmp_path, "after", label="aftermath")
    approve_request(_req(w, _run(w, do_seal=True)["request_id"]), w["project"])
    assert "signed by Ben but not finished" in _location_lines(w["project"], looks, {})[0]
    _run(w, do_finish=True)
    assert "Location pictures approved" in _location_lines(w["project"], looks, {})[0]
    rows = [r for r in _canon_summary(w["project"]) if r["entity"] == LOC]
    assert {r["role"] for r in rows} == {"establishing", "angle_1"} and all(r["kind"] == "location" for r in rows)
    assert next(r for r in rows if r["role"] == "angle_1")["label"] == "aftermath"

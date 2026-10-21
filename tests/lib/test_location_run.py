"""G10: scripts/location_run.py — the writer's own pictures of a place, added
freely and signed ONCE as a set (that signature also carries the fixed
no-real-person promise for each picture). Invented names only."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from lib.checkpoint import read_checkpoint
from lib.reference_import import SYNTHETIC_ATTESTATION, verify_lineage
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
    """A visual bible exists once a character sheet has been through; places live beside it."""
    res, req, _, _ = _publish(w)
    approve_request(req, w["project"])
    sheet_run.run_sheet(w["project"], CHAR, palette=["moss"], out=io.StringIO(), finish=True)


def _run(w, **kw):
    return location_run.run_location(w["project"], LOC, out=io.StringIO(), **kw)


def _add(w, tmp_path, name, label=None, size=(1536, 1024)):
    return _run(w, add_image_path=_picture(tmp_path, name, size), origin_tool="imagetool", label=label)


def _location(w):
    cp = read_checkpoint(w["pipeline"], PROJECT, "visual_bible")
    return cp, next((e for e in cp["artifacts"]["visual_bible"].get("locations") or [] if e["id"] == LOC), None)


def _pending_requests(w):
    return sorted(p.name for p in (w["project"] / ".gate-requests").glob("*.json"))


def test_add_then_one_signature_then_finish(world, tmp_path):
    w = world
    _bible_ready(w)
    before = _pending_requests(w)
    assert _add(w, tmp_path, "front")["label"] == "establishing"
    _add(w, tmp_path, "after", label="aftermath")
    assert _pending_requests(w) == before  # adding asks the writer for nothing
    sealed = _run(w, do_seal=True)
    req = _req(w, sealed["request_id"])
    assert req["kind"] == "location" and "no real person" in req["summary"]
    receipt = approve_request(req, w["project"])  # the ONE signature
    assert receipt["attestation_text"] == SYNTHETIC_ATTESTATION
    assert [p["label"] for p in receipt["staged_pictures"]] == ["establishing", "aftermath"]
    done = _run(w, do_finish=True)
    assert done["status"] == "approved" and done["receipt_id"] == receipt["receipt_id"]
    cp, entry = _location(w)
    assert cp["status"] == "in_progress" and entry["status"] == "approved"
    assert entry["establishing"]["role"] == "establishing" and [a["role"] for a in entry["angles"]] == ["aftermath"]
    for ref in [entry["establishing"], *entry["angles"]]:
        assert ref["provenance"]["attestation_receipt_id"] == receipt["receipt_id"]
        assert (w["project"] / ref["path"]).is_file()
        verify_lineage(w["project"], ref["asset_id"])  # the set signature roots each picture's lineage
    assert cp["artifacts"]["visual_bible"]["characters"][0]["status"] == "approved"


def test_one_picture_is_enough(world, tmp_path):
    w = world
    _bible_ready(w)
    _add(w, tmp_path, "only")
    approve_request(_req(w, _run(w, do_seal=True)["request_id"]), w["project"])
    _run(w, do_finish=True)
    _, entry = _location(w)
    assert entry["status"] == "approved" and entry["angles"] == []


def test_seal_needs_an_establishing_picture_and_locks_the_set(world, tmp_path):
    w = world
    _bible_ready(w)
    with pytest.raises(location_run.LocationRunError, match="establishing"):
        _run(w, do_seal=True)
    _add(w, tmp_path, "front")
    _run(w, do_seal=True)
    with pytest.raises(location_run.LocationRunError, match="up for signing"):
        _add(w, tmp_path, "late", label="late")


def test_add_and_remove_rules(world, tmp_path):
    w = world
    _bible_ready(w)
    with pytest.raises(location_run.LocationRunError, match="landscape"):
        _add(w, tmp_path, "tall", size=(1024, 1536))
    with pytest.raises(location_run.LocationRunError, match="landscape"):
        _add(w, tmp_path, "small", size=(800, 600))
    _add(w, tmp_path, "front")
    with pytest.raises(location_run.LocationRunError, match="labelled 'establishing'"):
        _add(w, tmp_path, "front2", label="establishing")
    _add(w, tmp_path, "a", label="one")
    with pytest.raises(location_run.LocationRunError, match="labelled 'one'"):
        _add(w, tmp_path, "b", label="one")
    _add(w, tmp_path, "c", label="two")
    _add(w, tmp_path, "d", label="three")
    with pytest.raises(location_run.LocationRunError, match="extra views"):
        _add(w, tmp_path, "e", label="four")
    with pytest.raises(location_run.LocationRunError, match="lowercase"):
        _add(w, tmp_path, "f", label="Bad Label")
    _run(w, remove_label="three")
    _add(w, tmp_path, "e", label="four")
    with pytest.raises(location_run.LocationRunError, match="no picture labelled"):
        _run(w, remove_label="missing")


def test_declined_set_keeps_pictures_and_reseals(world, tmp_path):
    w = world
    _bible_ready(w)
    _add(w, tmp_path, "front")
    first = _run(w, do_seal=True)
    decline_request(_req(w, first["request_id"]), w["project"], note="wrong time of day")
    with pytest.raises(location_run.Declined):
        _run(w, do_finish=True)
    _, entry = _location(w)
    assert entry is None
    _add(w, tmp_path, "after", label="aftermath")
    second = _run(w, do_seal=True)
    assert second["request_id"] != first["request_id"]
    approve_request(_req(w, second["request_id"]), w["project"])
    assert _run(w, do_finish=True)["revision"] == 2


def test_finish_waits_for_a_pending_sheet(world, tmp_path):
    w = world
    _bible_ready(w)
    _add(w, tmp_path, "front")
    approve_request(_req(w, _run(w, do_seal=True)["request_id"]), w["project"])
    _publish(w)  # a sheet now waits for signature on the same record
    with pytest.raises(location_run.LocationRunError, match="sheet waiting"):
        _run(w, do_finish=True)


def test_gate_refuses_a_changed_or_mislabelled_set(world, tmp_path):
    w = world
    _bible_ready(w)
    _add(w, tmp_path, "front")
    req = _req(w, _run(w, do_seal=True)["request_id"])
    staged = w["project"] / ".staging" / f"reference-{req['envelope']['staged_pictures'][0]['pixel_hash']}.png"
    good = staged.read_bytes()
    staged.write_bytes(_picture(tmp_path, "swap").read_bytes())
    with pytest.raises(gate_approve.GateHandlerError, match="does not hash"):
        approve_request(req, w["project"])
    staged.write_bytes(good)
    bad = json.loads(json.dumps(req))
    bad["envelope"]["staged_pictures"][0]["extra"] = "x"
    with pytest.raises(gate_approve.GateHandlerError, match="exactly"):
        approve_request(bad, w["project"])


def test_promise_must_cover_exactly_the_approved_pictures():
    from lib.receipts import validate_envelope

    record = {"id": "p", "assets": {"establishing": "a" * 64}}
    pic = {"pixel_hash": "a" * 64, "label": "establishing", "origin_tool": "t"}
    validate_envelope("location", record, "d", "p", {"attestation_text": SYNTHETIC_ATTESTATION, "staged_pictures": [pic]})
    with pytest.raises(ValueError, match="fixed promise"):
        validate_envelope("location", record, "d", "p", {"attestation_text": "other", "staged_pictures": [pic]})
    with pytest.raises(ValueError, match="exactly the pictures"):
        validate_envelope("location", record, "d", "p", {"attestation_text": SYNTHETIC_ATTESTATION,
                                                         "staged_pictures": [{**pic, "pixel_hash": "b" * 64}]})
    assert not any(validate_envelope("location", record, "d", "p", None).values())  # a draft-entry location request carries none


def test_needs_a_cast_place(world, tmp_path):
    with pytest.raises(location_run.LocationRunError, match="trailer cast"):
        location_run.run_location(world["project"], "not-in-cast", add_image_path=_picture(tmp_path, "x"),
                                  origin_tool="imagetool", out=io.StringIO())


def test_status_and_board_show_the_place(world, tmp_path):
    from backlot.state import _canon_summary, load_gate_detail
    from scripts.frontlot_status import _location_lines

    w = world
    looks = [{"entity_kind": "location", "entity_id": LOC}]
    line = lambda: _location_lines(w["project"], looks, {})[0]  # noqa: E731
    assert "No location pictures yet" in line()
    _bible_ready(w)
    _add(w, tmp_path, "front")
    assert "added, not yet put up" in line()
    sealed = _run(w, do_seal=True)
    assert "waiting for Ben's one signature" in line()
    packet = load_gate_detail(w["project"], sealed["request_id"])["packet"]
    assert [v["label"] for v in packet["visuals"]] == ["establishing"] and packet["packet_error"] is False
    approve_request(_req(w, sealed["request_id"]), w["project"])
    assert "signed by Ben but not finished" in line()
    _run(w, do_finish=True)
    assert "Location pictures approved" in line()
    rows = [r for r in _canon_summary(w["project"]) if r["entity"] == LOC]
    assert {r["role"] for r in rows} == {"establishing"} and all(r["kind"] == "location" for r in rows)


def test_pinned_manifest_is_untouched():
    """Editing the pinned manifest's bytes would force a migration signature (Codex R1#1)."""
    from lib.pipeline_loader import manifest_digest

    assert manifest_digest("authored-film@1.5") == "881e1f023e153331d6ce8aeaabc1510d794524eba3e20316ae59a50ca79b1913"

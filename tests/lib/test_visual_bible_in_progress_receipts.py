"""sheet_run finish plan D4: an ``in_progress`` (or ``awaiting_human``)
visual_bible is held to the same exact-receipt checks a completed one is —
every approved character, approved location and approved poster must name
the verified receipt of its current record. Invented names only."""
from __future__ import annotations

import pytest

from lib.canon_enforcement import location_approval_record, poster_approval_record
from lib.checkpoint import CheckpointValidationError
from tests.lib.look_lock_helpers import LOC, _approve, image, look_ref, look_refs_for
from tests.lib.test_authored_film_contract import PALETTE, gates_dir, project  # noqa: F401 (fixtures)
from tests.lib.test_d19_sheet_verify import (  # noqa: F401 (fixtures)
    _bible, _entry, _judge, _sheet_asset, world,
)
from tests.lib.test_per_entity_flow import write
from tests.tools.test_sheet_judge import _all


def _approved_character(w):
    t, tg = _sheet_asset(w, "turnaround", "t1"); e, eg = _sheet_asset(w, "expressions", "e1")
    qt, _ = _judge(w, "turnaround", t, tg, _all("turnaround"))
    qe, _ = _judge(w, "expressions", e, eg, _all("expressions", head_height="no"))
    entry = _entry(w, {"turnaround": t, "expressions": e}, {"turnaround": qt, "expressions": qe})
    from lib.canon_enforcement import character_approval_record
    r = _approve(w["project"], "visual_bible", f"sheet:{entry['id']}", character_approval_record(entry, PALETTE), "sheet", entry["id"], None)
    entry["status"] = "approved"; entry["approval_receipt_id"] = r["receipt_id"]
    return entry


def _approved_location(w):
    from lib.look_ingest import active_looks
    active = active_looks(w["project"])
    loc = {"id": LOC, "establishing": image(w["project"], "loc-est", role="establishing", look_refs=look_refs_for(w["l"])),
           "angles": [image(w["project"], f"loc-angle-{i}", role="angle", look_refs=look_refs_for(w["l"])) for i in range(2)], "look_ref": look_ref(w["l"], {"receipt_id": active[("location", LOC)].receipt_id}),
           "sheet_revision": 1, "status": "approved"}
    r = _approve(w["project"], "visual_bible", f"location:{LOC}", location_approval_record(loc, PALETTE), "location", LOC, None)
    loc["approval_receipt_id"] = r["receipt_id"]
    return loc


def _approved_poster(w):
    poster = {"key_art": image(w["project"], "key", role="key_art"), "title_card": image(w["project"], "title", role="title_card"),
              "poster_final": image(w["project"], "final", role="poster_final"), "status": "approved"}
    r = _approve(w["project"], "visual_bible", "poster", poster_approval_record(poster, PALETTE), "poster", "poster", None)
    poster["approval_receipt_id"] = r["receipt_id"]
    return poster


def test_in_progress_bible_accepts_entries_with_their_receipts(world):
    w = world
    bible = _bible(_approved_character(w))
    bible["locations"] = [_approved_location(w)]
    bible["poster"] = _approved_poster(w)
    write(w["pipeline"], "visual_bible", {"visual_bible": bible}, status="in_progress")


def test_in_progress_bible_refuses_an_approved_character_with_a_bogus_receipt_id(world):
    w = world
    ch = _approved_character(w)
    ch["approval_receipt_id"] = "self-attested"
    with pytest.raises(CheckpointValidationError, match="character .* no verified approval receipt"):
        write(w["pipeline"], "visual_bible", {"visual_bible": _bible(ch)}, status="in_progress")


def test_in_progress_bible_refuses_an_approved_location_with_a_bogus_receipt_id(world):
    w = world
    bible = _bible(_approved_character(w))
    loc = _approved_location(w)
    loc["approval_receipt_id"] = "self-attested"
    bible["locations"] = [loc]
    with pytest.raises(CheckpointValidationError, match="location .* no verified approval receipt"):
        write(w["pipeline"], "visual_bible", {"visual_bible": bible}, status="in_progress")


def test_in_progress_bible_refuses_an_approved_poster_with_a_bogus_receipt_id(world):
    w = world
    bible = _bible(_approved_character(w))
    poster = _approved_poster(w)
    poster["approval_receipt_id"] = "self-attested"
    bible["poster"] = poster
    with pytest.raises(CheckpointValidationError, match="poster .* no verified approval receipt"):
        write(w["pipeline"], "visual_bible", {"visual_bible": bible}, status="in_progress")

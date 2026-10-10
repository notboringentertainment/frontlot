"""D18 per-entity flow: looks, headshots and sheets proceed one character at a
time under ``in_progress``/``awaiting_human``; stage completion still needs
every cast entity. Invented names only."""

from __future__ import annotations

import copy
import io
import json
import sys

import pytest

from lib.checkpoint import (
    CheckpointValidationError,
    checkpoint_digest,
    get_completed_stages,
    write_checkpoint,
)
from lib.look_ingest import build_look_packet, ingest_look_tickets
from tests.lib.look_lock_helpers import (
    CHAR,
    CHAR2,
    LOC,
    PROJECT,
    activate_look,
    approve_headshot,
    approve_request,
    character_look,
    image,
    location_look,
    look_packet_for,
    look_refs_for,
    pin_project,
    prompt_recipe,
    write_ticket,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401  (gates_dir autouse)
    PIPELINE,
    authored_script,
    gates_dir,
    plain_decision_log,
    project,
)
from tests.lib.test_authored_film_v12 import bible_v11
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11


def write(pipeline_dir, stage, artifacts, *, status="completed"):
    return write_checkpoint(pipeline_dir, PROJECT, stage, status, artifacts, pipeline_type=PIPELINE, human_approved=True)


@pytest.fixture
def two_cast(project):
    """Pinned 1.2 project whose cast is two characters and one location."""
    pipeline_dir, project_dir = project
    pin_project(project_dir, "1.2")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    write(pipeline_dir, "proposal", {
        "proposal_packet": proposal_v11((CHAR, CHAR2), (LOC,)), "decision_log": plain_decision_log(),
    })
    return pipeline_dir, project_dir


def pending_for(project_dir, c, cid, n=2):
    from lib.look_ingest import active_looks

    rec = active_looks(project_dir)[("character", cid)]
    return {"version": "1.0", "state": "pending", "characters": [{
        "entity_kind": "character", "entity_id": cid,
        "look_ref": {"entity_kind": "character", "entity_id": cid, "look_hash": rec.look_hash, "receipt_id": rec.receipt_id},
        "prompt_recipe": prompt_recipe(c),
        "candidates": [
            image(project_dir, f"{cid}-cand-{i}", look_refs=look_refs_for(c),
                  receipt_prompt_recipe=prompt_recipe(c), prompt=c["prompt_safe_description"])
            for i in range(n)
        ],
    }]}


def approved_entry(pending_entry, chosen, receipt, digest, c):
    return {
        "entity_kind": "character", "entity_id": pending_entry["entity_id"], "look_ref": pending_entry["look_ref"],
        "prompt_recipe": prompt_recipe(c), "hero": chosen, "origin": "generated",
        "normalized_pixel_hash": chosen["asset_id"], "approval_receipt_id": receipt["receipt_id"],
        "candidates_checkpoint_digest": digest,
        "candidates_rejected": [x["asset_id"] for x in pending_entry["candidates"] if x["asset_id"] != chosen["asset_id"]],
    }


def one_character_through_headshot(pipeline_dir, project_dir):
    """CHAR and LOC ratified, CHAR's face approved; CHAR2 has no look at all."""
    c, l = character_look(), location_look()
    activate_look(project_dir, c)
    activate_look(project_dir, l)
    write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)}, status="in_progress")
    pending = pending_for(project_dir, c, CHAR)
    write(pipeline_dir, "headshots", {"headshot_packet": pending}, status="awaiting_human")
    digest = checkpoint_digest(pipeline_dir / PROJECT / "checkpoint_headshots.json")
    chosen = pending["characters"][0]["candidates"][0]
    _, receipt = approve_headshot(project_dir, c, chosen["asset_id"], digest, recipe=prompt_recipe(c))
    approved = {"version": "1.0", "state": "approved",
                "characters": [approved_entry(pending["characters"][0], chosen, receipt, digest, c)]}
    write(pipeline_dir, "headshots", {"headshot_packet": approved}, status="in_progress")
    return c, l, approved, receipt


class TestLookLockPartial:
    def test_partial_packet_is_accepted_in_progress_and_refused_completed(self, two_cast):
        pipeline_dir, project_dir = two_cast
        c = character_look()
        activate_look(project_dir, c)
        partial = look_packet_for(project_dir, c)
        write(pipeline_dir, "look_lock", {"look_packet": partial}, status="in_progress")
        assert "look_lock" not in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)
        with pytest.raises(CheckpointValidationError, match="no entry in look_packet"):
            write(pipeline_dir, "look_lock", {"look_packet": partial})
        # A partial packet that claims completeness is refused even in_progress.
        with pytest.raises(CheckpointValidationError, match="no entry in look_packet"):
            write(pipeline_dir, "look_lock", {"look_packet": dict(partial, complete=True)}, status="in_progress")
        # An unratified entry is still refused in a partial packet.
        forged = copy.deepcopy(partial)
        forged["looks"][0]["receipt_id"] = "forged"
        with pytest.raises(CheckpointValidationError, match="active look is"):
            write(pipeline_dir, "look_lock", {"look_packet": forged}, status="in_progress")
        # Once everyone is ratified the stage completes as before.
        c2, l = character_look(CHAR2, hair="shaved"), location_look()
        activate_look(project_dir, c2)
        activate_look(project_dir, l)
        write(pipeline_dir, "look_lock", {"look_packet": dict(look_packet_for(project_dir, c, c2, l), complete=True)})
        assert "look_lock" in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)

    def test_build_look_packet_marks_complete(self, tmp_path):
        project_dir = tmp_path / "p"
        project_dir.mkdir()
        proposal = proposal_v11((CHAR, CHAR2), (LOC,))
        c = character_look()
        looks = ingest_look_tickets({("character", CHAR): write_ticket(tmp_path, c)}, wayfinder_root=tmp_path)
        activate_look(project_dir, c)
        packet = build_look_packet(project_dir, looks, proposal=proposal)
        assert packet["complete"] is False and [e["entity_id"] for e in packet["looks"]] == [CHAR]
        c2, l = character_look(CHAR2, hair="shaved"), location_look()
        activate_look(project_dir, c2)
        activate_look(project_dir, l)
        looks = ingest_look_tickets({
            ("character", CHAR): write_ticket(tmp_path, c, ticket_id="wf-0badc0de"),
            ("character", CHAR2): write_ticket(tmp_path, c2, ticket_id="wf-0badc0d1"),
            ("location", LOC): write_ticket(tmp_path, l, ticket_id="wf-0badc0d2"),
        }, wayfinder_root=tmp_path)
        assert build_look_packet(project_dir, looks, proposal=proposal)["complete"] is True
        assert "complete" not in build_look_packet(project_dir, looks)


class TestHeadshotsPerCharacter:
    def test_pending_with_one_character_while_other_has_no_look(self, two_cast):
        pipeline_dir, project_dir = two_cast
        c, l, approved, receipt = one_character_through_headshot(pipeline_dir, project_dir)
        cp = json.loads((pipeline_dir / PROJECT / "checkpoint_headshots.json").read_text())
        assert cp["status"] == "in_progress" and cp["artifacts"]["headshot_packet"]["state"] == "approved"
        with pytest.raises(CheckpointValidationError, match="no entry in look_packet"):
            write(pipeline_dir, "headshots", {"headshot_packet": approved})
        # With CHAR2's look ratified the missing face is what blocks completion.
        c2 = character_look(CHAR2, hair="shaved")
        activate_look(project_dir, c2)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, c2, l)})
        with pytest.raises(CheckpointValidationError, match=f"{CHAR2!r} has no approved hero"):
            write(pipeline_dir, "headshots", {"headshot_packet": approved})

    def test_present_character_without_look_is_refused(self, two_cast):
        pipeline_dir, project_dir = two_cast
        c = character_look()
        activate_look(project_dir, c)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c)}, status="in_progress")
        packet = pending_for(project_dir, c, CHAR)
        packet["characters"][0]["entity_id"] = CHAR2
        packet["characters"][0]["look_ref"]["entity_id"] = CHAR2
        with pytest.raises(CheckpointValidationError, match="no entry in look_packet"):
            write(pipeline_dir, "headshots", {"headshot_packet": packet}, status="awaiting_human")

    def test_selection_gate_works_per_character_on_partial_packet(self, two_cast, monkeypatch):
        from lib.headshots import active_headshots, headshot_request

        class _Tty(io.StringIO):
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdin", _Tty())  # the gate is run by a human
        pipeline_dir, project_dir = two_cast
        c = character_look()
        activate_look(project_dir, c)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c)}, status="in_progress")
        pending = pending_for(project_dir, c, CHAR)
        write(pipeline_dir, "headshots", {"headshot_packet": pending}, status="awaiting_human")
        req = json.loads(headshot_request(project_dir, PROJECT, CHAR).read_text())
        receipt = approve_request(req, project_dir, selection=2)
        assert active_headshots(project_dir)[CHAR].receipt_id == receipt["receipt_id"]
        assert active_headshots(project_dir)[CHAR].asset_id == pending["characters"][0]["candidates"][1]["asset_id"]


class TestVisualBiblePerEntity:
    def test_sheet_for_a_with_a_headshot_while_b_has_none(self, two_cast):
        pipeline_dir, project_dir = two_cast
        c, l, approved, hs = one_character_through_headshot(pipeline_dir, project_dir)
        write(pipeline_dir, "visual_bible", {}, status="in_progress")
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        write(pipeline_dir, "visual_bible", {"visual_bible": bible}, status="awaiting_human")
        assert [e["id"] for e in bible["characters"]] == [CHAR] and [e["id"] for e in bible["locations"]] == [LOC]
        with pytest.raises(CheckpointValidationError, match="visual_bible cannot complete"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        assert "visual_bible" not in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)

    def test_sheet_for_character_without_headshot_is_refused(self, two_cast):
        pipeline_dir, project_dir = two_cast
        c, l, approved, hs = one_character_through_headshot(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        c2 = character_look(CHAR2, hair="shaved")
        activate_look(project_dir, c2)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, c2, l)}, status="in_progress")
        bible["characters"].append(dict(bible["characters"][0], id=CHAR2))
        with pytest.raises(CheckpointValidationError, match=f"{CHAR2!r} has no approved current headshot"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible}, status="awaiting_human")

    def test_script_refuses_in_progress_visual_bible(self, two_cast):
        pipeline_dir, project_dir = two_cast
        c, l, approved, hs = one_character_through_headshot(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        write(pipeline_dir, "visual_bible", {"visual_bible": bible}, status="in_progress")
        with pytest.raises(CheckpointValidationError, match="PREREQUISITE VIOLATION"):
            write(pipeline_dir, "script", {"script": authored_script()})

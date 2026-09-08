"""The shot-ready rule end to end (plan 2026-09-08, C1) — T1 to T5.

One real production, built by the real commands (headshot_run, sheet_run) and
real signed receipts, run twice: once pinned to authored-film 1.5 and once to
1.6. The cast is five characters; four are carried all the way to an approved
sheet, the fifth is left unfinished, and there is no poster and no location —
so the visual bible is PARTIAL, exactly as it is while the first shots are
made.

Under 1.5 that partial bible blocks ``script`` (T1). Under 1.6 the manifest
declares it a partial predecessor, so script, scene_plan and assets may
advance (T2) — while every per-entity rule still holds: an unapproved entity
cannot enter a shot (T3), a scene with no refs must declare itself entity-free
(T4), and one shot can never finish the film (T5).

Invented names only.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys

import pytest

from lib.checkpoint import (
    CheckpointValidationError,
    get_completed_stages,
    read_checkpoint,
    write_checkpoint,
)
from scripts import sheet_run
from scripts.headshot_run import run_headshot
from tests.lib.d19_helpers import config_1_2
from tests.lib.look_lock_helpers import (
    CHAR,
    CHAR2,
    LOC,
    PROJECT,
    activate_look,
    approve_request,
    character_look,
    look_packet_for,
    pin_project,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401  (gates_dir autouse, project fixture)
    approval_policy_decision,
    authored_script,
    gates_dir,
    passing_final_review,
    plain_decision_log,
    project,
    render_report,
    write_project_config,
)
from tests.contracts.test_phase0_contracts import sample_artifact
from tests.lib.test_headshot_run import FakeGen as HeroGen, PlanJudge
from tests.lib.test_sheet_run import PALETTE, FakeGen as SheetGen, SeqJudge
from tests.lib.test_visual_bible_contract import (
    _ref,
    approve_storyboards,
    canon_packet_v11,
    fake_image,
    proposal_v11,
    shot_asset,
)
from tests.tools.test_sheet_judge import _all

CHAR3 = "char-03-0badf00d"
CHAR4 = "char-04-badc0ffe"
CHAR5 = "char-05-c0ffee11"
CAST = (CHAR, CHAR2, CHAR3, CHAR4, CHAR5)
FINISHED = CAST[:4]          # four approved sheets
UNFINISHED = CHAR5           # look + hero only: never approved into the bible
VIDEO_ENDPOINT = "fal-ai/kling-video/o3/pro/reference-to-video"
HAIR = ["cropped ash-blond", "shaved", "long copper braid", "close-cropped grey", "loose black curls"]


def write(pipeline_dir, stage, artifacts, *, status="completed", human_approved=True):
    return write_checkpoint(
        pipeline_dir, PROJECT, stage, status, artifacts,
        pipeline_type="authored-film", human_approved=human_approved,
    )


def canon_packet_for(cast) -> dict:
    packet = canon_packet_v11()
    packet["characters"] = [{"id": cid, "name": f"Cast Member {i + 1}"} for i, cid in enumerate(cast)]
    packet["locations"] = [{"id": LOC, "name": "The Salt Station"}]
    return packet


def _gen(factory, index: int):
    """A fake generator whose pixels never collide with another character's —
    provenance is immutable per output hash, so every face and sheet must be
    its own image."""
    generator = factory()
    generator.n = index * 100
    return generator


def _approve_hero(project_dir, entity_id: str, index: int) -> None:
    """generate -> present -> the human selects -> the run finishes."""
    out = io.StringIO()
    result = run_headshot(project_dir, entity_id, candidates=1, generate=_gen(HeroGen, index),
                          judge_adapter=PlanJudge([]), out=out)
    request = json.loads((project_dir / ".gate-requests" / f"{result['request_id']}.json").read_text())
    approve_request(request, project_dir, selection=1)
    run_headshot(project_dir, entity_id, generate=_gen(HeroGen, index), judge_adapter=PlanJudge([]), out=out)


def _approve_sheets(project_dir, entity_id: str, index: int) -> None:
    """generate + judge both sheet roles -> the human signs -> the run finishes."""
    def _judge():
        return SeqJudge({"turnaround": [_all("turnaround")], "expressions": [_all("expressions")]})

    result = sheet_run.run_sheet(project_dir, entity_id, palette=PALETTE, generate=_gen(SheetGen, index),
                                 judge_adapter=_judge(), out=io.StringIO())
    request = json.loads((project_dir / ".gate-requests" / f"{result['request_id']}.json").read_text())
    approve_request(request, project_dir)
    sheet_run.run_sheet(project_dir, entity_id, palette=PALETTE, generate=_gen(SheetGen, index),
                        judge_adapter=_judge(), out=io.StringIO())


def build_world(project, monkeypatch, version: str) -> dict:
    """A pinned project whose visual bible holds four approved characters, no
    location and no poster, with the bible checkpoint still ``in_progress``."""
    pipeline_dir, project_dir = project

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())  # the gates are run by a human
    import lib.events as events_mod
    import lib.paths as paths_mod
    monkeypatch.setattr(events_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")

    config = config_1_2(max_hero_attempts=3)
    config["cast_cap"] = {"characters": len(CAST), "locations": 1}
    digest = write_project_config(project_dir, config)
    pin_project(project_dir, version)
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")

    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_for(CAST)})
    log = plain_decision_log()
    log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11(CAST, ()), "decision_log": log})

    looks = {cid: character_look(cid, hair=HAIR[i]) for i, cid in enumerate(CAST)}
    for look in looks.values():
        activate_look(project_dir, look)
    write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, *looks.values())},
          status="in_progress")
    (project_dir / "canon/visual/objects").mkdir(parents=True, exist_ok=True)

    for index, cid in enumerate(CAST):
        _approve_hero(project_dir, cid, index + 1)
    for index, cid in enumerate(FINISHED):
        _approve_sheets(project_dir, cid, index + 1)

    return {"pipeline": pipeline_dir, "project": project_dir, "version": version, "looks": looks}


@pytest.fixture(params=["1.5", "1.6"])
def world(request, project, monkeypatch):
    """The same production under each pin — the T1/T2 comparison."""
    return build_world(project, monkeypatch, request.param)


@pytest.fixture
def shot_ready(project, monkeypatch):
    """The production a shot is actually made in: pinned 1.6."""
    return build_world(project, monkeypatch, "1.6")


def bible_of(world) -> dict:
    return read_checkpoint(world["pipeline"], PROJECT, "visual_bible")["artifacts"]["visual_bible"]


class TestTheFixtureIsPartial:
    def test_four_approved_characters_no_location_no_poster(self, shot_ready):
        world = shot_ready
        bible = bible_of(world)
        approved = [c["id"] for c in bible["characters"] if c["status"] == "approved"]
        assert approved == list(FINISHED)
        assert UNFINISHED not in [c["id"] for c in bible["characters"]]
        assert not bible.get("locations") and not bible.get("poster")
        checkpoint = read_checkpoint(world["pipeline"], PROJECT, "visual_bible")
        assert checkpoint["status"] == "in_progress"


# ---------------------------------------------------------------------------
# The shot stages, built on the partial bible
# ---------------------------------------------------------------------------

def hero_ref_of(bible: dict, entity_id: str) -> dict:
    entry = next(c for c in bible["characters"] if c["id"] == entity_id)
    return _ref(entry["hero"], entity_id)


def scene_plan_for(character_ids, *, entity_free: bool = False, shot_id: str = "shot-1") -> dict:
    scene = {
        "id": "scene-1",
        "type": "generated",
        "description": "Two crew members meet in the empty corridor.",
        "start_seconds": 0,
        "end_seconds": 60,
        "script_section_id": "s1",
        "canon_refs": ["beat-001", "lock-001"],
        "character_refs": [] if entity_free else list(character_ids),
        "location_ref": None,
        "entity_free": entity_free,
        "model_endpoint": VIDEO_ENDPOINT,
        "shots": [{"shot_id": shot_id, "description": "Corridor, dolly in."}],
    }
    return {"version": "1.1", "scenes": [scene]}


def shot_assets(project_dir, refs, *, approve_board: bool = True):
    """One approved storyboard frame and one take for shot-1.

    The take is a CANDIDATE: under a shot-ready pin nothing is selected until
    the human signs the take gate (C4), and these tests are about the pin and
    the per-entity rules, not about that decision. The decision itself is
    T11 (tests/lib/test_take_receipt.py)."""
    board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs,
                       endpoint=VIDEO_ENDPOINT)
    if approve_board:
        approve_storyboards(project_dir, board)
    take = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs, board=board,
                      endpoint=VIDEO_ENDPOINT, usage_status="candidate")
    return {"version": "1.1", "assets": [board, take]}


def write_script(world, status="completed"):
    return write(world["pipeline"], "script", {"script": authored_script()}, status=status)


def _through_scene_plan(world, character_ids):
    write_script(world)
    write(world["pipeline"], "scene_plan", {"scene_plan": scene_plan_for(character_ids)})


def _draft_sheets(project_dir, entity_id: str, *, index: int) -> None:
    """Generate and judge an entity's sheets but never sign them: the entry
    lands in the bible as a draft."""
    def _judge():
        return SeqJudge({"turnaround": [_all("turnaround")], "expressions": [_all("expressions")]})

    sheet_run.run_sheet(project_dir, entity_id, palette=PALETTE, generate=_gen(SheetGen, index),
                        judge_adapter=_judge(), out=io.StringIO())


class TestT1AndT2ThePinDecides:
    """T1 + T2: the SAME partial bible blocks the shot stages under 1.5 and
    releases them under 1.6. Nothing else about the project differs."""

    def test_a_partial_bible_blocks_script_at_1_5_and_releases_it_at_1_6(self, world):
        if world["version"] == "1.5":
            for status in ("awaiting_human", "completed"):
                with pytest.raises(CheckpointValidationError, match="PREREQUISITE VIOLATION"):
                    write_script(world, status)
            return

        bible = bible_of(world)
        write_script(world, "awaiting_human")
        write_script(world, "completed")
        plan = scene_plan_for(FINISHED[:2])
        write(world["pipeline"], "scene_plan", {"scene_plan": plan}, status="awaiting_human")
        write(world["pipeline"], "scene_plan", {"scene_plan": plan})
        refs = [hero_ref_of(bible, cid) for cid in FINISHED[:2]]
        manifest = shot_assets(world["project"], refs)
        write(world["pipeline"], "assets", {"asset_manifest": manifest}, status="awaiting_human")
        write(world["pipeline"], "assets", {"asset_manifest": manifest})
        completed = get_completed_stages(world["pipeline"], PROJECT, "authored-film")
        assert {"script", "scene_plan", "assets"} <= set(completed)
        assert "visual_bible" not in completed


class TestT3UnapprovedAssetsCannotEnterAShot:
    """The relaxed prerequisite releases the STAGE, never the per-entity rule."""

    def test_a_scene_citing_an_entity_with_no_bible_entry_is_refused(self, shot_ready):
        write_script(shot_ready)
        with pytest.raises(CheckpointValidationError, match=UNFINISHED):
            write(shot_ready["pipeline"], "scene_plan",
                  {"scene_plan": scene_plan_for([FINISHED[0], UNFINISHED])})

    def test_a_scene_citing_a_draft_entry_is_refused(self, shot_ready):
        """The fifth character's sheets are generated and judged but never
        signed, so its entry sits in the bible as a draft."""
        _draft_sheets(shot_ready["project"], UNFINISHED, index=len(CAST))
        entry = next(c for c in bible_of(shot_ready)["characters"] if c["id"] == UNFINISHED)
        assert entry["status"] == "draft"
        write_script(shot_ready)
        with pytest.raises(CheckpointValidationError, match=UNFINISHED):
            write(shot_ready["pipeline"], "scene_plan",
                  {"scene_plan": scene_plan_for([FINISHED[0], UNFINISHED])})

    def test_a_take_referencing_an_image_no_approved_entity_owns_is_refused(self, shot_ready):
        bible = bible_of(shot_ready)
        _through_scene_plan(shot_ready, FINISHED[:2])
        stray = fake_image(shot_ready["project"], "stray-face", role="hero")
        refs = [_ref(stray, FINISHED[0]), hero_ref_of(bible, FINISHED[1])]
        with pytest.raises(CheckpointValidationError, match="not an approved visual_bible ImageRef"):
            write(shot_ready["pipeline"], "assets", {"asset_manifest": shot_assets(shot_ready["project"], refs)})

    def test_a_take_without_a_signed_storyboard_batch_is_refused(self, shot_ready):
        bible = bible_of(shot_ready)
        _through_scene_plan(shot_ready, FINISHED[:2])
        refs = [hero_ref_of(bible, cid) for cid in FINISHED[:2]]
        manifest = shot_assets(shot_ready["project"], refs, approve_board=False)
        with pytest.raises(CheckpointValidationError, match="storyboard"):
            write(shot_ready["pipeline"], "assets", {"asset_manifest": manifest})


class TestT4SettingMustBeDeclared:
    """Proves DECLARATION only: a scene that names nobody must say so. Invented
    surroundings inside a scene that names a character are a D-A / D-B judgement,
    not something code can see."""

    def test_a_scene_with_no_refs_must_declare_entity_free(self, shot_ready):
        write_script(shot_ready)
        silent = scene_plan_for([])
        del silent["scenes"][0]["entity_free"]
        with pytest.raises(CheckpointValidationError, match="entity_free"):
            write(shot_ready["pipeline"], "scene_plan", {"scene_plan": silent})
        denied = scene_plan_for([])
        denied["scenes"][0]["entity_free"] = False
        with pytest.raises(CheckpointValidationError, match="entity_free"):
            write(shot_ready["pipeline"], "scene_plan", {"scene_plan": denied})
        write(shot_ready["pipeline"], "scene_plan", {"scene_plan": scene_plan_for([], entity_free=True)})


class TestT5AShotCannotCompleteTheProject:
    def test_the_bible_still_needs_the_poster_and_the_full_cast(self, shot_ready):
        bible = bible_of(shot_ready)
        with pytest.raises(CheckpointValidationError, match="visual_bible cannot complete"):
            write(shot_ready["pipeline"], "visual_bible", {"visual_bible": bible})

    def test_completing_assets_leaves_the_bible_checkpoint_byte_identical(self, shot_ready):
        bible = bible_of(shot_ready)
        path = shot_ready["pipeline"] / PROJECT / "checkpoint_visual_bible.json"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        _through_scene_plan(shot_ready, FINISHED[:2])
        refs = [hero_ref_of(bible, cid) for cid in FINISHED[:2]]
        write(shot_ready["pipeline"], "assets", {"asset_manifest": shot_assets(shot_ready["project"], refs)})
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert "visual_bible" not in get_completed_stages(shot_ready["pipeline"], PROJECT, "authored-film")

    def test_edit_and_compose_still_require_a_completed_bible(self, shot_ready):
        bible = bible_of(shot_ready)
        _through_scene_plan(shot_ready, FINISHED[:2])
        refs = [hero_ref_of(bible, cid) for cid in FINISHED[:2]]
        write(shot_ready["pipeline"], "assets", {"asset_manifest": shot_assets(shot_ready["project"], refs)})
        edit_decisions = {**sample_artifact("edit_decisions"), "render_runtime": "ffmpeg"}
        for stage, artifacts in (("edit", {"edit_decisions": edit_decisions}),
                                 ("compose", {"render_report": render_report(),
                                              "final_review": passing_final_review(canon_packet_for(CAST))})):
            with pytest.raises(CheckpointValidationError, match="PREREQUISITE VIOLATION"):
                write(shot_ready["pipeline"], stage, artifacts, status="awaiting_human")


class TestT6ExistingApprovalsSurvive:
    """The whole 1.5 approval matrix — ratified looks, approved heroes with
    their hero verdicts, signed sheets — re-verified under each pin. Rewriting
    the bible checkpoint re-runs every receipt check, so this is the proof that
    a 1.6 pin re-reads 1.5 approvals unchanged, not an assertion by
    construction."""

    def test_look_hero_and_sheet_receipts_verify_under_both_pins(self, world):
        from lib.headshots import active_headshots, record_version_of
        from lib.look_ingest import active_looks

        project_dir = world["project"]
        looks = active_looks(project_dir)
        assert sorted(eid for kind, eid in looks) == sorted(CAST)
        heroes = active_headshots(project_dir)
        assert sorted(heroes) == sorted(CAST)
        # 1.2 is the BATCH-pin record version: 1.4 would have written 1.1 here,
        # so this is the 1.5 hero contract reaching 1.6 intact.
        versions = {eid: record_version_of(h.record) for eid, h in heroes.items()}
        assert set(versions.values()) == {"1.2"}, versions
        assert all(h.record.get("qc_receipt_id") for h in heroes.values())

        bible = bible_of(world)
        for entry in bible["characters"]:
            assert entry["approval_receipt_id"]
            assert set(entry["qc_receipts"]) == {"turnaround", "expressions"}
        # The write re-verifies every look_ref, headshot_ref, QC receipt and
        # approval receipt in the bible against this pin.
        write(world["pipeline"], "visual_bible", {"visual_bible": bible}, status="in_progress")
        assert bible_of(world) == bible

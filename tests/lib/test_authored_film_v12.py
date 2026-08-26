"""authored-film 1.2 enforcement: look_lock, headshots, visual_bible 1.1
(plan D10 Slice A step 5–6, Slice A′ steps 2–4, 6). Invented names only."""

from __future__ import annotations

import copy
import json

import pytest

from lib import receipts
from lib.canon_enforcement import character_approval_record, location_approval_record, poster_approval_record
from lib.checkpoint import (
    CheckpointValidationError,
    checkpoint_digest,
    get_completed_stages,
    get_latest_checkpoint,
    get_next_stage,
    write_checkpoint,
)
from lib.look_spec import look_hash
from tests.lib.look_lock_helpers import (
    CHAR,
    LOC,
    PROJECT,
    activate_look,
    approve_headshot,
    character_look,
    image,
    location_look,
    look_packet_for,
    look_ref,
    look_refs_for,
    pin_project,
    prompt_recipe,
    retire_headshot,
    retire_look,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401  (gates_dir autouse)
    GENERATOR_DEFAULTS,
    PALETTE,
    PIPELINE,
    approve,
    authored_script,
    gates_dir,
    plain_decision_log,
    project,
    write_project_config,
)
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11

PIPELINE_DIR = None


def write(pipeline_dir, stage, artifacts, *, status="completed"):
    return write_checkpoint(pipeline_dir, PROJECT, stage, status, artifacts, pipeline_type=PIPELINE, human_approved=True)


@pytest.fixture
def v12(project):
    pipeline_dir, project_dir = project
    pin_project(project_dir, "1.2")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11((CHAR,), (LOC,)), "decision_log": plain_decision_log()})
    return pipeline_dir, project_dir


def ratified(project_dir):
    c, l = character_look(), location_look()
    activate_look(project_dir, c)
    activate_look(project_dir, l)
    return c, l


def pending_packet(project_dir, c, n=2):
    from lib.look_ingest import active_looks

    rec = active_looks(project_dir)[("character", CHAR)]
    return {"version": "1.0", "state": "pending", "characters": [{
        "entity_kind": "character", "entity_id": CHAR,
        "look_ref": {"entity_kind": "character", "entity_id": CHAR, "look_hash": rec.look_hash, "receipt_id": rec.receipt_id},
        "prompt_recipe": prompt_recipe(c),
        "candidates": [image(project_dir, f"cand-{i}", look_refs=look_refs_for(c)) for i in range(n)],
    }]}


def through_headshots(pipeline_dir, project_dir):
    c, l = ratified(project_dir)
    write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
    pending = pending_packet(project_dir, c)
    write(pipeline_dir, "headshots", {"headshot_packet": pending}, status="awaiting_human")
    digest = checkpoint_digest(pipeline_dir / PROJECT / "checkpoint_headshots.json")
    chosen = pending["characters"][0]["candidates"][0]
    rejected = pending["characters"][0]["candidates"][1]
    record, receipt = approve_headshot(project_dir, c, chosen["asset_id"], digest, recipe=prompt_recipe(c))
    approved = {"version": "1.0", "state": "approved", "characters": [{
        "entity_kind": "character", "entity_id": CHAR, "look_ref": pending["characters"][0]["look_ref"],
        "prompt_recipe": prompt_recipe(c), "hero": chosen, "origin": "generated",
        "normalized_pixel_hash": chosen["asset_id"], "approval_receipt_id": receipt["receipt_id"],
        "candidates_checkpoint_digest": digest, "candidates_rejected": [rejected["asset_id"]],
    }]}
    write(pipeline_dir, "headshots", {"headshot_packet": approved})
    return c, l, approved, receipt, rejected


def bible_v11(project_dir, c, l, hero, hs_receipt, *, headshot_ref=None, sheet_ref_override=None):
    from lib.look_ingest import active_looks

    active = active_looks(project_dir)
    href = headshot_ref or {"entity_id": CHAR, "asset_id": hero["asset_id"], "approval_receipt_id": hs_receipt["receipt_id"]}
    sheet = {
        role: image(project_dir, f"{CHAR}-{role}", role=role, look_refs=look_refs_for(c),
                    headshot_ref=sheet_ref_override if sheet_ref_override is not None else href,
                    references_applied=[{"asset_id": hero["asset_id"], "path": hero["path"], "role": "hero"}])
        for role in ("front", "three_quarter", "profile", "full_body", "expressions", "wardrobe")
    }
    ch = {"id": CHAR, "hero": hero, "sheet": sheet, "wardrobe_negative": "no hat",
          "prompt_recipe": prompt_recipe(c), "look_ref": look_ref(c, {"receipt_id": active[("character", CHAR)].receipt_id}),
          "sheet_revision": 1, "status": "approved"}
    r = approve(project_dir, "visual_bible", f"sheet:{CHAR}", character_approval_record(ch, PALETTE), "sheet", CHAR)
    ch["approval_receipt_id"] = r["receipt_id"]
    loc = {"id": LOC, "establishing": image(project_dir, "loc-est", role="establishing", look_refs=look_refs_for(l)),
           "angles": [image(project_dir, f"loc-angle-{i}", role="angle", look_refs=look_refs_for(l)) for i in range(2)],
           "look_ref": look_ref(l, {"receipt_id": active[("location", LOC)].receipt_id}), "sheet_revision": 1, "status": "approved"}
    r = approve(project_dir, "visual_bible", f"location:{LOC}", location_approval_record(loc, PALETTE), "location", LOC)
    loc["approval_receipt_id"] = r["receipt_id"]
    poster = {"key_art": image(project_dir, "key", role="key_art"), "title_card": image(project_dir, "title", role="title_card"),
              "poster_final": image(project_dir, "final", role="poster_final"), "status": "approved"}
    r = approve(project_dir, "visual_bible", "poster", poster_approval_record(poster, PALETTE), "poster", "poster")
    poster["approval_receipt_id"] = r["receipt_id"]
    return {"version": "1.1", "project_slug": "test-feature", "palette": PALETTE, "generator_defaults": GENERATOR_DEFAULTS,
            "characters": [ch], "locations": [loc], "poster": poster}


# ---------------------------------------------------------------------------

class TestPinAndTuple:
    def test_unpinned_project_stays_on_1_1_and_has_no_look_lock(self, project):
        pipeline_dir, _ = project
        write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
        cp = json.loads((pipeline_dir / PROJECT / "checkpoint_canon_ingest.json").read_text())
        assert cp["pipeline"] == {"name": "authored-film", "version": "1.1", "manifest_digest": cp["pipeline"]["manifest_digest"]}
        assert cp["predecessors"] == []
        with pytest.raises(ValueError, match="Invalid stage"):
            write(pipeline_dir, "look_lock", {"look_packet": {"version": "1.0", "looks": []}})

    def test_pinned_checkpoint_binds_tuple_and_predecessors(self, v12):
        pipeline_dir, _ = v12
        cp = json.loads((pipeline_dir / PROJECT / "checkpoint_proposal.json").read_text())
        assert cp["pipeline"]["version"] == "1.2"
        assert [p["stage"] for p in cp["predecessors"]] == ["canon_ingest"]
        assert cp["predecessors"][0]["checkpoint_digest"] == checkpoint_digest(pipeline_dir / PROJECT / "checkpoint_canon_ingest.json")
        assert get_next_stage(pipeline_dir, PROJECT, PIPELINE) == "look_lock"

    def test_explicit_version_disagreeing_with_pin_is_refused(self, v12):
        pipeline_dir, _ = v12
        with pytest.raises(CheckpointValidationError, match="PIPELINE PIN"):
            write_checkpoint(pipeline_dir, PROJECT, "look_lock", "in_progress", {}, pipeline_type="authored-film@1.1")


class TestLookLockStage:
    def test_completes_with_active_receipts(self, v12):
        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
        assert "look_lock" in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)

    def test_missing_cast_entity_is_rejected(self, v12):
        pipeline_dir, project_dir = v12
        c = character_look()
        activate_look(project_dir, c)
        with pytest.raises(CheckpointValidationError, match="no entry in look_packet"):
            write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c)})

    def test_packet_without_receipt_is_rejected(self, v12):
        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        packet = look_packet_for(project_dir, c, l)
        packet["looks"][0]["receipt_id"] = "forged"
        with pytest.raises(CheckpointValidationError, match="active look is"):
            write(pipeline_dir, "look_lock", {"look_packet": packet})

    def test_shape_only_minor_and_spoiler_are_refused(self, v12):
        pipeline_dir, project_dir = v12
        l = location_look()
        activate_look(project_dir, l)
        for overrides, msg in (
            ({"shape_only": True}, "shape_only"),
            ({"minor": True}, "minors"),
            ({"spoiler": True}, "spoiler"),
        ):
            c = character_look(**overrides)
            activate_look(project_dir, c)
            with pytest.raises(CheckpointValidationError, match=msg):
                write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
            retire_look(project_dir, c)

    def test_superseded_look_invalidates_packet_hash(self, v12):
        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        packet = look_packet_for(project_dir, c, l)
        c2 = character_look(hair="shaved")
        activate_look(project_dir, c2, supersedes=look_hash(c))
        with pytest.raises(CheckpointValidationError, match="active look is"):
            write(pipeline_dir, "look_lock", {"look_packet": packet})


class TestHeadshotsStage:
    def test_pending_then_approved_completes(self, v12):
        pipeline_dir, project_dir = v12
        through_headshots(pipeline_dir, project_dir)
        assert get_completed_stages(pipeline_dir, PROJECT, PIPELINE)[-1] == "headshots"

    def test_cannot_complete_while_pending(self, v12):
        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
        with pytest.raises(CheckpointValidationError, match="pending headshot_packet"):
            write(pipeline_dir, "headshots", {"headshot_packet": pending_packet(project_dir, c)})

    def test_candidate_generated_without_look_refs_is_rejected(self, v12):
        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
        packet = pending_packet(project_dir, c)
        packet["characters"][0]["candidates"][0] = image(project_dir, "entity-free")
        with pytest.raises(CheckpointValidationError, match="never entity-free"):
            write(pipeline_dir, "headshots", {"headshot_packet": packet}, status="awaiting_human")

    def test_approved_packet_must_name_active_headshot_tip(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, receipt, rejected = through_headshots(pipeline_dir, project_dir)
        forged = copy.deepcopy(approved)
        forged["characters"][0]["hero"] = rejected
        forged["characters"][0]["normalized_pixel_hash"] = rejected["asset_id"]
        with pytest.raises(CheckpointValidationError, match="active headshot is"):
            write(pipeline_dir, "headshots", {"headshot_packet": forged})

    def test_headshot_before_sheet_is_enforced_at_visual_bible_entry(self, v12):
        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
        with pytest.raises(CheckpointValidationError, match="no approved headshot_packet"):
            write(pipeline_dir, "visual_bible", {}, status="in_progress")


class TestVisualBibleV12:
    def test_happy_path_completes_and_continues(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        write(pipeline_dir, "visual_bible", {}, status="in_progress")
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        write(pipeline_dir, "script", {"script": authored_script()})
        assert "script" in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)

    def test_sheet_without_headshot_ref_is_rejected(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs, sheet_ref_override={})
        with pytest.raises(CheckpointValidationError, match="does not name the approved hero"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def _sheet_with_receipt_recipe(self, project_dir, c, hero, hs, *, rendered_of: str, look_hash_override=None):
        import hashlib

        href = {"entity_id": CHAR, "asset_id": hero["asset_id"], "approval_receipt_id": hs["receipt_id"]}
        recipe = {"look_hash": look_hash_override or look_hash(c), "builder_version": "1.0",
                  "fields_used": ["prompt_safe_description"],
                  "rendered_sha256": hashlib.sha256(rendered_of.encode()).hexdigest()}
        return image(project_dir, f"{CHAR}-front-recipe", role="front", look_refs=look_refs_for(c), headshot_ref=href,
                     references_applied=[{"asset_id": hero["asset_id"], "path": hero["path"], "role": "hero"}],
                     receipt_prompt_recipe=recipe)

    @staticmethod
    def _swap_front(project_dir, bible, ref):
        """Replace the front sheet and re-approve the character record (the sheet digest changed)."""
        ch = bible["characters"][0]
        ch["sheet"]["front"] = ref
        ch.pop("approval_receipt_id", None)
        r = approve(project_dir, "visual_bible", f"sheet:{CHAR}", character_approval_record(ch, PALETTE), "sheet", CHAR)
        ch["approval_receipt_id"] = r["receipt_id"]

    def test_sheet_receipt_prompt_recipe_must_hash_the_prompt(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        hero = approved["characters"][0]["hero"]
        bible = bible_v11(project_dir, c, l, hero, hs)
        # image() prompts are "<role> of <seed>"; a recipe sealing that rendering passes...
        self._swap_front(project_dir, bible, self._sheet_with_receipt_recipe(
            project_dir, c, hero, hs, rendered_of=f"front of {CHAR}-front-recipe"))
        write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        # ...and one sealing a different rendering (an edited prompt) is refused.
        self._swap_front(project_dir, bible, self._sheet_with_receipt_recipe(
            project_dir, c, hero, hs, rendered_of="hand-edited prompt text"))
        with pytest.raises(CheckpointValidationError, match="prompt_recipe.rendered_sha256"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_sheet_receipt_prompt_recipe_must_name_active_look(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        hero = approved["characters"][0]["hero"]
        bible = bible_v11(project_dir, c, l, hero, hs)
        self._swap_front(project_dir, bible, self._sheet_with_receipt_recipe(
            project_dir, c, hero, hs, rendered_of=f"front of {CHAR}-front-recipe", look_hash_override="e" * 64))
        with pytest.raises(CheckpointValidationError, match="prompt_recipe.look_hash"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_rejected_candidate_cannot_be_headshot_ref(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, rejected = through_headshots(pipeline_dir, project_dir)
        bad = {"entity_id": CHAR, "asset_id": rejected["asset_id"], "approval_receipt_id": hs["receipt_id"]}
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs, headshot_ref=bad)
        with pytest.raises(CheckpointValidationError, match="does not name the approved hero"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_hero_must_be_the_approved_headshot(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, rejected = through_headshots(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, rejected, hs)
        with pytest.raises(CheckpointValidationError, match="not the approved headshot"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_1_0_bible_is_rejected_under_1_2(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        bible["version"] = "1.0"
        for ch in bible["characters"]:
            ch["approved_prompt_block"] = "x"
            for k in ("prompt_recipe", "look_ref", "sheet_revision"):
                ch.pop(k)
        for loc in bible["locations"]:
            for k in ("look_ref", "sheet_revision"):
                loc.pop(k)
        with pytest.raises(CheckpointValidationError):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_stale_look_ref_after_supersession_is_rejected(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        activate_look(project_dir, location_look(dressing=["nothing"]), supersedes=look_hash(l))
        # conservative invalidation catches it first: headshots is stale too
        with pytest.raises(CheckpointValidationError, match="invalidated by"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_tampered_look_ref_is_rejected(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        bible["locations"][0]["look_ref"]["receipt_id"] = "forged-receipt"
        with pytest.raises(CheckpointValidationError, match="is not the active look"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})


class TestInvalidation:
    def test_retired_look_invalidates_headshots_onward(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        assert get_completed_stages(pipeline_dir, PROJECT, PIPELINE) == ["canon_ingest", "proposal", "look_lock", "headshots"]
        retire_look(project_dir, c)
        assert get_completed_stages(pipeline_dir, PROJECT, PIPELINE) == ["canon_ingest", "proposal", "look_lock"]
        latest = get_latest_checkpoint(pipeline_dir, PROJECT)
        assert latest["stage"] == "headshots" and latest["invalidated_by"]["action"] == "retire"
        cache = (project_dir / "invalidations.jsonl").read_text().splitlines()
        assert len(cache) == 1 and json.loads(cache[0])["stage"] == "headshots"
        # the file on disk is untouched
        on_disk = json.loads((pipeline_dir / PROJECT / "checkpoint_headshots.json").read_text())
        assert on_disk["status"] == "completed" and "invalidated_by" not in on_disk
        with pytest.raises(CheckpointValidationError, match="invalidated by"):
            write(pipeline_dir, "visual_bible", {"visual_bible": {}}, status="awaiting_human")

    def test_deleted_cache_does_not_restore_retired_canon(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        retire_look(project_dir, c)
        (project_dir / "invalidations.jsonl").write_text("")
        assert "headshots" not in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)
        assert (project_dir / "invalidations.jsonl").read_text().strip()

    def test_retired_headshot_invalidates_visual_bible_onward_only(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        write(pipeline_dir, "visual_bible", {}, status="in_progress")
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        retire_headshot(project_dir, c, hs["receipt_id"])
        completed = get_completed_stages(pipeline_dir, PROJECT, PIPELINE)
        assert "headshots" in completed and "visual_bible" not in completed
        assert get_next_stage(pipeline_dir, PROJECT, PIPELINE) == "visual_bible"

    def test_checkpoint_written_after_receipt_stands(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        write(pipeline_dir, "visual_bible", {}, status="in_progress")
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        write(pipeline_dir, "visual_bible", {"visual_bible": bible})
        # a replacement look for the LOCATION retires nothing for the character;
        # it invalidates headshots+visual_bible written before it, and the
        # re-approved bible written after stands.
        l2 = location_look(dressing=["new netting"])
        activate_look(project_dir, l2, supersedes=look_hash(l))
        assert "visual_bible" not in get_completed_stages(pipeline_dir, PROJECT, PIPELINE)


class TestImportedAndTaint:
    def _import(self, project_dir, seed, origin_class):
        from lib import gates
        from lib.canonical_json import record_sha256
        from lib.reference_import import finalize_reference_import, prepare_reference_import
        from tests.lib.look_lock_helpers import png_bytes

        src = project_dir / ".staging" / f"{seed}.png"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(png_bytes(seed, size=(6, 6)))
        prepared = prepare_reference_import(project_dir, PROJECT, src, origin_class=origin_class, origin_tool="elsewhere")
        req = json.loads(prepared.request_path.read_text())
        token = gates.mint_gate_token(PROJECT, req["stage"], req["scope"], record_sha256(req["approval_record"]))
        receipt = receipts.record_human_approval(
            project_dir, PROJECT, req["stage"], req["scope"], req["approval_record"], token, "reference_import",
            entity_id=req["entity_id"], envelope=req["envelope"],
        )
        return finalize_reference_import(project_dir, receipt["receipt_id"])

    def test_imported_synthetic_hero_flows_through_headshots_and_sheets(self, v12):
        from lib.reference_import import imported_image_ref

        pipeline_dir, project_dir = v12
        c, l = ratified(project_dir)
        write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)})
        imported = self._import(project_dir, "elsewhere-face", "imported_synthetic")
        hero = imported_image_ref(project_dir, imported)
        pending = pending_packet(project_dir, c, n=1)
        pending["characters"][0]["candidates"] = [hero]
        write(pipeline_dir, "headshots", {"headshot_packet": pending}, status="awaiting_human")
        digest = checkpoint_digest(pipeline_dir / PROJECT / "checkpoint_headshots.json")
        _, receipt = approve_headshot(project_dir, c, hero["asset_id"], digest, recipe=prompt_recipe(c),
                                      origin="imported_synthetic", import_receipt_id=imported.import_receipt_id)
        approved = {"version": "1.0", "state": "approved", "characters": [{
            "entity_kind": "character", "entity_id": CHAR, "look_ref": pending["characters"][0]["look_ref"],
            "prompt_recipe": prompt_recipe(c), "hero": hero, "origin": "imported_synthetic",
            "import_receipt_id": imported.import_receipt_id, "normalized_pixel_hash": hero["asset_id"],
            "approval_receipt_id": receipt["receipt_id"], "candidates_rejected": []}]}
        # an approved packet that drops the import receipt is refused
        broken = copy.deepcopy(approved)
        broken["characters"][0]["origin"] = "generated"
        with pytest.raises(CheckpointValidationError, match="origin/pixel hash|imported"):
            write(pipeline_dir, "headshots", {"headshot_packet": broken})
        write(pipeline_dir, "headshots", {"headshot_packet": approved})
        write(pipeline_dir, "visual_bible", {}, status="in_progress")
        bible = bible_v11(project_dir, c, l, hero, receipt)
        write(pipeline_dir, "visual_bible", {"visual_bible": bible})

    def test_casting_inspiration_in_lineage_fails_the_gate(self, v12):
        pipeline_dir, project_dir = v12
        c, l, approved, hs, _ = through_headshots(pipeline_dir, project_dir)
        casting = self._import(project_dir, "real-person", "casting_inspiration")
        assert casting.import_receipt_id is None
        write(pipeline_dir, "visual_bible", {}, status="in_progress")
        bible = bible_v11(project_dir, c, l, approved["characters"][0]["hero"], hs)
        poisoned = image(project_dir, "poisoned-angle", role="angle", look_refs=look_refs_for(l),
                         input_asset_ids=[casting.normalized_pixel_hash])
        bible["locations"][0]["angles"][0] = poisoned
        loc = bible["locations"][0]
        r = approve(project_dir, "visual_bible", f"location:{LOC}", location_approval_record(loc, PALETTE), "location", LOC)
        loc["approval_receipt_id"] = r["receipt_id"]
        with pytest.raises(CheckpointValidationError, match="casting_inspiration"):
            write(pipeline_dir, "visual_bible", {"visual_bible": bible})

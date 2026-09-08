"""T11 — the ``take`` receipt (C4): accept or reject IS the decision.

One real interaction, end to end, in the real production the shot-ready
fixture builds: a candidate take is published as a gate request, the human
answers ONCE at the gate — accept or reject — and that single answer mints a
signed ``take`` receipt bound to the project, the shot, the take's bytes and
its generation receipt. The runner then applies the receipt: accept becomes
``usage_status: selected``, reject becomes ``rejected`` and the take stays in
the manifest as history. There is no second approval step anywhere in the
flow, and no programmatic path to a decision.

Enforcement is the other half: under a shot-ready pin (authored-film 1.6+)
the asset_manifest cannot record a ``selected`` take without a matching
verified receipt, and a receipt minted for another take or another shot never
matches. A project that is not shot-ready is untouched (the 1.5 contract is
proven byte-for-byte by T1/T6; here it is the older-pin control).

Invented ids only.
"""

from __future__ import annotations

import copy
import io
import json

import pytest

from lib.canon_enforcement import take_record
from lib.checkpoint import CheckpointValidationError, read_checkpoint, write_checkpoint
from lib.pathsafe import sha256_file
from lib.receipts import ReceiptError, find_generation, require_take_receipt, verified_approvals
from scripts import gate_approve, take_request

from tests.lib.look_lock_helpers import PROJECT
from tests.lib.test_authored_film_contract import (  # noqa: F401  (gates_dir autouse, project fixture)
    gates_dir,
    project,
)
from tests.lib.test_shot_ready_rule import (
    FINISHED,
    VIDEO_ENDPOINT,
    bible_of,
    build_world,
    hero_ref_of,
    scene_plan_for,
    write,
    write_script,
)
from tests.lib.test_visual_bible_contract import (
    approve_storyboards,
    shot_asset,
)

SHOT = "shot-1"


# ---------------------------------------------------------------------------
# The world a take is decided in: a 1.6 production stopped at the assets gate
# ---------------------------------------------------------------------------

@pytest.fixture
def shot_ready(project, monkeypatch):
    return build_world(project, monkeypatch, "1.6")


def _publish_takes(world, *seeds: str, shot_id: str = SHOT) -> dict:
    """script + scene_plan + an ``awaiting_human`` assets checkpoint holding
    one approved storyboard frame and one candidate take per seed."""
    pipeline, project_dir = world["pipeline"], world["project"]
    character = FINISHED[0]
    refs = [hero_ref_of(bible_of(world), character)]
    write_script(world)
    write(pipeline, "scene_plan", {"scene_plan": scene_plan_for([character], shot_id=shot_id)})
    board = shot_asset(project_dir, f"board-{shot_id}", asset_class="storyboard_frame", references=refs,
                       shot_id=shot_id, endpoint=VIDEO_ENDPOINT)
    approve_storyboards(project_dir, board)
    takes = [
        shot_asset(project_dir, seed, asset_class="shot_visual", references=refs, board=board,
                   shot_id=shot_id, endpoint=VIDEO_ENDPOINT, usage_status="candidate")
        for seed in seeds
    ]
    manifest = {"version": "1.1", "assets": [board, *takes]}
    write(pipeline, "assets", {"asset_manifest": manifest}, status="awaiting_human")
    return {"manifest": manifest, "board": board, "takes": takes, "shot_id": shot_id}


def _manifest_of(world) -> dict:
    return read_checkpoint(world["pipeline"], PROJECT, "assets")["artifacts"]["asset_manifest"]


def _asset_of(world, take_id: str) -> dict:
    return next(a for a in _manifest_of(world)["assets"] if a.get("id") == take_id)


def _decide_at_the_gate(world, request_id: str, answer: str, monkeypatch) -> int:
    """The whole human interaction: one keystroke at the gate. Returns how
    many prompts the human was asked."""
    asked = []

    def _input(prompt: str = "") -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", _input)
    rc = gate_approve.main(["--project", PROJECT, "--projects-dir", str(world["pipeline"]),
                            "--request", request_id])
    assert rc == 0, f"gate exited {rc}"
    return len(asked)


def _take_receipts(world) -> list[dict]:
    return verified_approvals(world["project"], "take")


def _expected_record(world, take: dict, usage_status: str) -> dict:
    project_dir = world["project"]
    sha = sha256_file(project_dir / take["path"])
    generation = find_generation(project_dir, sha)
    return take_record(
        project_id=PROJECT, shot_id=take["shot_id"], asset_id=sha,
        generation_receipt_id=generation["receipt_id"], usage_status=usage_status,
    )


# ---------------------------------------------------------------------------
# The interaction
# ---------------------------------------------------------------------------

class TestT11OneDecisionOneReceipt:
    def test_accept_mints_one_bound_receipt_and_selects_the_take(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        take = published["takes"][0]
        request = take_request.publish_take_request(world["project"], SHOT, take["id"])

        prompts = _decide_at_the_gate(world, request["request_id"], "a", monkeypatch)

        assert prompts == 1, "accepting a take must be one action, not an answer plus a confirmation"
        receipts = _take_receipts(world)
        assert len(receipts) == 1
        assert receipts[0]["record"] == _expected_record(world, take, "selected")
        assert receipts[0]["entity_id"] == take["id"]
        assert receipts[0]["scope"] == f"shot:{SHOT}"
        done = json.loads((world["project"] / ".gate-requests" / "done" / f"{request['request_id']}.json").read_text())
        assert done["approval_receipt_id"] == receipts[0]["receipt_id"]

        applied = take_request.finish_take(world["project"], request["request_id"], out=io.StringIO())
        assert applied["usage_status"] == "selected"
        assert applied["receipt_id"] == receipts[0]["receipt_id"]
        assert _asset_of(world, take["id"])["usage_status"] == "selected"

    def test_reject_mints_a_rejected_receipt_and_the_take_stays_as_history(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        take = published["takes"][0]
        request = take_request.publish_take_request(world["project"], SHOT, take["id"])

        prompts = _decide_at_the_gate(world, request["request_id"], "r", monkeypatch)

        assert prompts == 1, "rejecting a take must be one action too — a decline that records nothing is not a decision"
        receipts = _take_receipts(world)
        assert len(receipts) == 1
        assert receipts[0]["record"] == _expected_record(world, take, "rejected")
        assert not (world["project"] / ".gate-requests" / "declined").exists()

        applied = take_request.finish_take(world["project"], request["request_id"], out=io.StringIO())
        assert applied["usage_status"] == "rejected"
        rejected = _asset_of(world, take["id"])
        assert rejected["usage_status"] == "rejected"
        assert rejected["path"] == take["path"], "a rejected take stays in the manifest as history"

    def test_quitting_decides_nothing(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        request = take_request.publish_take_request(world["project"], SHOT, published["takes"][0]["id"])

        _decide_at_the_gate(world, request["request_id"], "q", monkeypatch)

        assert _take_receipts(world) == []
        assert (world["project"] / ".gate-requests" / f"{request['request_id']}.json").is_file()

        # And a take can never be quietly declined: the reject IS the record.
        req = json.loads((world["project"] / ".gate-requests" / f"{request['request_id']}.json").read_text())
        with pytest.raises(gate_approve.GateHandlerError, match="not declined"):
            gate_approve._decline_request(req, world["project"], "no note recorded")
        assert not (world["project"] / ".gate-requests" / "declined").exists()

    def test_no_record_exists_until_the_human_answers(self, shot_ready):
        """There is no programmatic decision path: the record itself cannot be
        constructed without the human's accept/reject."""
        world = shot_ready
        published = _publish_takes(world, "take-1")
        request = take_request.publish_take_request(world["project"], SHOT, published["takes"][0]["id"])
        req = json.loads((world["project"] / ".gate-requests" / f"{request['request_id']}.json").read_text())

        assert req.get("approval_record") is None
        with pytest.raises(gate_approve.GateHandlerError, match="accept"):
            gate_approve.construct(world["project"], req)


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

class TestT11TheReceiptIsRequiredAndBound:
    def test_a_selected_take_without_a_receipt_is_refused_at_assets(self, shot_ready):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        manifest = published["manifest"]
        published["takes"][0]["usage_status"] = "selected"
        with pytest.raises(CheckpointValidationError, match="take"):
            write(world["pipeline"], "assets", {"asset_manifest": manifest}, status="awaiting_human")

    def test_a_receipt_for_another_take_does_not_select_this_one(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1", "take-2")
        signed, other = published["takes"]
        request = take_request.publish_take_request(world["project"], SHOT, signed["id"])
        _decide_at_the_gate(world, request["request_id"], "a", monkeypatch)
        take_request.finish_take(world["project"], request["request_id"], out=io.StringIO())

        manifest = _manifest_of(world)
        take = next(a for a in manifest["assets"] if a["id"] == other["id"])
        take["usage_status"] = "selected"
        with pytest.raises(CheckpointValidationError, match="take"):
            write(world["pipeline"], "assets", {"asset_manifest": manifest}, status="awaiting_human")

    def test_a_receipt_is_bound_to_its_shot_asset_and_generation(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        take = published["takes"][0]
        request = take_request.publish_take_request(world["project"], SHOT, take["id"])
        _decide_at_the_gate(world, request["request_id"], "a", monkeypatch)

        project_dir = world["project"]
        sha = sha256_file(project_dir / take["path"])
        generation_id = find_generation(project_dir, sha)["receipt_id"]
        bound = dict(shot_id=SHOT, asset_id=sha, generation_receipt_id=generation_id, usage_status="selected")
        assert require_take_receipt(project_dir, **bound)["record"]["asset_id"] == sha
        for wrong in (
            dict(bound, shot_id="shot-9"),
            dict(bound, asset_id="f" * 64),
            dict(bound, generation_receipt_id="not-the-generation"),
            dict(bound, usage_status="rejected"),
        ):
            with pytest.raises(ReceiptError):
                require_take_receipt(project_dir, **wrong)

    def test_a_second_decision_on_a_decided_take_is_refused(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        take = published["takes"][0]
        request = take_request.publish_take_request(world["project"], SHOT, take["id"])
        _decide_at_the_gate(world, request["request_id"], "a", monkeypatch)
        take_request.finish_take(world["project"], request["request_id"], out=io.StringIO())

        with pytest.raises(take_request.TakeRequestError, match="decided"):
            take_request.publish_take_request(world["project"], SHOT, take["id"])


class TestT11ARequestIsAnEndingOrNothing:
    """Fix round 1: applying a decision may touch nothing but the manifest;
    one decision per shot is in flight at a time; and a request that can no
    longer be decided can be withdrawn — but only then."""

    def test_applying_a_decision_changes_the_manifest_and_nothing_else(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1")
        take = published["takes"][0]
        # A real assets checkpoint carries a spend snapshot, a review, run
        # state and policy flags. None of them is the take gate's business.
        write_checkpoint(
            world["pipeline"], PROJECT, "assets", "awaiting_human",
            {"asset_manifest": published["manifest"]},
            pipeline_type="authored-film", checkpoint_policy="manual_all", human_approval_required=True,
            human_approved=True, review={"reviewer": "invented", "notes": "keep me"},
            cost_snapshot={"total_spent_usd": 4.25}, metadata={"run_state": {"shot-1": {"mode": "takes"}}},
        )
        path = world["pipeline"] / PROJECT / "checkpoint_assets.json"
        before = json.loads(path.read_text())
        request = take_request.publish_take_request(world["project"], SHOT, take["id"])
        _decide_at_the_gate(world, request["request_id"], "a", monkeypatch)
        take_request.finish_take(world["project"], request["request_id"], out=io.StringIO())

        after = json.loads(path.read_text())
        volatile = {"timestamp", "artifacts"}
        assert {k: v for k, v in after.items() if k not in volatile} == \
               {k: v for k, v in before.items() if k not in volatile}
        expected = copy.deepcopy(before["artifacts"])
        next(a for a in expected["asset_manifest"]["assets"] if a["id"] == take["id"])["usage_status"] = "selected"
        assert after["artifacts"] == expected

    def test_a_shot_has_one_decision_in_flight_at_a_time(self, shot_ready, monkeypatch):
        world = shot_ready
        published = _publish_takes(world, "take-1", "take-2")
        first, second = published["takes"]
        request = take_request.publish_take_request(world["project"], SHOT, first["id"])

        with pytest.raises(take_request.TakeRequestError, match=request["request_id"]):
            take_request.publish_take_request(world["project"], SHOT, second["id"])
        # Republishing the SAME take is the same decision, not a second one.
        assert take_request.publish_take_request(world["project"], SHOT, first["id"])["request_id"] \
               == request["request_id"]

        _decide_at_the_gate(world, request["request_id"], "a", monkeypatch)
        # Signed but not applied is still in flight.
        with pytest.raises(take_request.TakeRequestError, match=request["request_id"]):
            take_request.publish_take_request(world["project"], SHOT, second["id"])

        take_request.finish_take(world["project"], request["request_id"], out=io.StringIO())
        queued = take_request.publish_take_request(world["project"], SHOT, second["id"])
        _decide_at_the_gate(world, queued["request_id"], "r", monkeypatch)
        take_request.finish_take(world["project"], queued["request_id"], out=io.StringIO())
        assert _asset_of(world, first["id"])["usage_status"] == "selected"
        assert _asset_of(world, second["id"])["usage_status"] == "rejected"

    def test_only_an_undecidable_request_can_be_withdrawn(self, shot_ready):
        from lib.pipeline_pin import PipelinePinError, require_migration_coverage

        world = shot_ready
        published = _publish_takes(world, "take-1")
        request = take_request.publish_take_request(world["project"], SHOT, published["takes"][0]["id"])
        request_id = request["request_id"]

        with pytest.raises(take_request.TakeRequestError, match="not stuck"):
            take_request.withdraw_take_request(world["project"], request_id, out=io.StringIO())

        # The stage moves on underneath it: the request can never be decided.
        write(world["pipeline"], "assets", {"asset_manifest": published["manifest"]}, status="awaiting_human")
        with pytest.raises(PipelinePinError, match=request_id):
            require_migration_coverage(world["project"], "authored-film", "1.6")

        assert take_request.withdraw_take_request(world["project"], request_id, out=io.StringIO())["withdrawn"]
        assert not (world["project"] / ".gate-requests" / f"{request_id}.json").exists()
        assert (world["project"] / ".gate-requests" / "abandoned" / f"{request_id}.json").is_file()
        assert _take_receipts(world) == []
        require_migration_coverage(world["project"], "authored-film", "1.6")


class TestT11OlderPinsAreUnchanged:
    """A project that is not shot-ready never needs a take receipt: the same
    manifest, with a selected take and nothing signed for it, still writes."""

    def test_a_selected_take_needs_no_receipt_without_a_shot_ready_pin(self, project):
        from tests.lib.test_visual_bible_contract import (
            establishing_ref,
            hero_ref,
            setup_through_scene_plan,
        )

        pipeline_dir, project_dir = project
        bible = setup_through_scene_plan(pipeline_dir, project_dir)
        refs = [hero_ref(bible), establishing_ref(bible)]
        board = shot_asset(project_dir, "board-1", asset_class="storyboard_frame", references=refs)
        approve_storyboards(project_dir, board)
        take = shot_asset(project_dir, "take-1", asset_class="shot_visual", references=refs, board=board,
                          usage_status="selected")
        from lib.canon_enforcement import project_is_shot_ready

        assert not project_is_shot_ready(project_dir)
        write(pipeline_dir, "assets", {"asset_manifest": {"version": "1.1", "assets": [board, take]}})
        assert verified_approvals(project_dir, "take") == []

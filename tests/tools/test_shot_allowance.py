"""C2 / T10 — the per-shot dollar allowance and video take limit.

Under a shot-ready pin (authored-film 1.6+) every paid call named for a shot
is bounded by two numbers that live on the shot's record in the on-disk,
pin-bound ``scene_plan`` checkpoint: ``spend_allowance_usd`` (one allowance
shared by storyboard frames, video takes and any other call for that shot)
and ``max_video_takes`` (video attempts only).

Both limits are proven at the REAL boundary: ``upload_image_fal`` and
``fal_queue_submit`` are watched and must not run on a refusal, and no
reservation event may be written. Counts are rebuilt from the reservation
ledger, never from memory.

All network is mocked. Real signed receipts against a per-test gates dir.
Invented ids only.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.shot_allowance import ShotAllowanceError, scope_usage, shot_scope
from lib.state_io import read_jsonl
from tools.cost_tracker import (
    IndeterminatePaidCallError,
    indeterminate_reservations,
    load_reservations,
    reconcile_paid_call,
    reserve_paid_call,
    resume_check,
)
from tools.graphics.seedream_image import SeedreamImage
from tools.video import _shared
from tools.video.kling_reference_video import KlingReferenceVideo

from tests.lib.look_lock_helpers import character_look
from tests.tools._authored_film_helpers import (
    make_tracker,
    make_verified_project,
    project_tracker,
    write_scene_plan_checkpoint,
)
from tests.tools.test_kling_reference_video import (  # noqa: F401  (env fixture)
    _happy_path,
    _inputs,
    _shot_setup,
    env,
)
from tests.tools.test_shot_ready_ownership import ENTITY, activate_look, pin, write_bible

from lib.look_spec import look_hash as _look_hash

SHOT = "sh-1"
KLING_TAKE_USD = 0.56  # 5s, audio off
SEEDREAM_FRAME_USD = 0.135  # one 2K image, one reference


# ---- fixtures ---------------------------------------------------------------


def write_scene_plan(project: Path, shots: list[dict], *, version: str = "1.6") -> None:
    write_scene_plan_checkpoint(project, shots, version=version, character_refs=(ENTITY,))


def shot_record(**limits) -> dict:
    return {"shot_id": SHOT, "description": "Hall, slow push in.", **limits}


@pytest.fixture
def shot(env):
    """A 1.6-pinned project with an approved bible entry, an active look, a
    signed storyboard batch, and a scene plan allowing one video take."""
    inputs, _expected, hero, frame = _shot_setup(env)
    look = character_look(ENTITY)
    activate_look(env, look)
    write_bible(env, entity_id=ENTITY, hero=hero, status="approved")
    pin(env, "1.6")
    write_scene_plan(env, [shot_record(spend_allowance_usd=1.0, max_video_takes=1)])
    inputs["look_refs"] = [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}]
    return {"project": env, "inputs": inputs, "hero": hero, "frame": frame, "look": look}


def seedream_inputs(shot: dict, **extra) -> dict:
    """A storyboard-frame call: a seedream edit naming the shot."""
    from tools import prompt_builder as pb

    built = pb.build_prompt(shot["look"], role="hero", palette=["moss", "brass"])
    base = {
        "prompt": built["prompt"],
        "prompt_recipe": built["prompt_recipe"],
        "project_dir": str(shot["project"]),
        "operation": "edit",
        "palette": ["moss", "brass"],
        "asset_class": "storyboard_frame",
        "asset_role": "hero",
        "shot_id": SHOT,
        "look_refs": [{"entity_kind": "character", "entity_id": ENTITY,
                       "look_hash": _look_hash(shot["look"])}],
        "reference_image_paths": [str(shot["hero"]["path"])],
        "reference_manifest": [{"asset_id": shot["hero"]["sha256"],
                                "path": "canon/visual/objects/hero.png",
                                "role": "hero", "visual_bible_entity_id": ENTITY}],
    }
    base.update(extra)
    return base


# ---- boundary helpers -------------------------------------------------------


def events(project: Path) -> list[dict]:
    return read_jsonl(project / "cost-reservations.jsonl")


def usage(project: Path) -> tuple[float, int]:
    return scope_usage(load_reservations(project), shot_scope(SHOT))


def run_video(shot: dict, *, request_id: str = "req-v1", **overrides):
    """Execute a take with the upload and submission boundaries watched."""
    inputs = dict(shot["inputs"])
    inputs.update(overrides)
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png") as up, \
         patch.object(_shared, "fal_queue_submit", return_value={"request_id": request_id}) as submit, \
         wait, dl, verify:
        result = KlingReferenceVideo().execute(_inputs(shot["project"], **inputs))
    return result, up, submit


def refuse_video(shot: dict, **overrides):
    """Execute a take expecting a refusal: nothing may be uploaded or submitted."""
    inputs = dict(shot["inputs"])
    inputs.update(overrides)
    before = events(shot["project"])
    with patch.object(_shared, "upload_image_fal") as up, \
         patch.object(_shared, "fal_queue_submit") as submit:
        result = KlingReferenceVideo().execute(_inputs(shot["project"], **inputs))
    assert not result.success, "the take was expected to be refused"
    up.assert_not_called()
    submit.assert_not_called()
    assert events(shot["project"]) == before, "a refused call wrote a reservation event"
    return result


def run_storyboard(shot: dict, *, request_id: str = "req-b1", **extra):
    from tests.tools.test_seedream_image import _fake_download

    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png") as up, \
         patch.object(_shared, "fal_queue_submit", return_value={"request_id": request_id}) as submit, \
         patch.object(_shared, "fal_queue_wait", return_value={"images": [{"url": "https://v3.fal.media/o.png"}]}), \
         patch.object(_shared, "fal_download", side_effect=_fake_download):
        result = SeedreamImage().execute(seedream_inputs(shot, **extra))
    return result, up, submit


def outstanding(shot: dict, *, request_id: str) -> str:
    """A take the provider ACCEPTED (request id persisted) whose tool then
    failed: ``pending_billing``, the state that releases nothing. Returns its
    reservation id."""
    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
         patch.object(_shared, "fal_queue_submit", return_value={"request_id": request_id}), \
         patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalQueueError("deadline")):
        assert not KlingReferenceVideo().execute(_inputs(shot["project"], **shot["inputs"])).success
    reservation = next(iter(load_reservations(shot["project"]).values()))
    assert reservation["state"] == "pending_billing"
    return reservation["reservation_id"]


def refuse_storyboard(shot: dict, **extra):
    before = events(shot["project"])
    with patch.object(_shared, "upload_image_fal") as up, \
         patch.object(_shared, "fal_queue_submit") as submit:
        result = SeedreamImage().execute(seedream_inputs(shot, **extra))
    assert not result.success, "the storyboard call was expected to be refused"
    up.assert_not_called()
    submit.assert_not_called()
    assert events(shot["project"]) == before, "a refused call wrote a reservation event"
    return result


# ---- the take limit ---------------------------------------------------------


class TestVideoTakeLimit:
    def test_a_storyboard_frame_and_one_video_fit_one_take(self, shot):
        """Storyboard calls spend dollars; only video calls spend a take."""
        board, up, submit = run_storyboard(shot)
        assert board.success, board.error
        assert usage(shot["project"]) == (pytest.approx(SEEDREAM_FRAME_USD, abs=1e-4), 0)

        take, _up, _submit = run_video(shot)
        assert take.success, take.error
        held, attempts = usage(shot["project"])
        assert attempts == 1
        assert held == pytest.approx(SEEDREAM_FRAME_USD + KLING_TAKE_USD, abs=1e-4)

    def test_a_second_video_is_refused_at_the_take_limit(self, shot):
        first, _up, _submit = run_video(shot)
        assert first.success, first.error
        result = refuse_video(shot, output_path=str(shot["project"] / "assets" / "video" / "b.mp4"))
        assert "max_video_takes" in result.error and SHOT in result.error

    def test_the_limit_counts_only_video_kind_reservations(self, shot):
        """Two storyboard frames leave the single take intact."""
        write_scene_plan(shot["project"], [shot_record(spend_allowance_usd=5.0, max_video_takes=1)])
        assert run_storyboard(shot, request_id="req-b1")[0].success
        assert run_storyboard(shot, request_id="req-b2")[0].success
        assert usage(shot["project"])[1] == 0
        assert run_video(shot)[0].success


# ---- the dollar allowance ---------------------------------------------------


class TestDollarAllowance:
    def test_a_storyboard_call_over_the_allowance_is_refused(self, shot):
        write_scene_plan(shot["project"], [shot_record(spend_allowance_usd=0.10, max_video_takes=2)])
        result = refuse_storyboard(shot)
        assert "spend_allowance_usd" in result.error and SHOT in result.error

    def test_a_video_call_over_the_allowance_is_refused(self, shot):
        write_scene_plan(shot["project"], [shot_record(spend_allowance_usd=0.20, max_video_takes=2)])
        result = refuse_video(shot)
        assert "spend_allowance_usd" in result.error

    def test_spend_plus_outstanding_is_what_the_allowance_measures(self, shot):
        """The first frame's dollars leave no room for a take even though the
        take limit is untouched."""
        write_scene_plan(shot["project"], [shot_record(spend_allowance_usd=0.60, max_video_takes=3)])
        assert run_storyboard(shot)[0].success
        result = refuse_video(shot)
        assert "spend_allowance_usd" in result.error


# ---- the counting rules (constraint 15) -------------------------------------


class TestWhatConsumesAndWhatReleases:
    def test_reconciling_an_unfinished_job_consumes_nothing(self, shot):
        """Polling and settling an existing job is not a new call: no new
        reservation, no second attempt, no second charge."""
        outstanding(shot, request_id="req-slow")
        before_ids = set(load_reservations(shot["project"]))
        before = usage(shot["project"])

        with pytest.raises(IndeterminatePaidCallError):
            resume_check(shot["project"])
        assert set(load_reservations(shot["project"])) == before_ids
        assert usage(shot["project"]) == before

        # The reconciler's COMPLETED branch: the provider finished the job it
        # already accepted. The shot pays for it once.
        rid = before_ids.pop()
        reconcile_paid_call(shot["project"], rid, KLING_TAKE_USD, "completed", project_tracker(shot["project"]))
        assert set(load_reservations(shot["project"])) == {rid}
        assert usage(shot["project"]) == before

    def test_a_confirmed_provider_rejection_releases_the_attempt_and_the_dollars(self, shot):
        rid = outstanding(shot, request_id="req-bad")
        assert usage(shot["project"]) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)
        refuse_video(shot)  # still counted while the outcome is unknown

        # The reconciler's FAILED/CANCELLED/ERROR branch: a definite rejection.
        reconcile_paid_call(shot["project"], rid, 0.0, "failed", project_tracker(shot["project"]))
        assert usage(shot["project"]) == (0.0, 0)
        assert run_video(shot, request_id="req-retry")[0].success

    def test_a_timed_out_submission_with_no_id_stays_outstanding(self, shot):
        """A missing provider request id is not proof of non-acceptance."""
        with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
             patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-lost"}), \
             patch("tools.cost_tracker.attach_request_id", side_effect=OSError("timed out")):
            assert not KlingReferenceVideo().execute(_inputs(shot["project"], **shot["inputs"])).success
        reservation = next(iter(load_reservations(shot["project"]).values()))
        assert reservation["state"] == "submitting" and reservation["provider_request_id"] is None
        assert usage(shot["project"]) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)

        # It blocks the next call before any upload, and it is still counted.
        result = refuse_video(shot)
        assert "terminal outcome" in result.error
        with pytest.raises(IndeterminatePaidCallError):
            resume_check(shot["project"])

        # The reconciler cannot resolve it by polling — it is exactly the
        # "check the dashboard by hand" case — so it stays counted until a
        # human confirms non-acceptance.
        assert [r["reservation_id"] for r in indeterminate_reservations(shot["project"])] == \
            [reservation["reservation_id"]]
        assert usage(shot["project"]) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)

        reconcile_paid_call(shot["project"], reservation["reservation_id"], 0.0, "failed",
                            project_tracker(shot["project"]))
        assert usage(shot["project"]) == (0.0, 0)
        assert run_video(shot, request_id="req-after")[0].success

    def test_a_failure_after_the_id_is_attached_releases_nothing(self, shot):
        with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
             patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-billed"}), \
             patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/o.mp4"}}), \
             patch.object(_shared, "fal_download", side_effect=RuntimeError("download died")):
            assert not KlingReferenceVideo().execute(_inputs(shot["project"], **shot["inputs"])).success
        reservation = next(iter(load_reservations(shot["project"]).values()))
        assert reservation["state"] == "pending_billing"
        assert usage(shot["project"]) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)
        refuse_video(shot)

    def test_a_rejected_take_refunds_nothing(self, shot):
        """A completed take keeps its dollars and its attempt whatever the
        writer later does with it: the ledger is the only counter."""
        assert run_video(shot)[0].success
        assert usage(shot["project"]) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)
        assert [e for e in events(shot["project"]) if e.get("state") == "failed"] == []
        refuse_video(shot)

    def test_reservation_events_carry_scope_and_kind_through_settlement(self, shot):
        assert run_video(shot)[0].success
        rows = [e for e in events(shot["project"]) if e.get("reservation_id")]
        assert len(rows) >= 3  # submitting, request id, terminal
        for row in rows:
            assert row["scope"] == shot_scope(SHOT)
            assert row["kind"] == "video"
        folded = next(iter(load_reservations(shot["project"]).values()))
        assert folded["scope"] == shot_scope(SHOT) and folded["kind"] == "video"

    def test_a_storyboard_reservation_is_scoped_to_the_shot_as_an_image(self, shot):
        assert run_storyboard(shot)[0].success
        folded = next(iter(load_reservations(shot["project"]).values()))
        assert folded["scope"] == shot_scope(SHOT) and folded["kind"] == "image"


# ---- concurrency and restart ------------------------------------------------


class TestConcurrencyAndRestart:
    def test_two_concurrent_calls_with_room_for_one_reserve_once(self, shot):
        """Both pass the pre-upload check; the lock at reservation lets one through."""
        project = shot["project"]
        write_scene_plan(project, [shot_record(spend_allowance_usd=KLING_TAKE_USD, max_video_takes=1)])
        start = threading.Barrier(2)
        outcomes: list[object] = []
        lock = threading.Lock()

        def reserve() -> None:
            start.wait(timeout=10)
            try:
                rid = reserve_paid_call(
                    make_tracker(project), project, tool="kling_reference_video",
                    endpoint="fal-ai/kling-video/o3/pro/reference-to-video",
                    normalized_inputs_hash="ab" * 32, reserved_usd=KLING_TAKE_USD,
                    inputs={"asset_class": "shot_visual", "shot_id": SHOT}, kind="video",
                )
                outcome: object = rid
            except Exception as exc:  # noqa: BLE001 — the refusal is the result
                outcome = exc
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        reserved = [o for o in outcomes if isinstance(o, str)]
        refused = [o for o in outcomes if isinstance(o, ShotAllowanceError)]
        assert len(reserved) == 1 and len(refused) == 1, outcomes
        assert usage(project) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)
        # The loser is refused before it can even estimate: one cost entry.
        assert len(json.loads((project / "cost_log.json").read_text())["entries"]) == 1

    def test_a_restart_rebuilds_the_count_from_the_ledger(self, shot):
        """Nothing is remembered between calls: losing the cost log (the
        tracker's memory) does not give the shot a second take."""
        assert run_video(shot)[0].success
        (shot["project"] / "cost_log.json").unlink()
        assert usage(shot["project"]) == (pytest.approx(KLING_TAKE_USD, abs=1e-4), 1)
        result = refuse_video(shot)
        assert "max_video_takes" in result.error


# ---- authority: the shot record is the only source --------------------------


class TestAuthority:
    def test_a_shot_call_without_a_shot_id_is_refused(self, shot):
        result = refuse_video(shot, shot_id=None)
        assert "shot_id" in result.error

    def test_a_shot_with_no_record_in_the_plan_is_refused(self, shot):
        write_scene_plan(shot["project"], [shot_record(spend_allowance_usd=1.0, max_video_takes=1)
                                           | {"shot_id": "sh-other"}])
        result = refuse_video(shot)
        assert SHOT in result.error and "scene_plan" in result.error

    def test_a_missing_scene_plan_checkpoint_is_refused(self, shot):
        (shot["project"] / "checkpoint_scene_plan.json").unlink()
        result = refuse_video(shot)
        assert "scene_plan" in result.error

    @pytest.mark.parametrize("record", [
        {"spend_allowance_usd": 1.0},
        {"max_video_takes": 1},
        {},
        {"spend_allowance_usd": "1.00", "max_video_takes": 1},
        {"spend_allowance_usd": 1.0, "max_video_takes": -1},
    ])
    def test_a_record_without_usable_limits_is_refused(self, shot, record):
        write_scene_plan(shot["project"], [shot_record(**record)])
        result = refuse_video(shot)
        assert "spend_allowance_usd" in result.error or "max_video_takes" in result.error

    def test_caller_supplied_limits_are_never_read(self, shot):
        """Inputs cannot raise a shot's ceiling (constraint 14)."""
        write_scene_plan(shot["project"], [shot_record(spend_allowance_usd=0.10, max_video_takes=9)])
        result = refuse_video(shot, spend_allowance_usd=99.0, max_video_takes=99)
        assert "spend_allowance_usd" in result.error


# ---- boundaries C2 does not move --------------------------------------------


class TestSeedanceSharesTheLimit:
    """Seedance 2.5 is the other video tool on the shot path (entity-free
    scenes); its takes come out of the same shot allowance."""

    def test_a_seedance_take_consumes_the_shots_only_video_attempt(self, tmp_path, monkeypatch):
        from tests.tools.test_seedance_v25 import _fake_download
        from tests.tools.test_seedance_v25 import _inputs as seedance_call
        from tests.tools.test_seedance_v25 import _shot_setup as seedance_shot
        from tools.video.seedance_video import SeedanceVideo

        monkeypatch.setenv("FAL_KEY", "test-key")
        project = make_verified_project(tmp_path, monkeypatch, "proj-cinder")
        (project / "assets" / "video").mkdir(parents=True)
        inputs, _expected, hero, _frame = seedance_shot(project)
        look = character_look(ENTITY)
        activate_look(project, look)
        write_bible(project, entity_id=ENTITY, hero=hero, status="approved")
        pin(project, "1.6")
        write_scene_plan_checkpoint(
            project,
            [{"shot_id": SHOT, "description": "Hall, slow push in.",
              "spend_allowance_usd": 20.0, "max_video_takes": 1}],
            character_refs=(ENTITY,),
        )
        inputs["look_refs"] = [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}]

        with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
             patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-sd25"}), \
             patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/o.mp4"}}), \
             patch.object(_shared, "fal_download", side_effect=_fake_download), \
             patch.object(_shared, "verify_video_file", return_value={"duration_seconds": 6.0}):
            first = SeedanceVideo().execute(seedance_call(project, **inputs))
        assert first.success, first.error
        assert scope_usage(load_reservations(project), shot_scope(SHOT))[1] == 1

        before = read_jsonl(project / "cost-reservations.jsonl")
        with patch.object(_shared, "upload_image_fal") as up, \
             patch.object(_shared, "fal_queue_submit") as submit:
            second = SeedanceVideo().execute(
                seedance_call(project, **dict(inputs, output_path=str(project / "assets" / "video" / "b.mp4"))))
        assert not second.success and "max_video_takes" in second.error
        up.assert_not_called()
        submit.assert_not_called()
        assert read_jsonl(project / "cost-reservations.jsonl") == before


class TestUntouchedBoundaries:
    def test_a_non_shot_governed_call_keeps_project_cap_behavior(self, shot):
        """No shot id, no allowance: the project cap is the only limit."""
        (shot["project"] / "checkpoint_scene_plan.json").unlink()
        result, _up, _submit = run_storyboard(
            shot, asset_class=None, shot_id=None, request_id="req-nonshot",
        )
        assert result.success, result.error
        assert usage(shot["project"]) == (0.0, 0)

    def test_the_project_cap_still_refuses_independently(self, tmp_path, monkeypatch):
        """A generous shot allowance does not lift the signed budget cap."""
        monkeypatch.setenv("FAL_KEY", "test-key")
        project = make_verified_project(tmp_path, monkeypatch, "proj-thrift", budget=0.10)
        (project / "assets" / "video").mkdir(parents=True)
        inputs, _expected, hero, _frame = _shot_setup(project)
        look = character_look(ENTITY)
        activate_look(project, look)
        write_bible(project, entity_id=ENTITY, hero=hero, status="approved")
        pin(project, "1.6")
        write_scene_plan(project, [shot_record(spend_allowance_usd=500.0, max_video_takes=9)])
        inputs["look_refs"] = [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}]
        with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
             patch.object(_shared, "fal_queue_submit") as submit:
            result = KlingReferenceVideo().execute(_inputs(project, **inputs))
        assert not result.success and "usable budget" in result.error
        submit.assert_not_called()
        assert not (project / "cost-reservations.jsonl").exists()

    def test_a_1_5_pinned_project_is_unchanged(self, env):
        """The shot path under 1.5 keeps today's project-cap-only behavior:
        no scene plan, no allowance, unlimited takes."""
        inputs, _expected, hero, _frame = _shot_setup(env)
        look = character_look(ENTITY)
        activate_look(env, look)
        write_bible(env, entity_id=ENTITY, hero=hero, status="approved")
        pin(env, "1.5")
        inputs["look_refs"] = [{"entity_kind": "character", "entity_id": ENTITY, "look_hash": _look_hash(look)}]
        wait, dl, verify = _happy_path()
        for i in range(2):
            with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
                 patch.object(_shared, "fal_queue_submit", return_value={"request_id": f"req-15-{i}"}), \
                 wait, dl, verify:
                result = KlingReferenceVideo().execute(
                    _inputs(env, **dict(inputs, output_path=str(env / "assets" / "video" / f"t{i}.mp4"))))
            assert result.success, result.error
        for reservation in load_reservations(env).values():
            assert "scope" not in reservation

    def test_a_call_with_no_project_scope_reserves_as_before(self, shot):
        """``reserve_paid_call`` without ``inputs`` is exactly today's call."""
        rid = reserve_paid_call(
            make_tracker(shot["project"]), shot["project"], tool="t", endpoint="e",
            normalized_inputs_hash="cd" * 32, reserved_usd=0.01,
        )
        reservation = load_reservations(shot["project"])[rid]
        assert "scope" not in reservation and "kind" not in reservation

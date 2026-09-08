"""Task 4 — the human reconciler runs on a look-governed, shot-ready project.

``scripts/reconcile_paid_calls.py`` generates nothing: it polls the provider
for jobs that already left the machine and settles their reservations. It
must therefore NOT be subject to look governance (which refuses any governed
visual call carrying no verified ``look_refs``) and must keep its
``check_resume=False`` privilege — it is the one caller allowed past a
nonterminal reservation, because reconciling IS the resume path.

These tests exercise the reconciler on a 1.6-pinned (shot-ready, and
therefore look-governed) project whose reservation is scoped to a shot, and
assert the C2 accounting in ``lib.shot_allowance`` moves only on a CONFIRMED
non-acceptance (constraint 15).

All network is mocked. Real signed receipts against a per-test gates dir.
Invented ids only.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

from lib.shot_allowance import scope_usage, shot_scope
from lib.state_io import read_jsonl
from scripts import reconcile_paid_calls as rc
from tools.cost_tracker import load_reservations, reconcile_paid_call, reserve_paid_call
from tools.video import _shared

from tests.tools._authored_film_helpers import (
    make_tracker,
    make_verified_project,
    project_tracker,
    write_scene_plan_checkpoint,
)
from tests.tools.test_shot_ready_ownership import pin

SHOT = "sh-alpha"
ENDPOINT = "fal-ai/kling-video/o3/pro/reference-to-video"


def _project(tmp_path, monkeypatch, slug: str, *, version: str) -> Path:
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, slug, budget=10.0)
    pin(project, version)
    return project


@pytest.fixture
def shot_project(tmp_path, monkeypatch) -> Path:
    """A shot-ready (1.6) project — look-governed by its pin — with one shot
    whose approved package allows $5 and three video takes."""
    project = _project(tmp_path, monkeypatch, "proj-shot-recon", version="1.6")
    write_scene_plan_checkpoint(
        project,
        [{"shot_id": SHOT, "description": "Hall, slow push in.",
          "spend_allowance_usd": 5.0, "max_video_takes": 3}],
        version="1.6",
    )
    return project


def _reserve_take(project: Path, *, usd: float, request_id: str | None = None,
                  state: str | None = None, kind: str = "video") -> str:
    """A reservation attributed to the shot, made the way a paid tool makes it."""
    from tools.cost_tracker import attach_request_id

    tracker = make_tracker(project, budget=10.0)
    rid = reserve_paid_call(
        tracker, project, tool="kling_reference_video", endpoint=ENDPOINT,
        normalized_inputs_hash="h" * 64, reserved_usd=usd,
        inputs={"project_dir": str(project), "shot_id": SHOT}, kind=kind,
    )
    if request_id:
        attach_request_id(project, rid, request_id)
    if state:
        reconcile_paid_call(project, rid, usd, state, tracker)
    return rid


def _usage(project: Path) -> tuple[float, int]:
    return scope_usage(load_reservations(project), shot_scope(SHOT))


def _status(body):
    class _R:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return body

    return _R()


def test_reservation_is_scoped_to_the_shot(shot_project):
    rid = _reserve_take(shot_project, usd=1.25)
    reservation = load_reservations(shot_project)[rid]
    assert reservation["scope"] == shot_scope(SHOT) and reservation["kind"] == "video"
    assert _usage(shot_project) == (1.25, 1)


def test_submitting_without_provider_id_is_left_alone_then_released_by_hand(shot_project, capsys):
    """(a) A missing request id is not proof the job never left (constraint 15):
    the reconciler instructs the human and releases nothing. Only the
    human-confirmed ``failed`` frees the shot's dollars and its take."""
    rid = _reserve_take(shot_project, usd=1.5)
    with patch.object(_shared, "fal_queue_submit") as submit, patch("requests.post") as post:
        summary = rc.reconcile_project(shot_project, api_key="test-key")
    submit.assert_not_called()
    post.assert_not_called()
    assert summary["manual"] == [rid] and summary["failed"] == [] and summary["completed"] == []
    assert load_reservations(shot_project)[rid]["state"] == "submitting"
    assert _usage(shot_project) == (1.5, 1)  # still outstanding against the shot
    out = capsys.readouterr().out
    assert "state=submitting, no provider request id" in out
    assert "Never resubmit." in out and "state='failed'" in out

    # The human checks the dashboard, finds nothing, and records it by hand.
    reconcile_paid_call(shot_project, rid, 0.0, "failed", project_tracker(shot_project))
    settled = load_reservations(shot_project)[rid]
    assert settled["state"] == "failed" and settled["scope"] == shot_scope(SHOT)
    assert _usage(shot_project) == (0.0, 0)  # dollars AND the take released


def test_pending_billing_with_id_settles_completed_and_stays_attributed(shot_project, capsys):
    """(b) The provider accepted and finished: the reservation settles
    ``completed`` and its spend stays attributed to the shot's scope."""
    rid = _reserve_take(shot_project, usd=2.0, request_id="req-shot-done", state="pending_billing")
    with patch("requests.get", return_value=_status({"status": "COMPLETED"})) as get, \
         patch.object(_shared, "fal_queue_submit") as submit:
        summary = rc.reconcile_project(shot_project, api_key="test-key")
    submit.assert_not_called()
    assert get.call_args.args[0].endswith("/status")
    assert summary["completed"] == [rid]
    settled = load_reservations(shot_project)[rid]
    assert settled["state"] == "completed" and settled["actual_usd"] == 2.0
    assert settled["scope"] == shot_scope(SHOT) and settled["kind"] == "video"
    assert _usage(shot_project) == (2.0, 1)  # a completed take is consumed, never released
    assert project_tracker(shot_project).budget_spent_usd == pytest.approx(2.0)


def test_provider_rejection_settles_failed_and_releases_the_shot(shot_project, monkeypatch):
    """(c) A 400/422 result means nothing was rendered or billed: confirmed
    non-acceptance, so the shot gets its dollars and its take back."""
    rid = _reserve_take(shot_project, usd=1.75, request_id="req-shot-400", state="pending_billing")
    monkeypatch.setattr(rc, "fal_queue_status", lambda *a, **k: {"status": "COMPLETED"})

    def reject(*a, **k):
        resp = _requests.Response()
        resp.status_code = 400
        resp._content = b'{"detail":[{"type":"invalid_reference"}]}'
        raise _requests.HTTPError("400", response=resp)

    monkeypatch.setattr(rc, "recover_output", reject)
    summary = rc.reconcile_project(shot_project, api_key="test-key")
    assert summary["failed"] == [rid]
    settled = load_reservations(shot_project)[rid]
    assert settled["state"] == "failed" and settled["scope"] == shot_scope(SHOT)
    assert _usage(shot_project) == (0.0, 0)


def test_reconciler_runs_look_governance_free_and_creates_no_reservation(shot_project):
    """(e) Look governance is the wrong check for a caller that generates
    nothing, and the reconciler never opens a new paid call."""
    _reserve_take(shot_project, usd=0.5, request_id="req-shot-run", state="pending_billing")
    before = {e.get("reservation_id") for e in read_jsonl(shot_project / "cost-reservations.jsonl")}
    governance = MagicMock(side_effect=AssertionError("the reconciler must not run look governance"))
    reserve = MagicMock(side_effect=AssertionError("the reconciler must not reserve"))
    with patch.object(_shared, "verify_look_governance", governance), \
         patch("tools.cost_tracker.reserve_paid_call", reserve), \
         patch("requests.get", return_value=_status({"status": "IN_PROGRESS"})):
        rc.reconcile_project(shot_project, api_key="test-key")
    governance.assert_not_called()
    reserve.assert_not_called()
    after = {e.get("reservation_id") for e in read_jsonl(shot_project / "cost-reservations.jsonl")}
    assert after == before


def test_look_governed_project_below_shot_ready_reconciles_as_before(tmp_path, monkeypatch, capsys):
    """(d) A 1.5 project — look-governed, no shot scopes — is unchanged: a
    terminal provider failure refunds the reservation exactly as it always did."""
    project = _project(tmp_path, monkeypatch, "proj-1-5-recon", version="1.5")
    tracker = make_tracker(project, budget=10.0)
    from tools.cost_tracker import attach_request_id

    rid = reserve_paid_call(tracker, project, tool="kling_reference_video", endpoint=ENDPOINT,
                            normalized_inputs_hash="h" * 64, reserved_usd=0.8)
    attach_request_id(project, rid, "req-plain")
    reconcile_paid_call(project, rid, 0.8, "pending_billing", tracker)
    with patch("requests.get", return_value=_status({"status": "FAILED"})):
        summary = rc.reconcile_project(project, api_key="test-key")
    assert summary["failed"] == [rid]
    settled = load_reservations(project)[rid]
    assert settled["state"] == "failed" and "scope" not in settled
    assert project_tracker(project).budget_spent_usd == 0.0
    assert "reservation refunded" in capsys.readouterr().out


def test_reconcile_call_context_is_reconciler_only(shot_project):
    """The reconciler context resolves root, verified config and a CAP tracker —
    and returns no governance dict, so it cannot stand in for a generation
    context. Path safety is the same as ``paid_call_context``'s."""
    root, tracker, config = _shared.reconcile_call_context(shot_project)
    assert root == shot_project.resolve()
    assert tracker.cost_log_path == shot_project / "cost_log.json"
    assert float(config.budget_usd_cap) == 10.0
    stray = shot_project.parent.parent / "stray-not-registered"
    stray.mkdir(exist_ok=True)
    with pytest.raises(_shared.PaidCallContextError):
        _shared.reconcile_call_context(stray)

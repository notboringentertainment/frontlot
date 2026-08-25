import pytest

from lib.config_model import BudgetMode
from tools import cost_tracker as ct
from tools.cost_tracker import (
    BudgetExceededError,
    CostTracker,
    IndeterminatePaidCallError,
    attach_request_id,
    indeterminate_reservations,
    load_reservations,
    reconcile_paid_call,
    reserve_paid_call,
    resume_check,
)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _tracker(project, budget=1.0):
    return CostTracker(
        budget_total_usd=budget,
        reserve_pct=0.0,
        single_action_approval_usd=100.0,
        require_approval_for_new_paid_tool=False,
        mode=BudgetMode.CAP,
        cost_log_path=project / "cost_log.json",
    )


CALL = dict(tool="image_gen", endpoint="fal-ai/example/edit", normalized_inputs_hash="ab" * 32)


def test_cap_exceeded_writes_no_reservation(project):
    tracker = _tracker(project, budget=0.10)
    with pytest.raises(BudgetExceededError):
        reserve_paid_call(tracker, project, reserved_usd=0.50, **CALL)
    assert not ct.reservations_path(project).exists()
    assert tracker.budget_reserved_usd == 0.0
    resume_check(project)  # nothing indeterminate


def test_reservation_persisted_as_submitting_before_network(project):
    tracker = _tracker(project)
    rid = reserve_paid_call(tracker, project, reserved_usd=0.25, **CALL)
    state = load_reservations(project)[rid]
    assert state["state"] == "submitting"
    assert state["provider_request_id"] is None
    assert state["idempotency_key"] == rid
    assert state["normalized_inputs_hash"] == CALL["normalized_inputs_hash"]
    assert tracker.budget_reserved_usd == pytest.approx(0.25)


def test_crash_after_submit_without_request_id_halts_on_resume(project):
    tracker = _tracker(project)
    rid = reserve_paid_call(tracker, project, reserved_usd=0.25, **CALL)
    # simulate crash: process dies here, nothing else recorded
    with pytest.raises(IndeterminatePaidCallError) as exc:
        resume_check(project)
    assert [r["reservation_id"] for r in exc.value.reservations] == [rid]
    assert "resubmit" in str(exc.value).lower()
    assert indeterminate_reservations(project)[0]["reservation_id"] == rid


def test_attach_then_reconcile_is_clean(project):
    tracker = _tracker(project)
    rid = reserve_paid_call(tracker, project, reserved_usd=0.25, **CALL)
    attach_request_id(project, rid, "req-777")
    assert indeterminate_reservations(project) == []
    reconcile_paid_call(project, rid, 0.21, "completed", tracker=tracker)
    state = load_reservations(project)[rid]
    assert state["state"] == "completed"
    assert state["provider_request_id"] == "req-777"
    assert state["actual_usd"] == pytest.approx(0.21)
    assert tracker.budget_reserved_usd == 0.0
    assert tracker.budget_spent_usd == pytest.approx(0.21)
    resume_check(project)

    # Reloading from disk sees the same state (survives restart)
    reloaded = CostTracker(cost_log_path=project / "cost_log.json", mode=BudgetMode.CAP)
    assert reloaded.budget_spent_usd == pytest.approx(0.21)


def test_failed_call_with_request_id_is_not_indeterminate(project):
    tracker = _tracker(project)
    rid = reserve_paid_call(tracker, project, reserved_usd=0.25, **CALL)
    attach_request_id(project, rid, "req-1")
    reconcile_paid_call(project, rid, 0.0, "failed", tracker=tracker)
    assert load_reservations(project)[rid]["state"] == "failed"
    resume_check(project)


def test_unknown_reservation_and_bad_state_raise(project):
    with pytest.raises(KeyError):
        attach_request_id(project, "nope", "req")
    with pytest.raises(KeyError):
        reconcile_paid_call(project, "nope", 0.0)
    tracker = _tracker(project)
    rid = reserve_paid_call(tracker, project, reserved_usd=0.1, **CALL)
    with pytest.raises(ValueError):
        reconcile_paid_call(project, rid, 0.0, state="maybe")
    with pytest.raises(ValueError):
        attach_request_id(project, rid, "")


def test_constructor_cap_is_authoritative_over_saved_log(project):
    old = _tracker(project, budget=100.0)
    entry = old.estimate("image_gen", "generate", 2.0)
    old.reserve(entry)
    assert (project / "cost_log.json").exists()

    reopened = _tracker(project, budget=10.0)
    assert reopened.budget_total_usd == 10.0
    assert reopened.budget_reserved_usd == 2.0  # spend state still loads
    assert len(reopened.entries) == 1

"""D19 step 3: qc_call_context refusals, cost-log transaction, pre-submit hook."""
import json
import multiprocessing as mp
from pathlib import Path

import pytest

from lib import qc_receipts as qr
from lib.project_config import ProjectConfigError
from tests.lib.d19_helpers import JUDGE_MODEL, config_1_1, make_qc_project, series_key, sign_config
from tools.cost_tracker import BudgetExceededError, CostTracker
from tools.video import _shared


def _inputs(project: Path, attempt_id: str | None) -> dict:
    d = {"project_dir": str(project)}
    if attempt_id:
        d["attempt_id"] = attempt_id
    return d


def test_context_requires_open_attempt_and_qc_manifest(tmp_path, monkeypatch):
    project = make_qc_project(tmp_path, monkeypatch)
    with pytest.raises(_shared.QCCallContextError, match="attempt_id"):
        _shared.qc_call_context(_inputs(project, None))
    with pytest.raises(_shared.QCCallContextError, match="no signed attempt_started"):
        _shared.qc_call_context(_inputs(project, "nope"))
    a = qr.start_attempt(project, series_key(project.name), max_attempts=3)
    root, tracker, config, qc, started = _shared.qc_call_context(_inputs(project, a["attempt_id"]))
    assert root == project.resolve() and qc.judge_model == JUDGE_MODEL and started["attempt_id"] == a["attempt_id"]
    # verdict attached → closed
    qr.attach_verdict(project, a["attempt_id"], qc_receipt_id="v-1")
    with pytest.raises(_shared.QCCallContextError, match="already has a verdict"):
        _shared.qc_call_context(_inputs(project, a["attempt_id"]))


def test_context_refuses_1_2_pin_and_missing_egress(tmp_path, monkeypatch):
    project = make_qc_project(tmp_path, monkeypatch, version="1.2")
    a = qr.start_attempt(project, series_key(project.name), max_attempts=3)
    with pytest.raises(_shared.QCCallContextError, match="authored-film 1.3"):
        _shared.qc_call_context(_inputs(project, a["attempt_id"]))
    (tmp_path / "two").mkdir()
    project2 = make_qc_project(tmp_path / "two", monkeypatch, slug="proj-ferry", config=config_1_1(openai_classes=("prompts",)))
    a2 = qr.start_attempt(project2, series_key(project2.name), max_attempts=3)
    with pytest.raises(ProjectConfigError, match="generated_sheet_images"):
        _shared.qc_call_context(_inputs(project2, a2["attempt_id"]))


def test_context_refuses_attempt_from_other_judge_or_policy(tmp_path, monkeypatch):
    project = make_qc_project(tmp_path, monkeypatch)
    a = qr.start_attempt(project, series_key(project.name, judge_model="other-judge"), max_attempts=3)
    with pytest.raises(_shared.QCCallContextError, match="different judge or policy"):
        _shared.qc_call_context(_inputs(project, a["attempt_id"]))


def test_1_0_config_has_no_qc(tmp_path, monkeypatch):
    from tests.tools._authored_film_helpers import make_verified_project
    project = make_verified_project(tmp_path, monkeypatch)
    from tests.lib.d19_helpers import pin
    pin(project, project.name, "1.3")
    a = qr.start_attempt(project, series_key(project.name), max_attempts=3)
    with pytest.raises(ProjectConfigError, match="no qc block"):
        _shared.qc_call_context(_inputs(project, a["attempt_id"]))


# ---- cost-log transaction ----

def _reserve_many(path: str, n: int) -> None:
    t = CostTracker(budget_total_usd=100.0, reserve_pct=0.0, single_action_approval_usd=1e9,
                    require_approval_for_new_paid_tool=False, cost_log_path=Path(path))
    for _ in range(n):
        eid = t.estimate("tool", "op", 1.0)
        t.reserve(eid)


def test_cost_log_transaction_survives_two_processes(tmp_path):
    log = tmp_path / "cost_log.json"
    ctx = mp.get_context("fork")
    ps = [ctx.Process(target=_reserve_many, args=(str(log), 25)) for _ in range(2)]
    [p.start() for p in ps]; [p.join() for p in ps]
    data = json.loads(log.read_text())
    assert len(data["entries"]) == 50
    assert round(sum(e["reserved_usd"] for e in data["entries"]), 2) == 50.0


def test_cap_is_atomic_under_reload(tmp_path):
    log = tmp_path / "cost_log.json"
    a = CostTracker(budget_total_usd=2.0, reserve_pct=0.0, single_action_approval_usd=1e9,
                    require_approval_for_new_paid_tool=False, mode=__import__("lib.config_model", fromlist=["BudgetMode"]).BudgetMode.CAP,
                    cost_log_path=log)
    b = CostTracker(budget_total_usd=2.0, reserve_pct=0.0, single_action_approval_usd=1e9,
                    require_approval_for_new_paid_tool=False, mode=a.mode, cost_log_path=log)
    a.reserve(a.estimate("t", "o", 1.5))
    with pytest.raises(BudgetExceededError):
        b.reserve(b.estimate("t", "o", 1.0))   # b reloads a's reservation before checking


# ---- pre-submit hook ----

def test_pre_submit_hook_runs_only_inside_context(tmp_path):
    calls = []
    _shared.run_pre_submit(tmp_path, "r0")
    with _shared.pre_submit_hook(lambda root, rid: calls.append((root, rid))):
        _shared.run_pre_submit(tmp_path, "r1")
    _shared.run_pre_submit(tmp_path, "r2")
    assert calls == [(tmp_path, "r1")]

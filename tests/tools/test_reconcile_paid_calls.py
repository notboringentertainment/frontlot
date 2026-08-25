"""Reservation states + the offline reconciler (mocked FAL, never resubmits)."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.pathsafe import sha256_file
from lib.receipts import find_generation
from scripts import reconcile_paid_calls as rc
from tools.cost_tracker import (
    IndeterminatePaidCallError,
    attach_request_id,
    load_reservations,
    nonterminal_reservations,
    reconcile_paid_call,
    reserve_paid_call,
    resume_check,
)
from tools.video import _shared

from tests.tools._authored_film_helpers import make_tracker, make_verified_project, project_tracker, tiny_png_bytes


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    return make_verified_project(tmp_path, monkeypatch, "proj-ledger", budget=10.0)


def _reserve(project, usd=1.0, request_id=None, state=None, hint=None, tool="seedance_video",
             endpoint="bytedance/seedance-2.5/reference-to-video"):
    tracker = make_tracker(project, budget=10.0)
    rid = reserve_paid_call(tracker, project, tool=tool, endpoint=endpoint,
                            normalized_inputs_hash="h" * 64, reserved_usd=usd, output_hint=hint)
    if request_id:
        attach_request_id(project, rid, request_id)
    if state:
        reconcile_paid_call(project, rid, usd, state, tracker)
    return rid


def test_resume_check_blocks_on_every_nonterminal_state(env):
    a = _reserve(env)                                   # submitting, no id
    b = _reserve(env, request_id="req-b")               # submitting, id (crash after attach)
    c = _reserve(env, request_id="req-c", state="pending_billing")
    d = _reserve(env, request_id="req-d", state="completed")
    e = _reserve(env, request_id="req-e", state="failed")
    assert {r["reservation_id"] for r in nonterminal_reservations(env)} == {a, b, c}
    with pytest.raises(IndeterminatePaidCallError) as info:
        resume_check(env)
    assert set(info.value.reservations and [r["reservation_id"] for r in info.value.reservations]) == {a, b, c}
    assert "pending_billing" in str(info.value) and "Never resubmit" in str(info.value)
    for rid in (d, e):
        with pytest.raises(ValueError, match="already"):
            reconcile_paid_call(env, rid, 0.0, "failed")
    with pytest.raises(ValueError, match="state must be"):
        reconcile_paid_call(env, a, 0.0, "lost")


def test_pending_billing_retains_charge_until_reconciled(env):
    rid = _reserve(env, usd=2.5, request_id="req-p", state="pending_billing")
    t = project_tracker(env)
    assert t.budget_spent_usd == pytest.approx(2.5) and t.budget_reserved_usd == 0.0
    reconcile_paid_call(env, rid, 0.0, "failed", t)
    assert t.budget_spent_usd == 0.0
    assert load_reservations(env)[rid]["state"] == "failed"


def _status(body):
    class _R:
        content = b"{}"
        def raise_for_status(self): pass
        def json(self): return body
    return _R()


def test_reconciler_settles_completed_failed_running_and_manual(env, capsys):
    video_out = env / "assets" / "video" / "shot.mp4"
    video_out.parent.mkdir(parents=True)
    done_video = _reserve(env, usd=1.5, request_id="req-done", state="pending_billing",
                          hint={"kind": "video", "output_path": str(video_out), "generate_audio": True})
    done_image = _reserve(env, usd=0.2, request_id="req-img", tool="seedream_image",
                          endpoint="bytedance/seedream/v5/pro/edit",
                          hint={"kind": "image", "objects_dir": str(env / "canon" / "visual" / "objects")})
    dead = _reserve(env, usd=0.7, request_id="req-dead", state="pending_billing")
    running = _reserve(env, usd=0.3, request_id="req-run")
    manual = _reserve(env, usd=0.4)
    statuses = {"req-done": "COMPLETED", "req-img": "COMPLETED", "req-dead": "FAILED", "req-run": "IN_PROGRESS"}
    seen_urls = []

    def fake_get(url, headers=None, timeout=None):
        seen_urls.append((url, headers))
        return _status({"status": statuses[url.rsplit("/", 2)[1]]})

    def fake_wait(model_id, request_id, *, api_key, deadline_s, poll_s=5.0, **kw):
        if request_id == "req-done":
            return {"video": {"url": "https://v3.fal.media/done.mp4"}}
        return {"images": [{"url": "https://v3.fal.media/img.png"}]}

    def fake_download(url, dest, **kw):
        Path(dest).write_bytes(b"real-mp4" if url.endswith(".mp4") else tiny_png_bytes())
        return {"bytes": 1, "content_type": kw["allowed_mime_prefixes"][0] + "x"}

    with patch("requests.get", side_effect=fake_get), patch("requests.post") as post, \
         patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "fal_queue_wait", side_effect=fake_wait), \
         patch.object(_shared, "fal_download", side_effect=fake_download), \
         patch.object(_shared, "verify_video_file", return_value={"video_codec": "h264"}) as verify:
        summary = rc.reconcile_project(env, api_key="test-key")
    post.assert_not_called()
    submit.assert_not_called()  # never resubmits
    assert all(u.startswith("https://queue.fal.run/") and u.endswith("/status") for u, _ in seen_urls)
    assert all(h["X-Fal-No-Retry"] == "1" for _, h in seen_urls)
    assert summary == {"completed": [done_video, done_image], "failed": [dead], "running": [running], "manual": [manual]}
    verify.assert_called_once()
    assert verify.call_args.kwargs == {"require_audio": True}
    res = load_reservations(env)
    assert res[done_video]["state"] == "completed" and res[done_video]["actual_usd"] == 1.5
    assert res[done_image]["state"] == "completed"
    assert res[dead]["state"] == "failed" and res[dead]["actual_usd"] == 0.0
    assert res[running]["state"] == "submitting" and res[manual]["state"] == "submitting"
    assert video_out.read_bytes() == b"real-mp4"
    assert find_generation(env, sha256_file(video_out))["provider_request_id"] == "req-done"
    stored = list((env / "canon" / "visual" / "objects").glob("*.png"))
    assert len(stored) == 1 and find_generation(env, sha256_file(stored[0]))["provider_request_id"] == "req-img"
    t = project_tracker(env)
    assert t.budget_spent_usd == pytest.approx(1.5 + 0.2)
    assert t.budget_reserved_usd == pytest.approx(0.3 + 0.4)
    out = capsys.readouterr().out
    assert "[manual]" in out and "Never resubmit" in out and "[running]" in out
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


def test_reconciler_skips_download_when_output_present_and_needs_key_for_ids(env):
    video_out = env / "assets" / "video" / "have.mp4"
    video_out.parent.mkdir(parents=True)
    video_out.write_bytes(b"already")
    rid = _reserve(env, usd=1.0, request_id="req-have", state="pending_billing",
                   hint={"kind": "video", "output_path": str(video_out)})
    with patch("requests.get", return_value=_status({"status": "COMPLETED"})), \
         patch.object(_shared, "fal_download") as dl:
        summary = rc.reconcile_project(env, api_key="test-key")
    dl.assert_not_called()
    assert summary["completed"] == [rid] and video_out.read_bytes() == b"already"
    _reserve(env, request_id="req-needs-key")
    with pytest.raises(rc.ReconcileError, match="FAL_KEY"):
        rc.reconcile_project(env, api_key=None)


def test_reconciler_requires_registered_verified_project_and_no_tty(env, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # not a TTY: read-only reconciliation is allowed
    assert rc.main(["--project", "proj-ledger"]) == 0
    assert rc.main(["--project", "proj-nope"]) == 2
    (env / "project.yaml").write_text((env / "project.yaml").read_text() + "# edited\n")
    assert rc.main(["--project", "proj-ledger"]) == 1
    # a stray (unregistered) directory cannot be reconciled either
    stray = tmp_path.parent / f"{tmp_path.name}-stray"
    stray.mkdir()
    with pytest.raises(_shared.PaidCallContextError):
        rc.reconcile_project(stray, api_key="k")

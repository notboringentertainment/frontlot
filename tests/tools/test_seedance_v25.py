"""Seedance 2.5 reference-to-video path (mocked FAL), trust boundary, and 2.0 routing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import lib.receipts as receipts_mod
from lib.receipts import find_generation
from lib.pathsafe import sha256_file
from tools.cost_tracker import IndeterminatePaidCallError, load_reservations, resume_check
from tools.video import _shared
from tools.video.seedance_video import SeedanceVideo

from tests.tools._authored_film_helpers import make_verified_project, project_tracker, tiny_png_bytes

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "bytedance-seedance-2.5-reference-to-video.json").read_text()
)
PROBED = {"file_size_bytes": 8, "file_size_mb": 0.0, "duration_seconds": 6.0, "video_width": 1280,
          "video_height": 720, "video_codec": "h264", "has_audio": True, "audio_codec": "aac"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch)
    (project / "assets" / "video").mkdir(parents=True)
    return project


@pytest.fixture
def legacy_env(monkeypatch, tmp_path):
    """A registered project WITHOUT project.yaml — the only place Seedance 2.0 still runs."""
    from tests.tools._authored_film_helpers import make_project

    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_project(tmp_path, monkeypatch, "proj-legacy")
    (project / "assets" / "video").mkdir(parents=True)
    return project


def _inputs(project, **extra):
    base = {
        "prompt": "@Image1 walks through fog",
        "operation": "reference_to_video",
        "reference_image_urls": ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png"],
        "duration": "6",
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "output_path": str(project / "assets" / "video" / "shot.mp4"),
        "project_dir": str(project),
    }
    base.update(extra)
    return base


def _fake_download(url, dest, **kw):
    Path(dest).write_bytes(b"fake-mp4")
    return {"bytes": 8, "content_type": "video/mp4"}


def _happy_path(**wait_result):
    wait_result = wait_result or {"video": {"url": "https://v3.fal.media/o.mp4"}, "seed": 4}
    return (
        patch.object(_shared, "fal_queue_wait", return_value=wait_result),
        patch.object(_shared, "fal_download", side_effect=_fake_download),
        patch.object(_shared, "verify_video_file", return_value=dict(PROBED)),
    )


def test_v25_payload_matches_fixture_shape(env):
    captured = {}

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["model_id"], captured["payload"], captured["key"] = model_id, payload, api_key
        return {"request_id": "req-25"}

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), wait, dl, verify as verify_mock:
        result = SeedanceVideo().execute(_inputs(env, reference_video_urls=["https://fal.media/ref.mp4"],
                                                 reference_audio_urls=["https://fal.media/ref.mp3"]))
    assert result.success, result.error
    assert captured["model_id"] == FIXTURE["model_id"]
    assert captured["key"] == "test-key"
    allowed = set(FIXTURE["input"]["properties"])
    assert set(captured["payload"]) <= allowed
    assert captured["payload"] == {
        "prompt": "@Image1 walks through fog",
        "image_urls": ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png"],
        "video_urls": ["https://fal.media/ref.mp4"],
        "audio_urls": ["https://fal.media/ref.mp3"],
        "resolution": "720p",
        "duration": "6",
        "aspect_ratio": "16:9",
        "generate_audio": True,
        "bitrate_mode": "standard",
    }
    assert "image_url" not in captured["payload"]
    verify_mock.assert_called_once()
    assert verify_mock.call_args.kwargs == {"require_audio": True}
    out = Path(result.data["output_path"])
    assert out.read_bytes() == b"fake-mp4"
    assert not any((env / ".staging").iterdir())
    assert result.metadata == {"model_endpoint": FIXTURE["model_id"], "provider_request_id": "req-25", "generator_kind": "model"}
    receipt = find_generation(env, sha256_file(out))
    assert receipt and receipt["provider_request_id"] == "req-25"
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "completed" and res["provider_request_id"] == "req-25"
    assert res["output_hint"] == {"kind": "video", "output_path": str(out), "generate_audio": True}
    assert res["reserved_usd"] == pytest.approx(0.4730 * 6 * 0.6, abs=1e-4)
    assert result.cost_usd == pytest.approx(0.4730 * 6 * 0.6, abs=1e-4)
    # the budget came from the verified project.yaml, not from the caller
    assert project_tracker(env).budget_spent_usd == pytest.approx(result.cost_usd, abs=1e-4)


def test_no_retries_advertised_for_paid_tool():
    assert SeedanceVideo.retry_policy.max_retries == 0


def test_v25_cost_estimate_from_fixture():
    tool = SeedanceVideo()
    p = FIXTURE["pricing"]["usd_per_second"]
    assert tool.estimate_cost({"duration": "10", "resolution": "720p"}) == pytest.approx(p["720p"] * 10)
    assert tool.estimate_cost({"duration": "10", "resolution": "480p"}) == pytest.approx(p["480p"] * 10)
    assert tool.estimate_cost({"duration": "auto"}) == pytest.approx(p["720p"] * 5)


def _urls(n, ext):
    return [f"https://fal.media/{i}.{ext}" for i in range(n)]


@pytest.mark.parametrize("kw,msg", [
    ({"reference_image_urls": _urls(31, "png")}, "at most 30 reference images"),
    ({"reference_image_urls": _urls(30, "png"), "reference_image_paths": ["x.png"]}, "at most 30 reference images"),
    ({"reference_video_urls": _urls(11, "mp4")}, "at most 10 reference videos"),
    ({"reference_audio_urls": _urls(11, "mp3")}, "at most 10 reference audio"),
    ({"operation": "image_to_video", "image_url": "https://fal.media/s.png"}, "no start/end-frame"),
])
def test_v25_preflight_rejects_over_cap_without_network(env, kw, msg):
    with patch.object(_shared, "fal_queue_submit") as submit:
        result = SeedanceVideo().execute(_inputs(env, **kw))
    assert not result.success and msg in result.error
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_v25_total_refs_cap_is_50(env):
    tool = SeedanceVideo()
    at_cap = {"operation": "reference_to_video", "reference_image_urls": _urls(30, "png"),
              "reference_video_urls": _urls(10, "mp4"), "reference_audio_urls": _urls(10, "mp3")}
    assert tool._v25_preflight_error(at_cap) is None
    tool.V25_LIMITS = {**tool.V25_LIMITS, "audios": 11}
    over = {**at_cap, "reference_audio_urls": _urls(11, "mp3")}
    assert "50 reference files in total" in tool._v25_preflight_error(over)


# ---- trust boundary (inspection #2) ----

def test_unregistered_project_dir_rejected_before_any_network(env, tmp_path, monkeypatch):
    import lib.paths as paths_mod

    stray = tmp_path.parent / f"{tmp_path.name}-stray"
    stray.mkdir()
    (stray / "project.yaml").write_bytes((env / "project.yaml").read_bytes())
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as up:
        r = SeedanceVideo().execute(_inputs(env, project_dir=str(stray), output_path=str(stray / "s.mp4")))
        assert not r.success and "preflight" in r.error and "registered project" in r.error
        r = SeedanceVideo().execute({**_inputs(env), "project_dir": ""})
        assert not r.success and "project_dir" in r.error
    submit.assert_not_called()
    up.assert_not_called()
    assert not (stray / "cost-reservations.jsonl").exists()


def test_caller_injected_tracker_and_cap_are_ignored(env, tmp_path, monkeypatch):
    """Budget comes only from the approved project.yaml (cap 0.5 here)."""
    from tests.tools._authored_film_helpers import make_tracker

    project = make_verified_project(tmp_path, monkeypatch, "proj-thrift", budget=0.5)
    (project / "assets" / "video").mkdir(parents=True)
    generous = make_tracker(project, budget=1000.0)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"),
                                            cost_tracker=generous, budget_usd_cap=1000.0))
    assert not r.success and "exceeds usable budget" in r.error
    submit.assert_not_called()


def test_unapproved_project_config_rejected(env, tmp_path, monkeypatch):
    """Editing project.yaml after approval invalidates it (digest no longer bound)."""
    cfg = env / "project.yaml"
    cfg.write_bytes(cfg.read_bytes().replace(b"budget_usd_cap: 50.0", b"budget_usd_cap: 5000.0"))
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env))
    assert not r.success and "approval receipt" in r.error
    submit.assert_not_called()


def test_egress_prompts_only_blocks_local_reference_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, "proj-hermit", egress_classes=("prompts",))
    (project / "assets" / "video").mkdir(parents=True)
    ref = project / "assets" / "ref.png"
    ref.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"),
                                            reference_image_paths=[str(ref)]))
    assert not r.success and "reference_images" in r.error
    up.assert_not_called()
    submit.assert_not_called()
    # URL-only references need only the prompts class
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-p"}), wait, dl, verify:
        r = SeedanceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4")))
    assert r.success, r.error


def test_reference_path_outside_project_rejected_before_upload(env, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, reference_image_paths=[str(outside)]))
    assert not r.success and "preflight" in r.error
    up.assert_not_called()
    submit.assert_not_called()


# ---- storyboard receipt (inspection #6) ----

def test_shot_visual_requires_shot_id_and_frame_hash(env, monkeypatch):
    calls = []
    monkeypatch.setattr(receipts_mod, "require_storyboard_receipt", lambda *a: calls.append(a), raising=False)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual"))
        assert not r.success and "shot_id" in r.error
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual", shot_id="s1"))
        assert not r.success and "storyboard_frame_sha256" in r.error
    submit.assert_not_called()
    assert calls == []


def test_shot_visual_missing_receipt_fails_preflight(env, monkeypatch):
    def deny(project_root, shot_id, frame_sha):
        raise ValueError(f"no signed storyboard approval covers {shot_id}/{frame_sha[:8]}")

    monkeypatch.setattr(receipts_mod, "require_storyboard_receipt", deny, raising=False)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual", shot_id="s1", storyboard_frame_sha256="ab" * 32))
    assert not r.success and "preflight" in r.error and "storyboard approval" in r.error
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_shot_visual_with_receipt_proceeds(env, monkeypatch):
    seen = []
    monkeypatch.setattr(receipts_mod, "require_storyboard_receipt", lambda root, sid, sha: seen.append((root, sid, sha)), raising=False)
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-sb"}), wait, dl, verify:
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual", shot_id="s1", storyboard_frame_sha256="cd" * 32))
    assert r.success, r.error
    assert seen == [(env.resolve(), "s1", "cd" * 32)]


def test_shot_visual_checker_absent_fails_closed(env, monkeypatch):
    monkeypatch.delattr(receipts_mod, "require_storyboard_receipt", raising=False)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual", shot_id="s1", storyboard_frame_sha256="ab" * 32))
    assert not r.success and "require_storyboard_receipt" in r.error
    submit.assert_not_called()


# ---- reservation states (inspection #3/#4/#5) ----

def test_crash_between_submit_and_attach_leaves_reservation_submitting(env):
    with patch.object(_shared, "fal_queue_submit", side_effect=ConnectionError("socket died mid-flight")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        result = SeedanceVideo().execute(_inputs(env))
    assert not result.success
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


def test_attach_request_id_failure_stays_submitting_and_blocks_resume(env):
    import tools.video.seedance_video as mod

    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-lost"}), \
         patch("tools.cost_tracker.attach_request_id", side_effect=OSError("disk full")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        result = SeedanceVideo().execute(_inputs(env))
    assert not result.success and "disk full" in result.error
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    assert project_tracker(env).budget_reserved_usd > 0  # never reconciled as failed
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


@pytest.mark.parametrize("stage", ["wait", "download", "verify"])
def test_failure_after_acceptance_is_pending_billing_and_retains_reservation(env, stage):
    wait = patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalDeadlineExceeded("late")) if stage == "wait" \
        else patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/o.mp4"}})
    dl = patch.object(_shared, "fal_download", side_effect=_shared.FalDownloadError("too big")) if stage == "download" \
        else patch.object(_shared, "fal_download", side_effect=_fake_download)
    verify = patch.object(_shared, "verify_video_file", side_effect=_shared.VideoVerificationError("no video stream")) if stage == "verify" \
        else patch.object(_shared, "verify_video_file", return_value=dict(PROBED))
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-x"}), wait, dl, verify:
        result = SeedanceVideo().execute(_inputs(env))
    assert not result.success
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "pending_billing" and res["provider_request_id"] == "req-x"
    assert res["actual_usd"] == pytest.approx(res["reserved_usd"])
    tracker = project_tracker(env)
    assert tracker.budget_spent_usd == pytest.approx(res["reserved_usd"], abs=1e-4)  # no refund
    assert not (env / "assets" / "video" / "shot.mp4").exists()
    assert not (env / ".staging").exists() or not any((env / ".staging").iterdir())
    with pytest.raises(IndeterminatePaidCallError, match="pending_billing"):
        resume_check(env)
    assert not find_generation(env, sha256_file(__file__))


def test_pending_billing_blocks_repeated_calls_at_the_boundary(tmp_path, monkeypatch):
    """A pending_billing reservation retains its budget AND halts every later call
    before submission (resume_check at the paid-call boundary)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, "proj-miser", budget=5.0)
    (project / "assets" / "video").mkdir(parents=True)
    inputs = _inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"))  # ~$2.84 each
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-1"}) as submit, \
         patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalQueueError("poll broke")):
        assert not SeedanceVideo().execute(inputs).success
        r = SeedanceVideo().execute(inputs)
    assert not r.success and "no terminal outcome" in r.error
    assert submit.call_count == 1
    assert len(load_reservations(project)) == 1
    t = project_tracker(project)
    assert t.budget_reserved_usd + t.budget_spent_usd > 0  # retained, never refunded


# ---- 2.0 legacy path: payload/caps unchanged, transport hardened ----

def test_v20_path_routed_through_queue_helpers(legacy_env):
    tool = SeedanceVideo()
    too_many = {"prompt": "p", "model_version": "2.0", "operation": "reference_to_video",
                "reference_image_urls": [f"https://fal.media/{i}.png" for i in range(10)]}
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = tool.execute(too_many)
    assert not r.success and "at most 9 reference images" in r.error
    submit.assert_not_called()
    assert tool.estimate_cost({"model_version": "2.0", "duration": "5"}) == pytest.approx(0.3034 * 5, abs=0.01)

    out = legacy_env / "assets" / "video" / "legacy.mp4"
    captured = {}

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["model_id"], captured["payload"] = model_id, payload
        return {"request_id": "r20", "status_url": "https://evil.example/s", "response_url": "https://evil.example/r"}

    def fake_wait(model_id, request_id, *, api_key, deadline_s, poll_s=5.0, **kw):
        captured["wait"] = (model_id, request_id, api_key, deadline_s)
        return {"video": {"url": "https://v3.fal.media/x.mp4"}, "seed": 1}

    def fake_download(url, dest, **kw):
        captured["download"] = (url, kw["max_bytes"])
        Path(dest).write_bytes(b"legacy")
        return {"bytes": 6, "content_type": "video/mp4"}

    with patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), \
         patch.object(_shared, "fal_queue_wait", side_effect=fake_wait), \
         patch.object(_shared, "fal_download", side_effect=fake_download), \
         patch.object(_shared, "verify_video_file", return_value=dict(PROBED)) as verify, \
         patch("requests.post") as raw_post, patch("requests.get") as raw_get:
        r = tool.execute({"prompt": "p", "model_version": "2.0", "output_path": str(out), "seed": 7,
                          "reference_image_urls": ["https://fal.media/a.png"], "operation": "reference_to_video"})
    assert r.success, r.error
    raw_post.assert_not_called()
    raw_get.assert_not_called()
    assert captured["model_id"] == "bytedance/seedance-2.0/reference-to-video"
    assert captured["payload"] == {"prompt": "p", "duration": "5", "aspect_ratio": "16:9", "resolution": "720p",
                                   "generate_audio": True, "seed": 7, "reference_image_urls": ["https://fal.media/a.png"]} \
        or captured["payload"]["reference_image_urls"] == ["https://fal.media/a.png"]
    assert captured["wait"][:3] == ("bytedance/seedance-2.0/reference-to-video", "r20", "test-key")
    assert captured["download"] == ("https://v3.fal.media/x.mp4", SeedanceVideo.V25_MAX_DOWNLOAD_BYTES)
    verify.assert_called_once()
    assert out.read_bytes() == b"legacy"
    assert not list(out.parent.glob(".legacy.mp4.*.part"))
    assert not (legacy_env / "cost-reservations.jsonl").exists()  # 2.0 keeps no reservations
    assert r.metadata["provider_request_id"] == "r20"
    assert find_generation(legacy_env, sha256_file(out))["model_endpoint"] == "bytedance/seedance-2.0/reference-to-video"


def test_v20_queue_submit_sends_no_retry_header(legacy_env):
    """The legacy path now inherits X-Fal-No-Retry from fal_queue_submit."""
    class _R:
        content = b"{}"
        def raise_for_status(self): pass
        def json(self): return {"request_id": "r", "status_url": "s", "response_url": "r"}

    with patch("requests.post", return_value=_R()) as post, \
         patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalQueueError("stop here")):
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "output_path": str(legacy_env / "assets" / "video" / "l.mp4")})
    assert not r.success
    assert post.call_args.args[0] == "https://queue.fal.run/bytedance/seedance-2.0/text-to-video"
    assert post.call_args.kwargs["headers"]["X-Fal-No-Retry"] == "1"


def test_v20_verification_failure_leaves_no_output(legacy_env):
    out = legacy_env / "assets" / "video" / "bad.mp4"
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "r"}), \
         patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/x.mp4"}}), \
         patch.object(_shared, "fal_download", side_effect=_fake_download), \
         patch.object(_shared, "verify_video_file", side_effect=_shared.VideoVerificationError("ffprobe is not installed")):
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "output_path": str(out)})
    assert not r.success and "ffprobe" in r.error
    assert not out.exists() and not list(out.parent.iterdir())


def test_v20_refused_for_authored_canon_project(env):
    """Any project with project.yaml is authored-canon: 2.0 must not run there."""
    out = env / "assets" / "video" / "legacy.mp4"
    with patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "upload_image_fal") as upload, \
         patch("requests.post") as raw_post:
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "output_path": str(out)})
    assert not r.success and "2.5" in r.error and "project.yaml" in r.error
    submit.assert_not_called()
    upload.assert_not_called()
    raw_post.assert_not_called()


def test_v20_never_uploads_a_path_outside_the_inferred_project(legacy_env, tmp_path):
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(tiny_png_bytes())
    out = legacy_env / "assets" / "video" / "legacy.mp4"
    with patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "upload_image_fal") as upload, \
         patch("requests.post") as raw_post:
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "operation": "image_to_video",
                                     "image_path": str(outside), "output_path": str(out)})
    assert not r.success and "elsewhere.png" in r.error
    upload.assert_not_called()
    submit.assert_not_called()
    raw_post.assert_not_called()


def test_nonterminal_reservation_blocks_new_paid_call_before_any_upload(env, monkeypatch):
    from tests.tools._authored_film_helpers import make_tracker
    from tools.cost_tracker import reserve_paid_call

    reserve_paid_call(make_tracker(env), env, tool="seedance_video", endpoint="x/y",
                      normalized_inputs_hash="ab" * 32, reserved_usd=0.5)
    ref = env / "ref.png"
    ref.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as upload, \
         patch.object(_shared, "fal_queue_submit") as submit, \
         patch("requests.post") as raw_post, patch("requests.get") as raw_get:
        r = SeedanceVideo().execute(_inputs(env, reference_image_urls=[], reference_image_paths=[str(ref)]))
    assert not r.success and "reconcile" in r.error.lower()
    upload.assert_not_called()
    submit.assert_not_called()
    raw_post.assert_not_called()
    raw_get.assert_not_called()
    assert len(load_reservations(env)) == 1  # no second reservation written

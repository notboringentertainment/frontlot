"""FAL queue helpers in tools/video/_shared.py — all network mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.video import _shared


def _resp(json=None, status=200, headers=None, chunks=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json if json is not None else {}
    m.content = b"x" if json is not None else b""
    m.headers = headers or {}
    m.raise_for_status.return_value = None
    m.iter_content.return_value = chunks or []
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    return m


def test_submit_sends_no_retry_header_and_returns_request_id():
    with patch("requests.post", return_value=_resp({"request_id": "req-9"})) as post:
        out = _shared.fal_queue_submit("vendor/model", {"prompt": "x"}, api_key="k")
    assert out["request_id"] == "req-9"
    args, kwargs = post.call_args
    assert args[0] == "https://queue.fal.run/vendor/model"
    assert kwargs["headers"]["X-Fal-No-Retry"] == "1"
    assert kwargs["headers"]["Authorization"] == "Key k"
    assert kwargs["json"] == {"prompt": "x"}


def test_submit_without_request_id_raises():
    with patch("requests.post", return_value=_resp({"detail": "nope"})):
        with pytest.raises(_shared.FalQueueError):
            _shared.fal_queue_submit("vendor/model", {}, api_key="k")


def test_wait_polls_fixture_urls_and_returns_result():
    statuses = [_resp({"status": "IN_QUEUE"}), _resp({"status": "IN_PROGRESS"}), _resp({"status": "COMPLETED"}),
                _resp({"video": {"url": "https://v3.fal.media/f.mp4"}})]
    with patch("requests.get", side_effect=statuses) as get:
        out = _shared.fal_queue_wait("vendor/model", "req-1", api_key="k", deadline_s=100, poll_s=0, _sleep=lambda s: None)
    assert out["video"]["url"].endswith("f.mp4")
    urls = [c.args[0] for c in get.call_args_list]
    assert urls[:3] == ["https://queue.fal.run/vendor/model/requests/req-1/status"] * 3
    assert urls[3] == "https://queue.fal.run/vendor/model/requests/req-1/response"
    assert all(c.kwargs["headers"]["X-Fal-No-Retry"] == "1" for c in get.call_args_list)


def test_wait_deadline_triggers_cancel():
    clock = iter([0.0, 0.0, 50.0, 200.0, 200.0])
    with patch("requests.get", return_value=_resp({"status": "IN_PROGRESS"})), \
         patch("requests.put", return_value=_resp({})) as put:
        with pytest.raises(_shared.FalDeadlineExceeded):
            _shared.fal_queue_wait("vendor/model", "req-2", api_key="k", deadline_s=100,
                                   poll_s=0, _sleep=lambda s: None, _clock=lambda: next(clock))
    put.assert_called_once()
    assert put.call_args.args[0] == "https://queue.fal.run/vendor/model/requests/req-2/cancel"


def test_wait_terminal_failure_raises():
    with patch("requests.get", return_value=_resp({"status": "FAILED", "error": "boom"})):
        with pytest.raises(_shared.FalQueueError, match="boom"):
            _shared.fal_queue_wait("vendor/model", "req-3", api_key="k", deadline_s=100, poll_s=0, _sleep=lambda s: None)


def test_download_rejects_disallowed_host(tmp_path):
    with patch("requests.get") as get:
        with pytest.raises(_shared.FalDownloadError, match="host not allowed"):
            _shared.fal_download("https://evil.example.com/f.mp4", tmp_path / "f", max_bytes=10, allowed_mime_prefixes=("video/",))
        with pytest.raises(_shared.FalDownloadError):
            _shared.fal_download("http://fal.media/f.mp4", tmp_path / "f", max_bytes=10, allowed_mime_prefixes=("video/",))
    get.assert_not_called()


def test_download_enforces_size_cap_and_mime(tmp_path):
    dest = tmp_path / "f.mp4"
    big = _resp(headers={"Content-Type": "video/mp4"}, chunks=[b"a" * 6, b"b" * 6])
    with patch("requests.get", return_value=big):
        with pytest.raises(_shared.FalDownloadError, match="exceeded cap"):
            _shared.fal_download("https://v3.fal.media/f.mp4", dest, max_bytes=10, allowed_mime_prefixes=("video/",))
    assert not dest.exists()
    wrong = _resp(headers={"Content-Type": "text/html"}, chunks=[b"<html>"])
    with patch("requests.get", return_value=wrong):
        with pytest.raises(_shared.FalDownloadError, match="content type"):
            _shared.fal_download("https://fal.media/f.mp4", dest, max_bytes=10, allowed_mime_prefixes=("video/",))
    ok = _resp(headers={"Content-Type": "video/mp4; charset=binary"}, chunks=[b"abc", b"de"])
    with patch("requests.get", return_value=ok):
        info = _shared.fal_download("https://fal.media/f.mp4", dest, max_bytes=10, allowed_mime_prefixes=("video/",))
    assert info == {"bytes": 5, "content_type": "video/mp4"}
    assert dest.read_bytes() == b"abcde"


def test_paid_call_context_requires_registered_verified_project(tmp_path, monkeypatch):
    from lib.project_config import ProjectConfigError

    from tests.tools._authored_film_helpers import make_verified_project

    with pytest.raises(_shared.PaidCallContextError, match="project_dir"):
        _shared.paid_call_context({})
    stray = tmp_path.parent / f"{tmp_path.name}-stray"
    stray.mkdir()
    (stray / "project.yaml").write_text("budget_usd_cap: 3.5\n")
    project = make_verified_project(tmp_path, monkeypatch, "proj-rook", budget=3.5)
    with pytest.raises(_shared.PaidCallContextError, match="registered project"):
        _shared.paid_call_context({"project_dir": str(stray), "budget_usd_cap": 99.0})
    # a file path deep inside the project attributes to its root
    root, tracker, config = _shared.paid_call_context({"project_dir": str(project / "assets" / "x.png")})
    assert root == project.resolve()
    assert tracker.budget_total_usd == 3.5 and config.budget_usd_cap == 3.5
    assert tracker.mode.value == "cap"
    # caller-injected cap/tracker never override the approved config
    root, tracker, _ = _shared.paid_call_context({"project_dir": str(project), "budget_usd_cap": 500.0, "cost_tracker": object()})
    assert tracker.budget_total_usd == 3.5
    (project / "project.yaml").write_text((project / "project.yaml").read_text() + "# edited\n")
    with pytest.raises(ProjectConfigError):
        _shared.paid_call_context({"project_dir": str(project)})


def _ffprobe_result(returncode=0, stdout="{}", stderr=""):
    class _P:
        pass
    p = _P()
    p.returncode, p.stdout, p.stderr = returncode, stdout, stderr
    return p


def test_verify_video_file_fails_closed(tmp_path):
    import json

    f = tmp_path / "clip.mp4"
    f.write_bytes(b"not really a video")
    with patch("shutil.which", return_value=None):
        with pytest.raises(_shared.VideoVerificationError, match="ffprobe is not installed"):
            _shared.verify_video_file(f)
    with patch("shutil.which", return_value="/usr/bin/ffprobe"):
        with patch("subprocess.run", return_value=_ffprobe_result(1, "", "Invalid data found")):
            with pytest.raises(_shared.VideoVerificationError, match="rejected"):
                _shared.verify_video_file(f)
        with patch("subprocess.run", return_value=_ffprobe_result(0, json.dumps({"streams": [{"codec_type": "audio"}]}))):
            with pytest.raises(_shared.VideoVerificationError, match="no video stream"):
                _shared.verify_video_file(f)
        video_only = json.dumps({"format": {"duration": "6.0"}, "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}]})
        with patch("subprocess.run", return_value=_ffprobe_result(0, video_only)):
            with pytest.raises(_shared.VideoVerificationError, match="no audio stream"):
                _shared.verify_video_file(f, require_audio=True)
            info = _shared.verify_video_file(f)
        assert info["video_codec"] == "h264" and info["duration_seconds"] == 6.0 and info["has_audio"] is False
        with pytest.raises(_shared.VideoVerificationError, match="missing"):
            _shared.verify_video_file(tmp_path / "nope.mp4")


def test_paid_call_context_halts_on_nonterminal_reservation_before_config_load(tmp_path, monkeypatch):
    """resume_check runs at the boundary itself, before any tracker/config work."""
    from tests.tools._authored_film_helpers import make_tracker, make_verified_project
    from tools.cost_tracker import IndeterminatePaidCallError, reserve_paid_call

    project = make_verified_project(tmp_path, monkeypatch, "proj-halt")
    reserve_paid_call(make_tracker(project), project, tool="t", endpoint="e", normalized_inputs_hash="cd" * 32, reserved_usd=1.0)
    with pytest.raises(IndeterminatePaidCallError):
        _shared.paid_call_context({"project_dir": str(project)})
    root, _tracker, _config = _shared.paid_call_context({"project_dir": str(project)}, check_resume=False)
    assert root == project.resolve()

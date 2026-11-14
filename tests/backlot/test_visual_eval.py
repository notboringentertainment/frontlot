"""Tests for Backlot visual eval image comparison helpers."""

import hashlib
import json
from pathlib import Path

from PIL import Image

from backlot.state import load_gate_detail
from scripts import backlot_visual_eval as visual_eval
from scripts.backlot_visual_eval import compare_images
from tests.lib.look_lock_helpers import CHAR


def _img(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (10, 10), color).save(path)


def test_compare_images_detects_large_drift(tmp_path):
    expected = tmp_path / "expected.png"
    actual = tmp_path / "actual.png"
    diff = tmp_path / "diff.png"
    _img(expected, (0, 0, 0))
    _img(actual, (255, 255, 255))

    result = compare_images(expected, actual, diff, threshold=0.015)

    assert result["passed"] is False
    assert result["changed_ratio"] == 1.0
    assert diff.exists()


def test_compare_images_can_mask_regions(tmp_path):
    expected = tmp_path / "expected.png"
    actual = tmp_path / "actual.png"
    diff = tmp_path / "diff.png"
    _img(expected, (0, 0, 0))
    _img(actual, (0, 0, 0))
    img = Image.open(actual)
    for x in range(5):
        for y in range(5):
            img.putpixel((x, y), (255, 255, 255))
    img.save(actual)

    result = compare_images(expected, actual, diff, threshold=0.015, masks=[(0, 0, 5, 5)])

    assert result["passed"] is True
    assert result["changed_ratio"] == 0.0


def test_missing_visual_baseline_fails_closed_unless_bless_is_explicit(monkeypatch, tmp_path):
    goldens = tmp_path / "goldens"
    capture = tmp_path / "capture"
    capture.mkdir()
    monkeypatch.setattr(visual_eval, "GOLDENS_DIR", goldens)
    monkeypatch.setattr(visual_eval, "SHOTS", [("one", "/", 10, 10, 0, [])])
    _img(capture / "one.png", (12, 34, 56))

    missing = visual_eval.compare_or_bless(capture, bless=False, threshold=0.015)

    assert missing == [{
        "name": "one",
        "status": "missing",
        "passed": False,
        "golden": str(goldens / "one.png"),
    }]
    assert not (goldens / "one.png").exists()

    blessed = visual_eval.compare_or_bless(capture, bless=True, threshold=0.015)
    assert blessed[0]["status"] == "blessed"
    assert (goldens / "one.png").is_file()


def test_headshot_golden_fixture_has_exactly_three_byte_real_candidates(monkeypatch, tmp_path):
    stage = tmp_path / "screenshot-stage"
    monkeypatch.setattr(visual_eval, "STAGE_DIR", stage)

    project = visual_eval.stage_gate_project()
    detail = load_gate_detail(project, visual_eval.GATE_REQUEST_ID)
    candidates = detail["packet"]["candidates"]

    assert [candidate["number"] for candidate in candidates] == [1, 2, 3]
    assert len(candidates) == 3
    for candidate in candidates:
        visual = candidate["visual"]
        payload = (project / visual["path"]).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(payload).hexdigest() == visual["sha256"]
        assert visual.get("error") is None

    request = json.loads(
        (project / ".gate-requests" / f"{visual_eval.GATE_REQUEST_ID}.json").read_text(encoding="utf-8")
    )
    assert request["entity_id"] == CHAR
    assert request["project_id"] == visual_eval.GATE_PROJECT_ID


def test_headshot_gate_sse_preserves_real_xterm_dom_and_session(tmp_path):
    visual_eval.stage_gate_project()
    server = visual_eval.start_server()
    try:
        result = visual_eval.run_interactions(tmp_path)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()

    assert result["status"] == "passed"
    assert result["identity"] == {
        "mount_same": True,
        "xterm_dom_same": True,
        "session_same": True,
        "terminal_same": True,
        "socket_same": True,
        "socket_open": True,
        "input_ready": True,
        "auth_frames": 1,
        "resize_frames": result["identity"]["resize_frames"],
        "input_byte_frames": 0,
        "packet_error_locked": True,
        "detail_refetched": True,
    }
    assert result["identity"]["resize_frames"] >= 1
    assert result["identity"]["packet_error_locked"] is True
    assert result["identity"]["detail_refetched"] is True
    assert Path(result["screenshot"]).is_file()
    assert len(result["masks"]) == 1
    comparison = visual_eval.compare_or_bless_interaction(result, bless=False, threshold=0.005)
    assert comparison["passed"] is True
    assert comparison["threshold"] == 0.005
    assert comparison["changed_ratio"] <= comparison["threshold"]

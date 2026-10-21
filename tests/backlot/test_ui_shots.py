"""Browser test: supervised shots in the trim bin and their takes in the viewer (fake claude, no spend)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from tests.backlot import claude_fakes

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECTION = ("One five-second medium close-up of hero-a as a continuity test. "
             "Hold a steady gaze, then a slight head turn.")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _row(shot: Path, row: dict) -> None:
    with (shot / "history.jsonl").open("a") as out:
        out.write(json.dumps(row) + "\n")


_CLIP = None


def _clip() -> bytes:
    """A real three-second clip, so the browser can play and scrub it. WebM,
    because Playwright's Chromium has no H.264."""
    global _CLIP
    if _CLIP is None:
        out = Path(tempfile.mkdtemp(prefix="om-clip-")) / "clip.webm"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=12",
                        "-c:v", "libvpx", "-b:v", "200k", str(out)], check=True)
        _CLIP = out.read_bytes()
    return _CLIP


def add_take(film: Path, take_id: str, digest: str, at: str) -> str:
    import hashlib
    rel = f"production/shots/shot-a/takes/{digest}.webm"
    (film / rel).parent.mkdir(parents=True, exist_ok=True)
    body = _clip()
    (film / rel).write_bytes(body)
    _row(film / "production" / "shots" / "shot-a", {
        "event_id": f"ev-{take_id}", "at": at, "kind": "take", "take_id": take_id, "path": rel,
        "sha256": hashlib.sha256(body).hexdigest(), "duration_seconds": 3.0, "cost_usd": 0.56,
        "brief_revision_id": "rev1", "user_note": "made"})
    return rel


@pytest.fixture(scope="module")
def shots_server():
    root = Path(tempfile.mkdtemp(prefix="om-ui-", dir="/tmp"))   # AF_UNIX path limit
    gates = root / "g"
    gates.mkdir()
    home = root / "home"
    home.mkdir()
    film = root / "projects" / "film"
    shot = film / "production" / "shots" / "shot-a"
    shot.mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    _row(shot, {"event_id": "rev1", "at": "2026-01-02T10:00:00+00:00", "kind": "brief", "user_note": "ok",
                "brief": {"shot_id": "shot-a", "direction": DIRECTION, "spend_allowance_usd": 15.0,
                          "max_video_takes": 2, "allowed_tools": ["kling_reference_video"]}})
    first = add_take(film, "t1", "a" * 64, "2026-01-02T11:00:00+00:00")
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "OPENMONTAGE_PROJECTS_DIR": str(root / "projects"),
        "OPENMONTAGE_GATES_DIR": str(gates),
        "FRONTLOT_CLAUDE": str(claude_fakes.write_fake_claude(root)),
        "FRONTLOT_SKIP_PREFLIGHT": "1",
        "FRONTLOT_LEASE_SECONDS": "0.2",
    })
    server = subprocess.Popen(
        [sys.executable, "-m", "backlot", "serve", "--port", str(port)],
        cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                break
        except Exception:
            time.sleep(0.2)
    else:
        server.terminate()
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("Front Lot server did not become healthy")
    token = (home / ".openmontage" / "backlot" / f"{port}.token").read_text().strip()
    try:
        yield f"http://127.0.0.1:{port}", token, film, first
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        claude_fakes.stop_brokers(gates)
        shutil.rmtree(root, ignore_errors=True)


def test_a_waiting_take_has_a_card_under_needs_you_and_plays_in_full(shots_server):
    base, token, _film, first = shots_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film#k={token}", wait_until="load")
            # This film has no image requests at all; the take still gets its card.
            card = page.locator("#gates .need.take-need")
            card.wait_for(timeout=10000)
            assert "video take" in card.inner_text().lower() and "1 take to review" in card.inner_text()
            assert page.locator("#needs-heading .count").inner_text() == "1"
            card.click()
            video = page.locator("#viewer .take-tile video")
            video.wait_for(timeout=5000)
            # The whole original file, never a trimmed section (#t= would limit playback).
            assert video.get_attribute("src") == f"/media/film/{first}"
            assert video.get_attribute("controls") is not None
            page.wait_for_function("() => document.querySelector('#viewer .take-tile video').readyState >= 1", timeout=10000)
            assert abs(video.evaluate("v => v.duration") - 3.0) < 0.3
            video.evaluate("v => { v.currentTime = 2.6; }")
            page.wait_for_function("() => Math.abs(document.querySelector('#viewer .take-tile video').currentTime - 2.6) < 0.2",
                                   timeout=5000)
            viewer_text = page.locator("#viewer").inner_text()
            assert "continuity test" in viewer_text and "$0.56" in viewer_text and "3 seconds" in viewer_text
            assert "a" * 16 not in viewer_text and "shot-a" not in viewer_text
            assert page.locator("#viewer .take-approve").is_enabled()
            assert page.locator("#viewer .take-decline").is_enabled()
            assert page.locator("#viewer .tape.waiting").inner_text().lower() == "needs you"
        finally:
            browser.close()


def test_several_takes_sit_side_by_side_and_bens_presses_settle_the_card(shots_server):
    base, token, film, first = shots_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film#k={token}", wait_until="load")
            page.locator("#gates .need.take-need").click(timeout=10000)
            page.locator("#viewer .take-tile video").wait_for(timeout=5000)
            second = add_take(film, "t2", "b" * 64, "2026-01-02T12:00:00+00:00")
            third = add_take(film, "t3", "c" * 64, "2026-01-02T13:00:00+00:00")
            page.wait_for_function("() => document.querySelectorAll('#viewer .take-tile').length === 3", timeout=15000)
            tiles = page.locator("#viewer .take-tile")
            boxes = [tiles.nth(i).bounding_box() for i in range(3)]
            assert len({round(b["y"]) for b in boxes}) == 1, boxes   # one row, side by side
            assert [tiles.nth(i).locator("video").get_attribute("src") for i in range(3)] == \
                [f"/media/film/{first}", f"/media/film/{second}", f"/media/film/{third}"]
            assert "sets the other 2 aside" in tiles.nth(0).inner_text()

            # A note being typed survives a live refresh.
            tiles.nth(0).locator("textarea").fill("Too fast at the turn.")
            tiles.nth(0).locator(".take-decline").click()
            page.wait_for_function("() => document.querySelectorAll('#viewer .take-tile').length === 2", timeout=10000)
            history = [json.loads(l) for l in (film / "production/shots/shot-a/history.jsonl").read_text().splitlines()]
            assert history[-1]["kind"] == "decline" and history[-1]["user_note"] == "Too fast at the turn."

            page.locator("#viewer .take-tile").nth(1).locator(".take-approve").click()
            page.wait_for_function("() => !document.querySelector('#gates .need.take-need')", timeout=10000)
            history = [json.loads(l) for l in (film / "production/shots/shot-a/history.jsonl").read_text().splitlines()]
            assert history[-1]["kind"] == "selection" and history[-1]["take_id"] == "t3"
            assert history[-1]["user_note"] == "Approved on the take card."
            assert "Nothing is waiting on you." in page.locator("#log-feed").inner_text()
            page.wait_for_function("() => document.querySelector('#viewer .tape.done')", timeout=5000)
            assert page.locator("#viewer .take-tile").count() == 0
            assert page.locator("#viewer .screen video").get_attribute("src") == f"/media/film/{third}"
        finally:
            browser.close()


def test_a_page_without_the_app_token_cannot_decide(shots_server):
    base, _token, film, _first = shots_server
    add_take(film, "t4", "d" * 64, "2026-01-02T14:00:00+00:00")
    before = (film / "production/shots/shot-a/history.jsonl").read_text()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{base}/p/film", wait_until="load")
            page.locator("#gates .need.take-need").click(timeout=10000)
            page.locator("#viewer .take-approve").last.click()
            page.locator("#viewer .take-error").wait_for(timeout=5000)
            assert "couldn't confirm this came from you" in page.locator("#viewer .take-error").inner_text()
        finally:
            browser.close()
    assert (film / "production/shots/shot-a/history.jsonl").read_text() == before

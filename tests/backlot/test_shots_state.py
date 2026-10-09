"""Supervised-production shots on the board (read through lib.supervised_production)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from backlot.state import load_board_state

DIRECTION = ("One five-second medium close-up of hero-a as a continuity test. "
             "Hold a steady gaze, then a slight head turn. No dialogue.")


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", root)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", root)
    monkeypatch.setattr(server_mod, "_summary_cache", {})
    monkeypatch.setattr(server_mod, "THUMB_CACHE_DIR", tmp_path / "thumbs")
    return root


def _film(root: Path) -> Path:
    film = root / "film"
    film.mkdir()
    (film / "project.json").write_text(json.dumps({"title": "Film"}))
    return film


def _row(shot: Path, row: dict) -> None:
    shot.mkdir(parents=True, exist_ok=True)
    with (shot / "history.jsonl").open("a") as out:
        out.write(json.dumps(row) + "\n")


def _brief(film: Path, shot_id: str = "shot-a", event_id: str = "rev1", at: str = "2026-01-02T10:00:00+00:00") -> Path:
    shot = film / "production" / "shots" / shot_id
    _row(shot, {"event_id": event_id, "at": at, "kind": "brief", "user_note": "ok",
                "brief": {"shot_id": shot_id, "direction": DIRECTION, "spend_allowance_usd": 15.0,
                          "max_video_takes": 2, "allowed_tools": ["kling_reference_video"]}})
    return shot


def _take(film: Path, shot: Path, take_id: str, digest: str, *, at: str, cost=0.56, revision="rev1") -> str:
    rel = f"production/shots/{shot.name}/takes/{digest}.mp4"
    (film / rel).parent.mkdir(parents=True, exist_ok=True)
    (film / rel).write_bytes(b"\x00\x00\x00\x18ftypmp42" + digest.encode())
    _row(shot, {"event_id": f"ev-{take_id}", "at": at, "kind": "take", "take_id": take_id, "path": rel,
                "sha256": digest, "duration_seconds": 5.0, "cost_usd": cost, "brief_revision_id": revision,
                "user_note": "made"})
    return rel


def test_prepared_shot_has_a_plain_label_and_no_takes(projects_root):
    film = _film(projects_root)
    _brief(film)
    shots = load_board_state(film)["shots"]
    assert len(shots) == 1
    shot = shots[0]
    assert shot["id"] == "shot-a"
    assert shot["label"] == "One five-second medium close-up of hero-a as a continuity test."
    assert shot["direction"] == DIRECTION
    assert shot["state"] == "prepared"
    assert shot["takes"] == []


def test_takes_are_listed_in_order_with_cost_time_and_selection(projects_root):
    film = _film(projects_root)
    shot = _brief(film)
    first = _take(film, shot, "t1", "a" * 64, at="2026-01-02T11:00:00+00:00", cost=0.56)
    second = _take(film, shot, "t2", "b" * 64, at="2026-01-02T12:00:00+00:00", cost=None)
    shots = load_board_state(film)["shots"]
    assert shots[0]["state"] == "has_takes"
    takes = shots[0]["takes"]
    assert [t["path"] for t in takes] == [first, second]
    assert takes[0]["made_at"] == "2026-01-02T11:00:00+00:00"
    assert takes[0]["cost_usd"] == 0.56 and takes[1]["cost_usd"] is None
    assert takes[0]["duration_seconds"] == 5.0
    assert all(t["playable"] for t in takes)
    assert not any(t["selected"] for t in takes)
    assert shots[0]["spent_usd"] == 0.56

    _row(shot, {"event_id": "sel", "at": "2026-01-02T13:00:00+00:00", "kind": "selection",
                "take_id": "t1", "user_note": "this one"})
    shots = load_board_state(film)["shots"]
    assert shots[0]["state"] == "selected"
    assert [t["selected"] for t in shots[0]["takes"]] == [True, False]


def test_stopped_shot_reads_stopped(projects_root):
    film = _film(projects_root)
    shot = _brief(film)
    _take(film, shot, "t1", "c" * 64, at="2026-01-02T11:00:00+00:00")
    _row(shot, {"event_id": "st", "at": "2026-01-02T12:00:00+00:00", "kind": "stop", "user_note": "enough"})
    assert load_board_state(film)["shots"][0]["state"] == "stopped"


def test_take_outside_the_film_or_missing_is_not_playable(projects_root, tmp_path):
    film = _film(projects_root)
    shot = _brief(film)
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    _row(shot, {"event_id": "e1", "at": "2026-01-02T11:00:00+00:00", "kind": "take", "take_id": "t1",
                "path": "../../elsewhere.mp4", "sha256": "d" * 64, "brief_revision_id": "rev1"})
    _row(shot, {"event_id": "e2", "at": "2026-01-02T11:30:00+00:00", "kind": "take", "take_id": "t2",
                "path": "production/shots/shot-a/takes/gone.mp4", "sha256": "e" * 64, "brief_revision_id": "rev1"})
    takes = load_board_state(film)["shots"][0]["takes"]
    assert [t["playable"] for t in takes] == [False, False]
    assert all(t["path"] is None for t in takes)


def test_unreadable_or_unprepared_shots_never_break_the_board(projects_root):
    film = _film(projects_root)
    _brief(film)
    broken = film / "production" / "shots" / "shot-b"
    broken.mkdir(parents=True)
    (broken / "history.jsonl").write_text("{not json\n")
    (film / "production" / "shots" / "shot-c").mkdir()      # brief.json drafted, never prepared
    (film / "production" / "shots" / "shot-c" / "brief.json").write_text("{}")
    (film / "production" / "shots" / "bad name!").mkdir()
    state = load_board_state(film)
    assert [s["id"] for s in state["shots"]] == ["shot-a"]


def test_film_without_shots_has_an_empty_list(projects_root):
    film = _film(projects_root)
    assert load_board_state(film)["shots"] == []


def test_take_file_is_served_by_the_film_media_route(projects_root, monkeypatch):
    film = _film(projects_root)
    shot = _brief(film)
    rel = _take(film, shot, "t1", "f" * 64, at="2026-01-02T11:00:00+00:00")

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as client:
        body = client.get("/api/project/film/state").json()
        assert body["shots"][0]["takes"][0]["path"] == rel
        response = client.get(f"/media/film/{rel}")
        assert response.status_code == 200
        assert response.content.endswith(("f" * 64).encode())
        assert client.get("/media/film/../../elsewhere.mp4").status_code in (403, 404)

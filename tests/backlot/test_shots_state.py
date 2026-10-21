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
    assert shots[0]["state"] == "waiting"
    assert shots[0]["waiting_count"] == 2
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
    # Approving one take sets aside the other one made before it.
    assert [t["waiting"] for t in shots[0]["takes"]] == [False, False]
    assert shots[0]["waiting_count"] == 0


def test_a_take_made_after_the_choice_waits_again_and_declines_settle(projects_root):
    film = _film(projects_root)
    shot = _brief(film)
    _take(film, shot, "t1", "a" * 64, at="2026-01-02T11:00:00+00:00")
    _row(shot, {"event_id": "sel", "at": "2026-01-02T12:00:00+00:00", "kind": "selection", "take_id": "t1", "user_note": "ok"})
    _take(film, shot, "t2", "b" * 64, at="2026-01-02T13:00:00+00:00")
    entry = load_board_state(film)["shots"][0]
    assert entry["state"] == "waiting" and entry["waiting_count"] == 1
    assert [(t["selected"], t["waiting"]) for t in entry["takes"]] == [(True, False), (False, True)]
    _row(shot, {"event_id": "dec", "at": "2026-01-02T14:00:00+00:00", "kind": "decline", "take_id": "t2", "user_note": "no"})
    entry = load_board_state(film)["shots"][0]
    assert entry["state"] == "selected"
    assert [t["declined"] for t in entry["takes"]] == [False, True]


def test_changed_brief_source_is_named_in_plain_words(projects_root):
    film = _film(projects_root)
    note = film / "canon" / "shot-note.md"
    note.parent.mkdir()
    note.write_text("# The Gallery Scene\n\nOriginal.")
    from lib.pathsafe import sha256_file
    shot = film / "production" / "shots" / "shot-a"
    _row(shot, {"event_id": "rev1", "at": "2026-01-02T10:00:00+00:00", "kind": "brief", "user_note": "ok",
                "brief": {"shot_id": "shot-a", "direction": DIRECTION, "spend_allowance_usd": 15.0,
                          "max_video_takes": 2, "allowed_tools": ["kling_reference_video"],
                          "source_snapshots": [{"path": str(note), "sha256": sha256_file(note)}]}})
    _take(film, shot, "t1", "a" * 64, at="2026-01-02T11:00:00+00:00")
    assert load_board_state(film)["shots"][0]["takes"][0]["source_changes"] == []
    note.write_text("# The Gallery Scene\n\nRewritten.")
    changes = load_board_state(film)["shots"][0]["takes"][0]["source_changes"]
    assert changes == [{"kind": "source", "name": "The Gallery Scene", "entity_id": None, "missing": False}]
    note.unlink()
    changes = load_board_state(film)["shots"][0]["takes"][0]["source_changes"]
    assert changes == [{"kind": "source", "name": "shot note", "entity_id": None, "missing": True}]


def test_a_pipeline_record_source_reads_as_what_it_holds():
    from lib.supervised_production import _title
    assert _title(Path("/film/checkpoint_visual_bible.json")) == "the approved visual bible"
    assert _title(Path("/film/missing-notes.txt")) == "missing notes"


def test_stopped_shot_keeps_its_unjudged_take_waiting(projects_root):
    film = _film(projects_root)
    shot = _brief(film)
    _take(film, shot, "t1", "c" * 64, at="2026-01-02T11:00:00+00:00")
    _row(shot, {"event_id": "st", "at": "2026-01-02T12:00:00+00:00", "kind": "stop", "user_note": "enough"})
    entry = load_board_state(film)["shots"][0]
    assert entry["state"] == "waiting" and entry["stopped"] is True
    _row(shot, {"event_id": "dec", "at": "2026-01-02T13:00:00+00:00", "kind": "decline", "take_id": "t1", "user_note": "no"})
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


TOKEN = "t" * 43
ORIGIN = "http://127.0.0.1:4750"


@pytest.fixture
def decider(projects_root, monkeypatch):
    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    film = _film(projects_root)
    shot = _brief(film)
    _take(film, shot, "t1", "a" * 64, at="2026-01-02T11:00:00+00:00")
    _take(film, shot, "t2", "b" * 64, at="2026-01-02T12:00:00+00:00")
    # The take files must still match their recorded hashes for an approve.
    import hashlib
    for row in [json.loads(l) for l in (shot / "history.jsonl").read_text().splitlines()]:
        if row["kind"] == "take":
            row_path = film / row["path"]
            digest = hashlib.sha256(row_path.read_bytes()).hexdigest()
            text = (shot / "history.jsonl").read_text().replace(f'"sha256": "{row["sha256"]}"', f'"sha256": "{digest}"')
            (shot / "history.jsonl").write_text(text)
    with TestClient(server_mod.create_app(port=4750, capability_token=TOKEN)) as client:
        yield client, film, shot


def _decide(client, take, decision, note=None, *, token=TOKEN, origin=ORIGIN):
    headers = {}
    if token is not None:
        headers["X-Frontlot-Capability"] = token
    if origin is not None:
        headers["Origin"] = origin
    body = {"decision": decision} if note is None else {"decision": decision, "note": note}
    return client.post(f"/api/project/film/shot/shot-a/take/{take}/decision", json=body, headers=headers)


def _kinds(shot):
    return [json.loads(l)["kind"] for l in (shot / "history.jsonl").read_text().splitlines()]


def test_take_decision_needs_the_app_token_and_origin(decider):
    client, film, shot = decider
    before = (shot / "history.jsonl").read_text()
    assert _decide(client, "t1", "approve", token=None).status_code == 401
    assert _decide(client, "t1", "approve", token="x" * 43).status_code == 401
    assert _decide(client, "t1", "approve", origin=None).status_code == 403
    assert _decide(client, "t1", "approve", origin="http://evil.example").status_code == 403
    assert (shot / "history.jsonl").read_text() == before


def test_app_without_a_token_refuses_every_press(projects_root, monkeypatch, decider):
    _, film, shot = decider
    with TestClient(server_mod.create_app(port=4750, capability_token=None)) as client:
        assert _decide(client, "t1", "approve", token="").status_code == 401


def test_approve_chooses_the_take_and_settles_the_card(decider):
    client, film, shot = decider
    response = _decide(client, "t2", "approve", "The turn lands.")
    assert response.status_code == 200, response.text
    rows = [json.loads(l) for l in (shot / "history.jsonl").read_text().splitlines()]
    assert rows[-1]["kind"] == "selection" and rows[-1]["take_id"] == "t2"
    assert rows[-1]["user_note"] == "The turn lands."
    entry = load_board_state(film)["shots"][0]
    assert entry["waiting_count"] == 0 and entry["state"] == "selected"
    # The other take was passed over, so it can't be decided any more.
    assert _decide(client, "t1", "approve").status_code == 409
    assert _kinds(shot).count("selection") == 1


def test_decline_with_and_without_a_note(decider):
    client, film, shot = decider
    assert _decide(client, "t1", "decline").status_code == 200
    rows = [json.loads(l) for l in (shot / "history.jsonl").read_text().splitlines()]
    assert rows[-1]["kind"] == "decline" and rows[-1]["user_note"] == "Declined on the take card."
    assert _decide(client, "t1", "decline").status_code == 409
    assert _decide(client, "t2", "decline", "  Too fast.  ").status_code == 200
    assert json.loads((shot / "history.jsonl").read_text().splitlines()[-1])["user_note"] == "Too fast."
    entry = load_board_state(film)["shots"][0]
    assert entry["waiting_count"] == 0 and not any(t["selected"] for t in entry["takes"])


def test_a_changed_take_file_cannot_be_approved(decider):
    client, film, shot = decider
    rel = next(json.loads(l)["path"] for l in (shot / "history.jsonl").read_text().splitlines()
               if json.loads(l).get("take_id") == "t1")
    (film / rel).write_bytes(b"swapped")
    response = _decide(client, "t1", "approve")
    assert response.status_code == 409
    assert "can't be approved" in response.json()["detail"]
    assert "selection" not in _kinds(shot)


def test_bad_decisions_are_refused(decider):
    client, film, shot = decider
    assert _decide(client, "t1", "choose").status_code == 400
    assert _decide(client, "t1", "approve", "x" * 2001).status_code == 400
    assert _decide(client, "nope", "approve").status_code == 404
    response = client.post("/api/project/film/shot/no-shot/take/t1/decision", json={"decision": "approve"},
                           headers={"X-Frontlot-Capability": TOKEN, "Origin": ORIGIN})
    assert response.status_code == 404
    assert "selection" not in _kinds(shot) and "decline" not in _kinds(shot)


def test_a_failure_after_the_approval_is_saved_still_counts(decider, monkeypatch):
    client, film, shot = decider
    import lib.run_common as run_common

    def broken(*args, **kwargs):
        raise RuntimeError("decision log unreadable")

    monkeypatch.setattr(run_common, "write_decision", broken)
    response = _decide(client, "t1", "approve")
    assert response.status_code == 200
    assert _kinds(shot).count("selection") == 1

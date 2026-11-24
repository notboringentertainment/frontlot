import base64
import io
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lab.app import create_app
from lab.canon import read_with_claude
from lab.fal import MODEL, Fal

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT = "0b4cb6ec-ec94-46c7-a9ea-aa90f1d753cb"
SHOW = f"/api/shows/{PROJECT}"
CANON = {
    "entries": [
        dict(name="Esme Vale", kind="person", quotes=["She has a blunt dark bob and a coat with a collar turned up against the salt wind."],
             description="An approximation.", drafted=False, look_id="esme-dock"),
        dict(name="Orin Bell", kind="person", quotes=[], description="A broad man with silver hair.", drafted=True, look_id=None),
        dict(name="Tide Office", kind="place", quotes=["The Tide Office is a clapboard room on stilts over a tidal inlet."],
             description="A clapboard room on stilts.", drafted=False, look_id=None),
        dict(name="Bell Steps", kind="place", quotes=[], description="Seaweed-covered stone steps.", drafted=True, look_id=None),
    ]
}


@pytest.fixture
def folders(tmp_path):
    return shutil.copytree(FIXTURES, tmp_path / "writer"), tmp_path / "lab"


@pytest.fixture
def png():
    with io.BytesIO() as stream:
        Image.new("RGB", (20, 20), "#627b81").save(stream, format="PNG")
        return stream.getvalue()


def test_kit_list(folders):
    root, _ = folders
    pending = root / "River Index (a9q3).writeros/memory/exports/studio-kit"
    pending.mkdir(parents=True)
    (pending / "state.json").write_text('{"status":"pending","generation":null}')
    unreadable = root / "October Rooks (x6q8).writeros/memory/exports/studio-kit"
    unreadable.mkdir(parents=True)
    (unreadable / "state.json").write_text("{?")
    missing = root / "Oxbow Weather (n2t4).writeros"
    missing.mkdir()
    with TestClient(create_app(*folders)) as client:
        shows = {show["title"]: show for show in client.get("/api/shows").json()}
        assert {title: show["status"] for title, show in shows.items()} == {
            "The Lantern Post": "current", "Winter Freight": "no-canon",
            "River Index": "pending", "October Rooks": "unreadable", "Oxbow Weather": "missing",
        }
        assert shows["The Lantern Post"]["id"] == PROJECT
        assert client.post(f"/api/shows/{shows['Winter Freight']['id']}/canon").status_code == 404


def test_missing_root(folders):
    root, library = folders
    with TestClient(create_app(root / "missing", library)) as client:
        assert client.get("/api/shows").json() == []


@pytest.mark.parametrize("outcome", ["good", "bad", "missing"])
def test_claude_and_manual_entries(folders, monkeypatch, outcome):
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["input"])
        assert args[1] == "-p"
        assert "--no-session-persistence" in args
        assert "clapboard room" in calls[-1] and "The undelivered letters" in calls[-1]
        assert "esme-dock" in calls[-1]
        assert "face clearly visible and turned toward the camera" in calls[-1]
        if outcome == "missing":
            raise FileNotFoundError
        stdout = json.dumps({"structured_output": CANON}) if outcome == "good" else "Not JSON"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr("lab.canon.subprocess.run", run)
    monkeypatch.setattr("lab.canon.shutil.which", lambda _: "claude")
    with TestClient(create_app(*folders, canon_reader=read_with_claude)) as client:
        first = client.post(f"{SHOW}/canon").json()
        assert client.post(f"{SHOW}/canon").json() == first
        assert len(calls) == 1
        if outcome == "good":
            assert first["notice"] == ""
            esme, orin, tide, steps = first["entries"]
            assert esme["description"].startswith("A woman with a blunt dark bob")
            assert esme["quotes"] and not esme["drafted"]
            assert orin["drafted"] and not orin["quotes"]
            assert tide["kind"] == steps["kind"] == "place"
            client.patch(f"{SHOW}/entries/{esme['id']}", json={"name": "Esme V.", "description": "A red coat."})
            client.delete(f"{SHOW}/entries/{orin['id']}")
            reread = client.post(f"{SHOW}/canon?again=true").json()
            assert [entry["name"] for entry in reread["entries"]] == ["Esme V.", "Tide Office", "Bell Steps"]
            assert reread["entries"][0]["description"] == "A red coat."
            assert len(calls) == 2
        else:
            assert not first["entries"]
            assert "add people and places by hand" in first["notice"]
        added = client.post(f"{SHOW}/entries", json={"name": "Pella Rook", "kind": "person"}).json()
        assert client.patch(f"{SHOW}/entries/{added['id']}", json={"name": "Pella Reed"}).json()["name"] == "Pella Reed"


def test_pictures_references_import_spending_and_read_only(folders, png, monkeypatch):
    root, library = folders
    snapshot = lambda: {path.relative_to(root): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
                        for path in root.rglob("*")}
    before = snapshot()
    requests = []

    def fal_queue(request):
        if request.method == "POST":
            assert request.headers["authorization"] == "Key fixture"
            requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"status_url": "https://queue.fal.run/fixture/status",
                                             "response_url": "https://queue.fal.run/fixture/result"})
        if request.url.path == "/fixture/status":
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/fixture/result":
            units = {2: "0.19", 3: "invalid"}.get(len(requests))
            headers = {"X-Fal-Billable-Units": units} if units is not None else {}
            return httpx.Response(200, headers=headers, json={"images": [{"url": "https://fixture.invalid/picture.png"}] * 4})
        assert request.url.host == "fixture.invalid"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=png)

    fal = Fal(transport=httpx.MockTransport(fal_queue))
    monkeypatch.setattr(fal, "headers", lambda: {"Authorization": "Key fixture"})
    with TestClient(create_app(root, library, canon_reader=lambda *_: CANON, fal=fal)) as client:
        assert client.get("/").status_code == 200
        show = client.post(f"{SHOW}/canon").json()
        assert Decimal(show["limit"]) == 25
        entry = f"{SHOW}/entries/{show['entries'][0]['id']}"
        made = client.post(f"{entry}/make", json={"description": "Esme in a coat."}).json()
        assert len(made["pictures"]) == 4
        assert Decimal(made["spent"]) == Decimal("0.20") and made["estimated"]
        assert requests[0][0] == "/" + MODEL.text
        assert requests[0][1]["prompt"] == "Esme in a coat."
        assert requests[0][1]["num_images"] == 4 and "image_urls" not in requests[0][1]
        first, second, *_ = made["pictures"]
        for picture in (first, second):
            assert client.patch(picture["url"], json={"kept": True}).json()["kept"]
        assert client.patch(first["url"], json={"main": True}).json()["main"]
        more = client.post(f"{entry}/make", json={"description": "Esme in sunlight."}).json()
        assert len(more["pictures"]) == 8
        assert Decimal(more["spent"]) - Decimal(made["spent"]) == Decimal("0.19")
        state = json.loads((library / PROJECT / "show.json").read_text())
        assert Decimal(state["estimated"]) == Decimal("0.20")
        endpoint, payload = requests[-1]
        assert endpoint == "/" + MODEL.edit
        assert payload["prompt"] == (
            "Show the same person, preserving the face and identity from the reference picture. Esme in sunlight."
        )
        assert base64.b64decode(payload["image_urls"][0].split(",", 1)[1]) == png
        assert client.get(first["url"]).content == png

        outside = Image.new("RGB", (32, 32), "#301860")
        with io.BytesIO() as stream:
            outside.save(stream, format="JPEG")
            imported = client.post(f"{entry}/import", content=stream.getvalue()).json()
        assert imported["kept"]
        assert client.patch(imported["url"], json={"main": True}).json()["main"]
        assert not client.patch(first["url"], json={"kept": False}).json()["main"]
        assert client.patch(SHOW, json={"limit": "0.58"}).status_code == 200
        assert client.post(f"{entry}/make", json={"description": "Esme indoors."}).status_code == 409
        assert len(requests) == 2
        assert client.patch(SHOW, json={"limit": "0.59"}).status_code == 200
        assert client.post(f"{entry}/make", json={"description": "Esme indoors."}).status_code == 200
        ref = base64.b64decode(requests[-1][1]["image_urls"][0].split(",", 1)[1])
        assert Image.open(io.BytesIO(ref)).getpixel((0, 0)) != (98, 123, 129)

    with TestClient(create_app(root, library, fal=fal)) as client:
        saved = client.get(SHOW).json()
        assert Decimal(saved["spent"]) == Decimal("0.59") and saved["estimated"]
        assert len(saved["entries"][0]["pictures"]) == 13
        assert sum(picture["kept"] for picture in saved["entries"][0]["pictures"]) == 2
        assert sum(picture["main"] for picture in saved["entries"][0]["pictures"]) == 1
    assert snapshot() == before


def test_budget_shows_cents_without_rounding_saved_spending(folders):
    _, library = folders
    with TestClient(create_app(*folders)) as client:
        assert client.patch(SHOW, json={"limit": "25"}).status_code == 200
        path = library / PROJECT / "show.json"
        state = json.loads(path.read_text())
        state["spent"] = "0.025"
        path.write_text(json.dumps(state))

        saved = client.get(SHOW).json()
        assert saved["spent"] == "0.03"
        assert saved["limit"] == "25.00"
        assert json.loads(path.read_text())["spent"] == "0.025"


def test_changed_generation_reads_named_canon_and_keeps_local_edits(folders):
    root, library = folders
    exports = next(root.glob("The Lantern Post*/memory/exports/studio-kit"))
    calls = []

    def reader(canon, *_):
        calls.append(canon)
        return CANON

    with TestClient(create_app(root, library, canon_reader=reader)) as client:
        original = client.post(f"{SHOW}/canon").json()
        entry = original["entries"][0]
        client.patch(f"{SHOW}/entries/{entry['id']}", json={"description": "A different coat."})
        state_file = exports / "state.json"
        state = json.loads(state_file.read_text())
        newer = exports / "gen-3-0123456789ab"
        shutil.copytree(exports / state["generation"], newer)
        new_canon = newer / "canon.md"
        new_canon.write_text(new_canon.read_text() + "\nEsme works through the night.\n")
        client.post(f"{SHOW}/canon")
        assert len(calls) == 1
        state["generation"] = newer.name
        state_file.write_text(json.dumps(state))
        updated = client.post(f"{SHOW}/canon").json()
        assert len(calls) == 2
        assert "works through the night" in calls[-1]
        assert updated["entries"][0]["id"] == entry["id"]
        assert updated["entries"][0]["description"] == "A different coat."

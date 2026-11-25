import base64
import io
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient as StarletteTestClient
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


def TestClient(app):
    return StarletteTestClient(app, base_url="http://localhost:4751")


def test_only_local_hosts_are_allowed(folders):
    with TestClient(create_app(*folders)) as client:
        for host in ("127.0.0.1:4751", "localhost:4751"):
            assert client.get("/api/ready", headers={"Host": host}).status_code == 200
        assert client.get("/api/ready", headers={"Host": "testserver"}).status_code == 400


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
        added = client.post(f"{SHOW}/entries", json={"name": "Pella Rook", "kind": "person", "description": "A yellow coat."}).json()
        assert client.patch(f"{SHOW}/entries/{added['id']}", json={"name": "Pella Reed"}).json()["name"] == "Pella Reed"


def test_description_is_required_and_unchanged_draft_keeps_its_source(folders):
    def no_fal(request):
        pytest.fail("A blank description must never reach Fal.")

    fal = Fal(transport=httpx.MockTransport(no_fal))
    with TestClient(create_app(*folders, canon_reader=lambda *_: CANON, fal=fal)) as client:
        show = client.post(f"{SHOW}/canon").json()
        orin = show["entries"][1]
        entry = f"{SHOW}/entries/{orin['id']}"
        for fields in ({}, {"description": "  "}):
            assert client.post(f"{SHOW}/entries", json={"name": "Pella Rook", "kind": "person", **fields}).status_code == 422

        for method, path in (("PATCH", entry), ("POST", f"{entry}/make")):
            response = client.request(method, path, json={"description": "  "})
            assert response.status_code == 422
            assert response.json()["detail"] == "Write a description before saving or making pictures."

        saved = client.patch(entry, json={"name": orin["name"], "description": orin["description"]}).json()
        assert saved["description"] == orin["description"]
        assert saved["drafted"] and saved["source"] == "Drafted, not from canon"
        assert client.get(SHOW).json()["entries"] == show["entries"]


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
        state_file = library / PROJECT / "show.json"
        kept_file = library / PROJECT / show["entries"][0]["id"] / "kept" / first["id"]
        before_rejection = state_file.read_bytes()
        assert client.patch(first["url"], json={"kept": False, "main": True}).status_code == 409
        assert state_file.read_bytes() == before_rejection
        assert kept_file.read_bytes() == png

        scene = "At a desk, lit by a lamp, looking up."
        more = client.post(f"{entry}/make", json={"description": "Esme in a coat.", "scene": scene}).json()
        assert more["description"] == "Esme in a coat."
        assert all(picture["scene"] == scene for picture in more["pictures"][4:])
        assert len(more["pictures"]) == 8
        assert Decimal(more["spent"]) - Decimal(made["spent"]) == Decimal("0.19")
        state = json.loads((library / PROJECT / "show.json").read_text())
        assert Decimal(state["estimated"]) == Decimal("0.20")
        endpoint, payload = requests[-1]
        assert endpoint == "/" + MODEL.edit
        assert "Show the same person, preserving the face and identity from the reference picture." in payload["prompt"]
        assert "Esme in a coat." in payload["prompt"] and scene in payload["prompt"]
        assert "across the batch" not in payload["prompt"]
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
        prompt = requests[-1][1]["prompt"]
        assert "Show the same person" in prompt and "Esme indoors." in prompt
        assert all(word in prompt for word in ("angle", "framing", "expression", "across the batch"))
        ref = base64.b64decode(requests[-1][1]["image_urls"][0].split(",", 1)[1])
        assert Image.open(io.BytesIO(ref)).getpixel((0, 0)) != (98, 123, 129)

    with TestClient(create_app(root, library, fal=fal)) as client:
        saved = client.get(SHOW).json()
        assert Decimal(saved["spent"]) == Decimal("0.59") and saved["estimated"]
        pictures = saved["entries"][0]["pictures"]
        assert len(pictures) == 13
        assert all("scene" not in picture for picture in pictures[:4])
        assert all(picture["scene"] == scene for picture in pictures[4:8])
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


def test_voices_sound_and_credits(folders, monkeypatch, png):
    from lab.elevenlabs import ElevenLabs

    audio = b"ID3 fixture audio"
    description = "Middle-aged, with a coastal accent and a low, dry voice."
    drafts = []
    used = 100
    awaiting_usage = set()

    def draft_voice(canon, outline, person):
        assert "clapboard room" in canon
        drafts.append(person["name"])
        return description

    def eleven(request):
        nonlocal used
        path = request.url.path
        if request.url.host == "fixture.invalid":
            assert "xi-api-key" not in request.headers
            return httpx.Response(200, content=audio)
        assert request.headers["xi-api-key"] == "fixture"
        if path == "/v1/user/subscription":
            return httpx.Response(200, json={"character_count": used, "character_limit": 1000})
        if path == "/v1/workspace/analytics/query/usage-by-product-over-time":
            query = json.loads(request.content)
            assert query["start_time"] <= query["end_time"]
            rows = [["Voice Generation", 100, 1], ["Music", 50, 1]]
            if awaiting_usage:
                rows = [row for row in rows if row[0] not in awaiting_usage]
                awaiting_usage.clear()
            return httpx.Response(200, json={
                "columns": ["product_type", "total_usage", "usage_count"],
                "rows": rows,
            })
        voice = {"voice_id": "library-voice", "name": "Library voice",
                 "preview_url": "https://fixture.invalid/sample.mp3"}
        if path == "/v2/voices":
            return httpx.Response(200, json={"voices": [voice], "has_more": False})
        if path == "/v1/voices/library-voice":
            return httpx.Response(200, json=voice)
        payload = json.loads(request.content)
        if path == "/v1/text-to-voice/design":
            assert payload["voice_description"] == description
            assert payload["model_id"] == "eleven_multilingual_ttv_v2"
            awaiting_usage.add("Voice Generation")
            used += 100
            return httpx.Response(200, json={"previews": [
                {"generated_voice_id": name, "audio_base_64": base64.b64encode(audio).decode()}
                for name in ("first", "second", "third")
            ]})
        if path == "/v1/text-to-voice":
            assert payload["generated_voice_id"] == "second"
            assert payload["voice_description"] == description
            return httpx.Response(200, json={"voice_id": "new-voice", "name": payload["voice_name"]})
        if path == "/v1/text-to-speech/new-voice":
            assert payload == {"text": "Wait for the tide.", "model_id": "eleven_flash_v2_5"}
            used += 9
            return httpx.Response(200, content=audio, headers={"character-cost": "9"})
        if path == "/v1/sound-generation":
            assert payload["model_id"] == "eleven_text_to_sound_v3"
            assert payload["text"] == "A bell in the fog."
            used += 40
            return httpx.Response(200, content=audio, headers={"character-cost": "40"})
        assert path == "/v1/music"
        assert payload == {"prompt": "A short piano phrase.", "music_length_ms": 3000, "model_id": "music_v2_5"}
        used += 50
        return httpx.Response(200, content=audio)

    root, library = folders
    provider = ElevenLabs(httpx.MockTransport(eleven))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fixture")
    with TestClient(create_app(root, library, canon_reader=lambda *_: CANON,
                               eleven=provider, voice_reader=draft_voice)) as client:
        show = client.post(f"{SHOW}/canon").json()
        person, other = show["entries"][:2]
        entry = f"{SHOW}/entries/{person['id']}"
        picture = client.post(f"{entry}/import", content=png).json()
        assert client.get(picture["url"]).headers["content-type"] == "image/png"
        old = json.loads((library / PROJECT / "show.json").read_text())
        assert "voice" not in old["entries"][0] and "sound" not in old
        loaded = client.get(SHOW).json()
        assert loaded["entries"][0]["pictures"] == [picture]
        assert loaded["credits_used"] == 0 and loaded["sound"] == {}
        assert drafts == []
        client.patch(SHOW, json={"limit": "0"})
        assert client.get(f"{SHOW}/audio/credits").json()["remaining"] == 900
        for _ in range(2):
            assert client.post(f"{entry}/voice/draft").json()["description"] == description
        assert drafts == [person["name"]]

        previews = client.post(f"{entry}/voice/design", json={"text": description}).json()
        assert len(previews) == 3
        assert client.get(f"{SHOW}/audio/credits").json()["credits_partial"]
        assert client.get(f"{SHOW}/audio/credits").json()["credits_used"] == 100
        assert client.get(previews[1]["url"]).content == audio
        picked = client.post(f"{entry}/voice/pick", json={"preview": previews[1]["id"]}).json()
        assert picked["id"] == "new-voice"
        voices = client.get(f"{SHOW}/audio/voices").json()
        assert voices == [{"id": "library-voice", "name": "Library voice", "sample": True}]
        other_entry = f"{SHOW}/entries/{other['id']}"
        sample = client.post(f"{other_entry}/voice/listen", json={"voice_id": "library-voice"}).json()
        assert client.get(sample["url"]).content == audio
        assert client.post(f"{other_entry}/voice/pick", json={"voice_id": "library-voice"}).json()["id"] == "library-voice"

        take = client.post(f"{entry}/takes", json={"text": "Wait for the tide."}).json()
        effect = client.post(f"{SHOW}/sound/effects", json={"text": "A bell in the fog."}).json()
        music = client.post(f"{SHOW}/sound/music", json={"text": "A short piano phrase.", "seconds": 3}).json()
        credits = client.get(f"{SHOW}/audio/credits").json()
        assert credits["credits_used"] == 199 and not credits["credits_partial"]
        for clip in (take, effect, music):
            assert client.get(clip["url"], headers={"Host": "elsewhere"}).status_code == 400
            assert client.get(clip["url"], headers={"Range": "bytes=0-2"}).content == b"ID3"
            assert client.patch(clip["url"], json={"kept": True}).json()["kept"]
            assert (library / PROJECT / "audio/kept" / clip["id"]).read_bytes() == audio
        for path, extension, media_type in (
            (f"{entry}/takes", "mp3", "audio/mpeg"),
            (f"{SHOW}/sound/effects", "aiff", "audio/aiff"),
            (f"{SHOW}/sound/music", "aif", "audio/aiff"),
        ):
            response = client.post(f"{path}/import", params={"filename": f"Outside take.{extension}"}, content=audio)
            assert response.status_code == 201
            imported = response.json()
            playback = client.get(imported["url"])
            assert imported["kept"] and playback.content == audio
            assert playback.headers["content-type"] == media_type
        assert "Wait for the tide" in take["id"] and person["name"] in take["id"]

    with TestClient(create_app(root, library, eleven=provider)) as client:
        saved = client.get(SHOW).json()
        assert saved["spent"] == saved["limit"] == "0.00"
        assert saved["entries"][0]["voice"]["id"] == "new-voice"
        assert saved["entries"][1]["voice"]["id"] == "library-voice"
        assert len(saved["entries"][0]["takes"]) == 2
        assert len(saved["sound"]["effects"]) == len(saved["sound"]["music"]) == 2
        assert saved["credits_used"] == 199 and not saved["credits_partial"]
        assert client.get(f"{SHOW}/audio/credits").json()["remaining"] == 701


def test_audio_import_size_limit(folders, monkeypatch):
    monkeypatch.setattr("lab.audio.MAX_IMPORT_BYTES", 10, raising=False)
    root, library = folders
    with TestClient(create_app(root, library)) as client:
        path = f"{SHOW}/sound/music/import"
        params = {"filename": "Outside take.wav"}
        response = client.post(path, params=params, content=iter([b"12345", b"678901"]))
        assert response.status_code == 413
        assert response.json()["detail"] == "Choose an audio file that is 200 MB or smaller."
        assert not (library / PROJECT / "audio").exists()

        response = client.post(path, params=params, content=iter([b"12345", b"67890"]))
        assert response.status_code == 201
        assert client.get(response.json()["url"]).content == b"1234567890"


def test_audio_missing_key(folders, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("lab.elevenlabs.dotenv_values", lambda *_, **__: {})
    with TestClient(create_app(*folders)) as client:
        message = "ElevenLabs needs a key before it can make audio."
        assert client.get(f"{SHOW}/audio/credits").json()["notice"] == message
        response = client.post(f"{SHOW}/sound/effects", json={"text": "A bell in the fog."})
        assert response.status_code == 502 and response.json()["detail"] == message


@pytest.fixture
def footage_fal(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fixture")
    submissions = []
    status = {"status": "IN_PROGRESS"}
    video = b"fixture mp4 video"

    def queue(request):
        if request.url.host == "fixture.invalid":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=video)
        assert request.headers["authorization"] == "Key fixture"
        if request.method == "POST":
            submissions.append((request.url.path, json.loads(request.content)))
            job = f"https://queue.fal.run/fixture/{len(submissions)}"
            return httpx.Response(200, json={"status_url": job + "/status", "response_url": job})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json=status)
        model, _ = submissions[int(request.url.path.rsplit("/", 1)[1]) - 1]
        headers = {"X-Fal-Billable-Units": "4.75"} if model.endswith(("/image-to-video", "/reference-to-video")) else {}
        return httpx.Response(200, headers=headers, json={"video": {"url": "https://fixture.invalid/shot.mp4"}})

    return Fal(httpx.MockTransport(queue)), submissions, status, video


def test_footage_from_a_kept_picture_and_words_groups_takes(folders, footage_fal, png, monkeypatch):
    fal, submissions, status, video = footage_fal
    status["status"] = "COMPLETED"
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **_: opened.append(args))
    with TestClient(create_app(*folders, canon_reader=lambda *_: CANON, fal=fal)) as client:
        show = client.post(f"{SHOW}/canon").json()
        place = show["entries"][2]
        entry = f"{SHOW}/entries/{place['id']}"
        picture = client.post(entry + "/import", content=png).json()
        path = SHOW + "/footage"
        fields = {"description": "The tide rises under the office.", "seconds": 5}

        queued = client.post(path, json={**fields, "picture": picture["id"]})
        assert queued.status_code == 202
        assert queued.json()["spent"] == "0.35"
        assert queued.json()["footage"]["prices"]["plain"] == {"5": "0.35", "10": "0.70"}
        assert queued.json()["footage"]["pending_jobs"] == [
            {"title": fields["description"], "from": place["name"]},
        ]
        first = client.post(path + "/check").json()
        assert first["spent"] == "0.33" and not first["estimated"]
        queued = client.post(path, json=fields)
        assert queued.status_code == 202 and queued.json()["spent"] == "0.68"
        second = client.post(path + "/check").json()
        assert second["spent"] == "0.68" and second["estimated"]
        (shot,) = second["footage"]["shots"]
        assert shot["title"] == fields["description"]
        reference, words = shot["clips"]
        assert reference["from"] == place["name"] and not words.get("from")
        kling = "/fal-ai/kling-video/v2.6/pro/"
        image_call, text_call = submissions
        assert image_call[0] == kling + "image-to-video"
        assert {key: value for key, value in image_call[1].items() if key != "start_image_url"} == {
            "prompt": fields["description"], "duration": "5", "generate_audio": False,
        }
        assert base64.b64decode(image_call[1]["start_image_url"].split(",", 1)[1]) == client.get(picture["url"]).content
        assert text_call == (kling + "text-to-video",
                             {"prompt": fields["description"], "duration": "5", "generate_audio": False})

        url = reference["url"]
        assert client.get(url, headers={"Host": "elsewhere"}).status_code == 400
        response = client.get(url, headers={"Range": "bytes=2-6"})
        assert response.status_code == 206 and response.content == video[2:7]
        assert client.patch(url, json={"kept": True}).json()["kept"]
        kept = folders[1] / PROJECT / "footage/kept" / reference["id"]
        assert kept.read_bytes() == video and kept.name.startswith("The tide rises")
        assert client.post(path + "/finder").status_code == 204
        assert opened == [["open", str(kept.parent)]]
        assert not client.patch(url, json={"kept": False}).json()["kept"]
        assert not kept.exists()


@pytest.mark.parametrize("has_start", [False, True])
def test_footage_with_kept_references(folders, footage_fal, png, has_start):
    fal, submissions, status, _ = footage_fal
    path = SHOW + "/footage"
    with TestClient(create_app(*folders, canon_reader=lambda *_: CANON, fal=fal)) as client:
        show = client.post(SHOW + "/canon").json()
        person, _, place, _ = show["entries"]
        portrait = client.post(f"{SHOW}/entries/{person['id']}/import", content=png).json()
        with io.BytesIO() as stream:
            Image.new("RGB", (20, 20), "#ffffff").save(stream, format="PNG")
            landscape = client.post(f"{SHOW}/entries/{place['id']}/import", content=stream.getvalue()).json()

        description = f"Locked wide shot. {person['name'].lower()} walks into the shot"
        references = [portrait]
        names = [person["name"]]
        expected_prompt = "Locked wide shot. @Image1 walks into the shot"
        if not has_start:
            description += f" at {place['name']}"
            expected_prompt += " at @Image2"
            references.append(landscape)
            names.append(place["name"])
        queued = client.post(path, json={"description": description, "seconds": 5,
                             "picture": landscape["id"] if has_start else None,
                             "references": [picture["id"] for picture in references]})
        assert queued.status_code == 202
        assert queued.json()["spent"] == "0.56"
        assert queued.json()["footage"]["prices"]["references"] == {"5": "0.560", "10": "1.120"}
        origin = place["name"] if has_start else None
        assert queued.json()["footage"]["pending_jobs"] == [
            {"title": description, "from": origin, "with": names},
        ]

        (model, payload), = submissions
        assert model == "/fal-ai/kling-video/o3/pro/reference-to-video"
        assert payload["prompt"] == expected_prompt
        assert payload["duration"] == "5" and payload["generate_audio"] is False
        assert [base64.b64decode(url.split(",", 1)[1]) for url in payload["image_urls"]] == [
            client.get(picture["url"]).content for picture in references
        ]
        if has_start:
            assert base64.b64decode(payload["start_image_url"].split(",", 1)[1]) == client.get(landscape["url"]).content
        else:
            assert "start_image_url" not in payload

        status["status"] = "COMPLETED"
        checked = client.post(path + "/check").json()
        assert checked["spent"] == "0.53" and not checked["estimated"]
        shot, = checked["footage"]["shots"]
        clip, = shot["clips"]
        assert shot["title"] == description
        assert clip.get("from") == origin and clip["with"] == names


def test_pending_footage_survives_reload_and_uses_shared_limit(folders, footage_fal):
    fal, submissions, status, video = footage_fal
    path = SHOW + "/footage"
    fields = {"description": "Mist moves over the steps.", "seconds": 10}
    with TestClient(create_app(*folders, fal=fal)) as client:
        client.patch(SHOW, json={"limit": "0.69"})
        assert client.post(path, json=fields).status_code == 409
        assert not submissions
        client.patch(SHOW, json={"limit": "0.70"})
        assert client.post(path, json=fields).status_code == 202
        checked = client.post(path + "/check").json()
        assert checked["footage"]["pending"] and checked["spent"] == "0.70"
        assert checked["footage"]["pending_jobs"] == [{"title": fields["description"], "from": None}]
        assert client.post(path, json=fields).status_code == 409

    state_file = folders[1] / PROJECT / "show.json"
    old = json.loads(state_file.read_text())
    clip = {"id": "Old take.mp4", "url": path + "/clips/Old%20take.mp4", "kept": True, "from": "Bell Steps"}
    clip_folder = state_file.parent / "footage"
    clip_folder.mkdir()
    (clip_folder / clip["id"]).write_bytes(video)
    old["footage"]["shots"] = [{"title": "The tide turns.", "clips": [clip]}]
    assert "with" not in next(iter(old["footage"]["jobs"].values()))
    state_file.write_text(json.dumps(old))

    with TestClient(create_app(*folders, fal=fal)) as client:
        show = client.get(SHOW).json()
        assert show["footage"]["shots"][0]["clips"] == [clip]
        assert client.get(clip["url"]).content == video
        assert show["footage"]["pending"] and show["spent"] == "0.70"
        assert show["footage"]["pending_jobs"] == [{"title": fields["description"], "from": None}]
        status["status"] = "COMPLETED"
        checked = client.post(path + "/check").json()
        assert not checked["footage"]["pending"] and checked["spent"] == "0.70"
        assert checked["footage"]["pending_jobs"] == []
        assert len(checked["footage"]["shots"][1]["clips"]) == 1
        assert client.post(path + "/check").json() == checked
        assert len(submissions) == 1


def test_footage_import_and_old_show(folders, png, monkeypatch):
    path = SHOW + "/footage"
    monkeypatch.setattr("lab.footage.MAX_IMPORT_BYTES", 20, raising=False)
    with TestClient(create_app(*folders, canon_reader=lambda *_: CANON)) as client:
        entry = client.post(SHOW + "/canon").json()["entries"][0]
        picture = client.post(f"{SHOW}/entries/{entry['id']}/import", content=png).json()
        state_file = folders[1] / PROJECT / "show.json"
        old = json.loads(state_file.read_text())
        old["entries"][0].update(voice={"id": "old", "name": "Old voice"}, takes=[{"title": "Wait for the tide."}])
        old["sound"] = {"music": [{"title": "A piano phrase."}]}
        state_file.write_text(json.dumps(old))
        before = state_file.read_bytes()
        loaded = client.get(SHOW).json()
        assert loaded["footage"]["shots"] == [] and not loaded["footage"]["pending"]
        assert loaded["footage"]["pending_jobs"] == []
        assert loaded["entries"][0]["pictures"] == [picture]
        assert loaded["entries"][0]["voice"]["id"] == "old"
        assert loaded["entries"][0]["takes"] == old["entries"][0]["takes"]
        assert loaded["sound"] == old["sound"]
        assert state_file.read_bytes() == before

        params = {"filename": "Outside take.mov"}
        assert client.post(path + "/import", params=params, content=b"x" * 21).status_code == 413
        response = client.post(path + "/import", params=params, content=b"x" * 20)
        assert response.status_code == 201
        clip = response.json()
        assert clip["kept"] and client.get(clip["url"]).content == b"x" * 20
        assert client.get(clip["url"]).headers["content-type"] == "video/quicktime"
        assert (folders[1] / PROJECT / "footage/kept" / clip["id"]).is_file()
        assert client.get(SHOW).json()["footage"]["shots"][0]["title"] == "Outside take"

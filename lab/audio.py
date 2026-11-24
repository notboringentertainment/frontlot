import asyncio
import base64
import re
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .elevenlabs import EFFECTS, MUSIC, VOICE_DESIGN, ElevenLabsError

SHOW = "/api/shows/{project}"
PERSON = SHOW + "/entries/{entry_id}"
AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
               ".aac": "audio/aac", ".aiff": "audio/aiff", ".flac": "audio/flac",
               ".ogg": "audio/ogg", ".opus": "audio/ogg", ".webm": "audio/webm"}


class Words(BaseModel):
    text: str
    seconds: float | None = Field(default=None, ge=0.5, le=600)


class VoiceChoice(BaseModel):
    preview: str | None = None
    voice_id: str | None = None


class KeepInput(BaseModel):
    kept: bool


def credit_state(state):
    return {"credits_used": state.get("credits_used", 0),
            "credits_partial": bool(state.get("pending_credits"))}


def audio_state(state):
    return {"sound": state.get("sound", {}), **credit_state(state)}


def voice_state(entry):
    return {key: entry.get(key, default) for key, default in
            (("voice", None), ("voice_description", ""), ("voice_drafted", False),
             ("voice_previews", []), ("takes", []))}


def register_audio(app, library, kit_for, eleven, voice_reader):
    @app.exception_handler(ElevenLabsError)
    async def provider_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=502)

    def person(state, entry_id):
        entry = library.entry(state, entry_id)
        if entry["kind"] != "person":
            raise HTTPException(404, "Only people have voices.")
        return entry

    def charge_for(project):
        def charge(amount, pending):
            state = library.load(project)
            if pending is None:
                state["credits_used"] = state.get("credits_used", 0) + amount
            else:
                state.setdefault("pending_credits", []).append(pending)
            library.save(project, state)
        return charge

    async def settle_credits(project):
        for pending in library.load(project).get("pending_credits", []):
            amount = await eleven.usage(**pending)
            if amount is None:
                continue
            state = library.load(project)
            if pending in state["pending_credits"]:
                state["pending_credits"].remove(pending)
                state["credits_used"] = state.get("credits_used", 0) + amount
                library.save(project, state)

    def folder(project):
        return library.root / project / "audio"

    def audio_file(directory, name):
        path = directory / name
        if (not path.is_file() or path.resolve().parent != directory.resolve()
                or not path.resolve().is_relative_to(library.root.resolve())):
            raise HTTPException(404, "This audio is no longer in the show.")
        return FileResponse(path, media_type=AUDIO_TYPES[path.suffix],
                            headers={"X-Content-Type-Options": "nosniff"})

    def save_preview(project, data):
        directory = folder(project) / "previews"
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}.mp3"
        (directory / name).write_bytes(data)
        return {"id": name, "url": f"/api/shows/{project}/audio/previews/{name}"}

    def save_clip(project, target, title, data, suffix=".mp3", kept=False, person_id=None):
        state = library.load(project)
        # Keep a paid take even if the entry was removed while ElevenLabs was working.
        entry = next((item for item in state["entries"] if item["id"] == person_id), None)
        clips = entry.setdefault("takes", []) if entry else state.setdefault("sound", {}).setdefault(target, [])
        label = f"{entry['name']} - {title}" if entry else title
        stem = re.sub(r"[^\w .'-]", " ", label).strip(" .")[:58].rstrip() or "Audio"
        directory = folder(project)
        directory.mkdir(parents=True, exist_ok=True)
        number = 1
        while (directory / (name := f"{stem} - {number}{suffix}")).exists():
            number += 1
        (directory / name).write_bytes(data)
        clip = {"id": name, "title": title, "kept": False,
                "url": f"/api/shows/{project}/audio/clips/{quote(name)}"}
        if kept:
            library.keep(directory, clip, True)
        clips.append(clip)
        library.save(project, state)
        return clip

    async def import_clip(project, target, filename, request, person_id=None):
        path = Path(filename)
        suffix = path.suffix.lower()
        data = await request.body()
        if suffix not in AUDIO_TYPES or not data:
            raise HTTPException(422, "Choose an audio file, such as an MP3 or WAV.")
        return save_clip(project, target, path.stem, data, suffix, kept=True, person_id=person_id)

    def words(value, minimum=1, maximum=1000):
        value = value.strip()
        if not minimum <= len(value) <= maximum:
            message = "Write something to make audio from."
            if value or minimum > 1:
                message = f"Use {minimum} to {maximum} characters."
            raise HTTPException(422, message)
        return value

    @app.get(SHOW + "/audio/credits")
    async def credits(project: str):
        kit_for(project)
        try:
            eleven.headers()
        except ElevenLabsError as error:
            return {"remaining": None, "notice": str(error), **credit_state(library.load(project))}
        await settle_credits(project)
        count, limit = await eleven.balance()
        remaining = max(0, limit - count) if isinstance(limit, int) and isinstance(count, int) else None
        return {"remaining": remaining, "notice": "", **credit_state(library.load(project))}

    @app.get(SHOW + "/audio/voices")
    async def voices(project: str):
        kit_for(project)
        return [{"id": voice["voice_id"], "name": voice["name"], "sample": bool(voice.get("preview_url"))}
                for voice in await eleven.voices()]

    @app.post(PERSON + "/voice/draft")
    async def draft(project: str, entry_id: str):
        kit = kit_for(project)
        entry = person(library.load(project), entry_id)
        if not entry.get("voice_description"):
            try:
                canon, outline, _ = kit.sources()
                description = await asyncio.to_thread(voice_reader, canon, outline, entry)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
                raise HTTPException(502, "Claude couldn’t draft a voice. You can describe it yourself.") from None
            state = library.load(project)
            entry = person(state, entry_id)
            if not entry.get("voice_description"):
                entry.update(voice_description=description, voice_drafted=True)
                library.save(project, state)
        return {"description": entry["voice_description"], "drafted": entry["voice_drafted"]}

    def update_description(entry, text):
        if text != entry.get("voice_description"):
            entry.update(voice_description=text, voice_drafted=False)

    @app.post(PERSON + "/voice/design")
    async def design(project: str, entry_id: str, fields: Words):
        kit_for(project)
        person(library.load(project), entry_id)
        text = words(fields.text, minimum=20)
        result = await eleven.generate("/v1/text-to-voice/design", {
            "voice_description": text, "model_id": VOICE_DESIGN, "auto_generate_text": True,
        }, charge_for(project), product="Voice Generation")
        state = library.load(project)
        entry = person(state, entry_id)
        previews = []
        for preview in result.json()["previews"]:
            clip = save_preview(project, base64.b64decode(preview["audio_base_64"]))
            previews.append({**clip, "generated_voice_id": preview["generated_voice_id"], "description": text})
        for preview in entry.get("voice_previews", []):
            (folder(project) / "previews" / preview["id"]).unlink(missing_ok=True)
        update_description(entry, text)
        entry["voice_previews"] = previews
        library.save(project, state)
        return previews

    @app.post(PERSON + "/voice/pick")
    async def pick_voice(project: str, entry_id: str, fields: VoiceChoice):
        kit_for(project)
        entry = person(library.load(project), entry_id)
        if fields.preview:
            preview = next((item for item in entry.get("voice_previews", []) if item["id"] == fields.preview), None)
            if preview is None:
                raise HTTPException(404, "This preview is no longer available.")
            result = await eleven.request("POST", "/v1/text-to-voice", json={
                "voice_name": entry["name"],
                "voice_description": preview["description"],
                "generated_voice_id": preview["generated_voice_id"],
            })
            voice = result.json()
        elif fields.voice_id:
            voice = await eleven.voice(fields.voice_id)
        else:
            raise HTTPException(422, "Choose a voice first.")
        state = library.load(project)
        entry = person(state, entry_id)
        entry["voice"] = {"id": voice["voice_id"], "name": voice["name"]}
        library.save(project, state)
        return entry["voice"]

    @app.post(PERSON + "/voice/listen")
    async def listen(project: str, entry_id: str, fields: VoiceChoice):
        kit_for(project)
        person(library.load(project), entry_id)
        if not fields.voice_id:
            raise HTTPException(422, "Choose a voice first.")
        voice = await eleven.voice(fields.voice_id)
        if voice.get("preview_url"):
            data = await eleven.sample(voice["preview_url"])
        else:
            result = await eleven.speech(fields.voice_id, "Hello. This is how I sound.",
                                         charge_for(project))
            data = result.content
        return save_preview(project, data)

    @app.get(SHOW + "/audio/previews/{name}")
    async def preview_file(project: str, name: str):
        kit_for(project)
        if not re.fullmatch(r"[0-9a-f]{32}\.mp3", name):
            raise HTTPException(404, "This preview is no longer available.")
        return audio_file(folder(project) / "previews", name)

    @app.post(PERSON + "/takes")
    async def read_line(project: str, entry_id: str, fields: Words):
        kit_for(project)
        entry = person(library.load(project), entry_id)
        if not entry.get("voice"):
            raise HTTPException(422, "Choose a voice before reading a line.")
        text = words(fields.text, maximum=40000)
        result = await eleven.speech(entry["voice"]["id"], text, charge_for(project))
        return save_clip(project, "takes", text, result.content, person_id=entry_id)

    @app.post(PERSON + "/takes/import", status_code=201)
    async def import_take(project: str, entry_id: str, filename: str, request: Request):
        kit_for(project)
        person(library.load(project), entry_id)
        return await import_clip(project, "takes", filename, request, person_id=entry_id)

    @app.post(SHOW + "/sound/{kind}")
    async def make_sound(project: str, kind: Literal["effects", "music"], fields: Words):
        kit_for(project)
        if kind == "effects":
            text = words(fields.text)
            path = "/v1/sound-generation"
            payload = {"text": text, "model_id": EFFECTS}
            if fields.seconds is not None:
                if fields.seconds > 30:
                    raise HTTPException(422, "Choose a length of up to 30 seconds for a sound effect.")
                payload["duration_seconds"] = fields.seconds
        else:
            text = words(fields.text, maximum=4100)
            if fields.seconds is None or fields.seconds < 3:
                raise HTTPException(422, "Choose a music length between 3 and 600 seconds.")
            path = "/v1/music"
            payload = {"prompt": text, "music_length_ms": round(fields.seconds * 1000), "model_id": MUSIC}
        product = "Music" if kind == "music" else "Sound Effects"
        result = await eleven.generate(path, payload, charge_for(project), product=product)
        return save_clip(project, kind, text, result.content)

    @app.post(SHOW + "/sound/{kind}/import", status_code=201)
    async def import_sound(project: str, kind: Literal["effects", "music"], filename: str, request: Request):
        kit_for(project)
        return await import_clip(project, kind, filename, request)

    def get_clip(state, name):
        groups = list(state.get("sound", {}).values())
        groups.extend(entry.get("takes", []) for entry in state["entries"])
        for clips in groups:
            for clip in clips:
                if clip["id"] == name:
                    return clip
        raise HTTPException(404, "This audio is no longer in the show.")

    @app.get(SHOW + "/audio/clips/{name}")
    async def clip_file(project: str, name: str):
        kit_for(project)
        clip = get_clip(library.load(project), name)
        return audio_file(folder(project), clip["id"])

    @app.patch(SHOW + "/audio/clips/{name}")
    async def keep_clip(project: str, name: str, change: KeepInput):
        kit_for(project)
        state = library.load(project)
        clip = get_clip(state, name)
        if clip["kept"] != change.kept:
            library.keep(folder(project), clip, change.kept)
            library.save(project, state)
        return clip

    @app.post(SHOW + "/audio/finder", status_code=204)
    async def finder(project: str):
        kit_for(project)
        directory = folder(project) / "kept"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["open", str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise HTTPException(500, "The lab couldn’t open Finder.") from None

import asyncio
import re
import shutil
import subprocess
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryFile
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .audio import SHOW, KeepInput
from .fal import MAX_VIDEO_PICTURES, FalError, VideoFailed, video_price

FOOTAGE = SHOW + "/footage"
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
MAX_IMPORT_BYTES = 200_000_000
SHOT_LENGTHS = (5, 10)


class ShotInput(BaseModel):
    description: str
    seconds: Literal[5, 10]
    picture: str | None = None
    references: list[str] = []


def footage_state(state):
    footage = state.get("footage", {})
    pending_jobs = [
        {"title": job["title"], "from": job["from"], **({"with": job["with"]} if "with" in job else {})}
        for job in footage.get("jobs", {}).values()
    ]
    return {
        "shots": footage.get("shots", []),
        "pending": bool(pending_jobs),
        "pending_jobs": pending_jobs,
        "notice": footage.get("notice", ""),
        "prices": {
            mode: {seconds: str(video_price(mode == "references") * seconds) for seconds in SHOT_LENGTHS}
            for mode in ("plain", "references")
        },
        "max_pictures": MAX_VIDEO_PICTURES,
    }


def register_footage(app, library, kit_for, fal, making, spending):
    checking = defaultdict(asyncio.Lock)

    def view(state):
        return {"footage": footage_state(state), **spending(state)}

    def folder(project):
        return library.root / project / "footage"

    def save_clip(project, state, title, source, suffix, origin=None, kept=False, references=()):
        directory = folder(project)
        directory.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w .'-]", " ", title).strip(" .")[:58].rstrip() or "Clip"
        number = 1
        while (directory / (name := f"{stem} - {number}{suffix}")).exists():
            number += 1
        with (directory / name).open("wb") as destination:
            shutil.copyfileobj(source, destination)
        clip = {"id": name, "kept": False, "url": f"/api/shows/{project}/footage/clips/{quote(name)}"}
        if origin:
            clip["from"] = origin
        if references:
            clip["with"] = references
        if kept:
            library.keep(directory, clip, True)
        shots = state.setdefault("footage", {}).setdefault("shots", [])
        shot = next((item for item in shots if item["title"] == title), None)
        if shot is None:
            shot = {"title": title, "clips": []}
            shots.append(shot)
        shot["clips"].append(clip)
        return clip

    @app.post(FOOTAGE, status_code=202)
    async def make_clip(project: str, fields: ShotInput):
        kit_for(project)
        description = fields.description.strip()
        if not description:
            raise HTTPException(422, "Describe what happens before making a clip.")
        async with making[project]:
            state = library.load(project)
            reference_ids = list(dict.fromkeys(fields.references))
            if len(reference_ids) + bool(fields.picture) > MAX_VIDEO_PICTURES:
                raise HTTPException(422, f"Choose up to {MAX_VIDEO_PICTURES} pictures, including the starting picture.")
            unit_price = video_price(reference_ids)
            cost = unit_price * fields.seconds
            if Decimal(state["spent"]) + cost > Decimal(state["limit"]):
                raise HTTPException(409, "This clip would go over the spending limit. Raise the limit to make more clips.")
            kept = {
                picture["id"]: (entry["name"], library.pictures(project, entry) / picture["id"])
                for entry in state["entries"] if not entry.get("deleted")
                for picture in entry["pictures"] if picture["kept"]
            }
            if fields.picture and fields.picture not in kept:
                raise HTTPException(404, "This starting picture is no longer kept.")
            if any(picture not in kept for picture in reference_ids):
                raise HTTPException(404, "One of the people and places pictures is no longer kept.")
            origin, start = kept.get(fields.picture, (None, None))
            references = [kept[picture] for picture in reference_ids]
            try:
                job = await fal.submit_video(description, fields.seconds, start, references)
            except FalError as error:
                raise HTTPException(502, str(error)) from None
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                raise HTTPException(502, "The footage service couldn’t start this clip. Check your connection and try again.") from None
            state = library.load(project)
            footage = state.setdefault("footage", {})
            footage.pop("notice", None)
            footage.setdefault("jobs", {})[uuid4().hex] = {
                **job, "title": description, "from": origin, "cost": str(cost),
                "unit_price": str(unit_price),
                **({"with": list(dict.fromkeys(name for name, _ in references))} if references else {}),
            }
            for total in ("spent", "estimated"):
                state[total] = str(Decimal(state[total]) + cost)
            library.save(project, state)
            return view(state)

    @app.post(FOOTAGE + "/check")
    async def check_clips(project: str):
        kit_for(project)
        notice = ""
        async with checking[project]:
            for job_id, job in library.load(project).get("footage", {}).get("jobs", {}).items():
                try:
                    result = await fal.video_result(job)
                    if result is None:
                        continue
                except VideoFailed as error:
                    result = None
                    notice = str(error)
                except (FalError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    notice = "The lab couldn’t check the clip yet. It will try again."
                    continue

                state = library.load(project)
                footage = state["footage"]
                reserved = Decimal(job["cost"])
                amount = estimated = Decimal(0)
                if result is not None:
                    data, units = result
                    try:
                        save_clip(project, state, job["title"], BytesIO(data), ".mp4", job["from"],
                                  references=job.get("with", ()))
                    except OSError:
                        notice = "The lab couldn’t save the clip yet. It will try again."
                        continue
                    amount = estimated = reserved
                    if units is not None:
                        amount, estimated = units * Decimal(job["unit_price"]), Decimal(0)
                else:
                    footage["notice"] = notice
                state["spent"] = str(Decimal(state["spent"]) + amount - reserved)
                state["estimated"] = str(Decimal(state["estimated"]) + estimated - reserved)
                del footage["jobs"][job_id]
                library.save(project, state)

            state = library.load(project)
            response = view(state)
            if notice:
                response["footage"]["notice"] = notice
            return response

    @app.post(FOOTAGE + "/import", status_code=201)
    async def import_clip(project: str, filename: str, request: Request):
        kit_for(project)
        path = Path(filename)
        suffix = path.suffix.lower()
        message = "Choose an MP4, MOV or WebM video."
        if suffix not in VIDEO_TYPES:
            raise HTTPException(422, message)
        with TemporaryFile() as source:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(413, "Choose a video that is 200 MB or smaller.")
                source.write(chunk)
            if not size:
                raise HTTPException(422, message)
            source.seek(0)
            state = library.load(project)
            clip = save_clip(project, state, path.stem, source, suffix, kept=True)
            library.save(project, state)
            return clip

    def get_clip(state, name):
        for shot in footage_state(state)["shots"]:
            for clip in shot["clips"]:
                if clip["id"] == name:
                    return clip
        raise HTTPException(404, "This clip is no longer in the show.")

    @app.get(FOOTAGE + "/clips/{name}")
    async def clip_file(project: str, name: str):
        kit_for(project)
        clip = get_clip(library.load(project), name)
        directory = folder(project)
        path = directory / clip["id"]
        resolved = path.resolve()
        if (not path.is_file() or resolved.parent != directory.resolve()
                or not resolved.is_relative_to(library.root.resolve())):
            raise HTTPException(404, "This clip is no longer in the show.")
        return FileResponse(path, media_type=VIDEO_TYPES[path.suffix],
                            headers={"X-Content-Type-Options": "nosniff"})

    @app.patch(FOOTAGE + "/clips/{name}")
    async def keep_clip(project: str, name: str, change: KeepInput):
        kit_for(project)
        state = library.load(project)
        clip = get_clip(state, name)
        if clip["kept"] != change.kept:
            library.keep(folder(project), clip, change.kept)
            library.save(project, state)
        return clip

    @app.post(FOOTAGE + "/finder", status_code=204)
    async def finder(project: str):
        kit_for(project)
        directory = folder(project) / "kept"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["open", str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise HTTPException(500, "The lab couldn’t open Finder.") from None

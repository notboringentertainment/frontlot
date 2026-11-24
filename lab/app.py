import asyncio
import io
import json
import os
import subprocess
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .canon import canon_entries, read_with_claude
from .fal import MODEL, Fal, FalError
from .kit import find_kits, read_json

STATIC = Path(__file__).parent / "static"
DEFAULT_BATCH = 4
DEFAULT_LIMIT = "25.00"


class EntryInput(BaseModel):
    name: str
    kind: Literal["person", "place"]
    description: str = ""


class EntryEdit(BaseModel):
    name: str | None = None
    description: str | None = None


class MakeInput(BaseModel):
    description: str


class LimitInput(BaseModel):
    limit: Decimal = Field(ge=0, decimal_places=2)


class PictureEdit(BaseModel):
    kept: bool | None = None
    main: Literal[True] | None = None


class Library:
    def __init__(self, root):
        self.root = root

    def load(self, project):
        try:
            return read_json(self.root / project / "show.json")
        except FileNotFoundError:
            return {"limit": DEFAULT_LIMIT, "spent": "0.00", "estimated": "0.00",
                    "entries": [], "readings": {}}

    def save(self, project, state):
        folder = self.root / project
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / "show.json.tmp"
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(folder / "show.json")

    def pictures(self, project, entry):
        return self.root / project / entry["id"]

    def entry(self, state, entry_id):
        for entry in state["entries"]:
            if entry["id"] == entry_id and not entry.get("deleted"):
                return entry
        raise HTTPException(404, "This entry is no longer in the show.")

    def add_picture(self, project, entry, data, kept=False):
        folder = self.pictures(project, entry)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}.png"
        path = folder / name
        path.write_bytes(data)
        picture = {"id": name, "kept": False}
        if kept:
            self.keep(folder, picture, True)
        entry["pictures"].append(picture)
        return picture

    @staticmethod
    def keep(folder, picture, kept):
        if kept:
            (folder / "kept").mkdir(exist_ok=True)
            os.link(folder / picture["id"], folder / "kept" / picture["id"])
        else:
            (folder / "kept" / picture["id"]).unlink(missing_ok=True)
        picture["kept"] = kept


def new_entry(**fields):
    return {"id": uuid4().hex, "quotes": [], "source": "", "drafted": False,
            "main": None, "pictures": [], **fields}


def create_app(projects_root=None, library_root=None, *, canon_reader=None, fal=None):
    home = Path.home()
    projects = Path(projects_root or os.getenv("FRONT_LOT_PROJECTS", home / "WriterOS Projects"))
    library = Library(Path(library_root or os.getenv(
        "FRONT_LOT_LIBRARY", home / "Library/Application Support/Front Lot Lab"
    )))
    canon_reader = canon_reader or read_with_claude
    fal = fal or Fal()
    reading = defaultdict(asyncio.Lock)
    making = defaultdict(asyncio.Lock)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def kit_for(project):
        for kit in find_kits(projects):
            if kit.id == project and kit.status == "current":
                return kit
        raise HTTPException(404, "This show’s canon isn’t ready in WriterOS yet.")

    def picture_view(project, entry, picture):
        return {**picture, "main": entry["main"] == picture["id"],
                "url": f"/api/shows/{project}/entries/{entry['id']}/pictures/{picture['id']}"}

    def entry_view(project, entry):
        return {key: entry[key] for key in ("id", "name", "kind", "description", "quotes", "source", "drafted")} | {
            "pictures": [picture_view(project, entry, picture) for picture in entry["pictures"]]
        }

    def spending(state):
        with localcontext() as context:
            context.rounding = ROUND_HALF_UP
            return {"spent": f"{Decimal(state['spent']):.2f}",
                    "limit": f"{Decimal(state['limit']):.2f}",
                    "estimated": Decimal(state["estimated"]) > 0}

    def show_view(kit, state):
        return {**kit.public(), **spending(state),
                "notice": state["readings"].get(kit.generation.name, ""),
                "entries": [entry_view(kit.id, entry) for entry in state["entries"] if not entry.get("deleted")]}

    @app.get("/")
    async def page():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/ready")
    async def ready():
        return {"front_lot_lab": True}

    @app.get("/api/shows")
    async def shows():
        return [kit.public() for kit in find_kits(projects)]

    @app.get("/api/shows/{project}")
    async def show(project: str):
        return show_view(kit_for(project), library.load(project))

    @app.post("/api/shows/{project}/canon")
    async def read_canon(project: str, again: bool = False):
        async with reading[project]:
            kit = kit_for(project)
            generation = kit.generation.name
            state = library.load(project)
            if generation in state["readings"] and not again:
                return show_view(kit, state)

            notice = ""
            found = []
            try:
                canon, outline, looks = kit.sources()
            except (OSError, ValueError, TypeError, AttributeError):
                notice = "The lab couldn’t open this show’s canon. You can add people and places by hand."
            else:
                try:
                    result = await asyncio.to_thread(canon_reader, canon, outline, looks)
                    found = list(canon_entries(result, canon, looks))
                except FileNotFoundError:
                    notice = "Claude isn’t available on this Mac. You can add people and places by hand."
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
                    notice = "Claude couldn’t read the canon. Try reading it again or add people and places by hand."

            state = library.load(project)
            by_name = {}
            for entry in state["entries"]:
                # Remember the original name after a rename or removal.
                by_name[(entry["kind"], entry.get("canon_name", entry["name"]).casefold())] = entry
            for fields in found:
                key = (fields["kind"], fields["name"].casefold())
                existing = by_name.get(key)
                if existing is None:
                    existing = new_entry(**fields, canon_name=fields["name"])
                    state["entries"].append(existing)
                    by_name[key] = existing
                elif not existing.get("deleted"):
                    existing["quotes"] = fields["quotes"]
                    if "canon_name" in existing and not existing.get("edited"):
                        for field in ("description", "source", "drafted"):
                            existing[field] = fields[field]
                    existing.setdefault("canon_name", fields["name"])
            state["readings"][generation] = notice
            library.save(project, state)
            return show_view(kit, state)

    @app.patch("/api/shows/{project}")
    async def set_limit(project: str, change: LimitInput):
        kit_for(project)
        state = library.load(project)
        state["limit"] = str(change.limit)
        library.save(project, state)
        return spending(state)

    @app.post("/api/shows/{project}/entries", status_code=201)
    async def add_entry(project: str, fields: EntryInput):
        kit_for(project)
        name = fields.name.strip()
        if not name:
            raise HTTPException(422, "Give this person or place a name.")
        state = library.load(project)
        entry = new_entry(name=name, kind=fields.kind, description=fields.description.strip(), edited=True)
        state["entries"].append(entry)
        library.save(project, state)
        return entry_view(project, entry)

    @app.patch("/api/shows/{project}/entries/{entry_id}")
    async def edit_entry(project: str, entry_id: str, change: EntryEdit):
        kit_for(project)
        state = library.load(project)
        entry = library.entry(state, entry_id)
        if change.name is not None:
            if not change.name.strip():
                raise HTTPException(422, "Give this person or place a name.")
            entry["name"] = change.name.strip()
        if change.description is not None:
            update_description(entry, change.description.strip())
        library.save(project, state)
        return entry_view(project, entry)

    def update_description(entry, description):
        if entry["description"] != description:
            entry.update(description=description, edited=True, source="", drafted=False)

    @app.delete("/api/shows/{project}/entries/{entry_id}", status_code=204)
    async def remove_entry(project: str, entry_id: str):
        kit_for(project)
        state = library.load(project)
        library.entry(state, entry_id)["deleted"] = True
        library.save(project, state)

    @app.post("/api/shows/{project}/entries/{entry_id}/make")
    async def make_pictures(project: str, entry_id: str, fields: MakeInput):
        async with making[project]:
            kit_for(project)
            state = library.load(project)
            entry = library.entry(state, entry_id)
            description = fields.description.strip()
            if not description:
                raise HTTPException(422, "Write a visual description first.")
            update_description(entry, description)
            library.save(project, state)
            cost = MODEL.usd * DEFAULT_BATCH
            if Decimal(state["spent"]) + cost > Decimal(state["limit"]):
                raise HTTPException(409, "This batch would go over the spending limit. Raise the limit to make more pictures.")

            # Snapshot the reference for this batch. Its gallery file stays in place when unkept.
            reference = library.pictures(project, entry) / entry["main"] if entry["main"] else None
            charged = Decimal(0)
            estimated_charge = Decimal(0)

            def charge(amount, *, estimated):
                nonlocal charged, estimated_charge
                latest = library.load(project)
                new_estimate = amount if estimated else Decimal(0)
                latest["spent"] = str(Decimal(latest["spent"]) + amount - charged)
                latest["estimated"] = str(Decimal(latest["estimated"]) + new_estimate - estimated_charge)
                library.save(project, latest)
                charged, estimated_charge = amount, new_estimate

            def save_picture(data):
                latest = library.load(project)
                target = next(item for item in latest["entries"] if item["id"] == entry_id)
                library.add_picture(project, target, data)
                library.save(project, latest)

            try:
                await fal.make(description, entry["kind"], DEFAULT_BATCH, reference, charge, save_picture)
            except FalError as error:
                raise HTTPException(502, str(error)) from None
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                message = "The picture service couldn’t finish. Check your connection and try again."
                if charged:
                    message = "The request went through, but some pictures may be missing. Check the pictures and total below."
                raise HTTPException(502, message) from None

            latest = library.load(project)
            return {**entry_view(project, library.entry(latest, entry_id)), **spending(latest)}

    @app.post("/api/shows/{project}/entries/{entry_id}/import", status_code=201)
    async def import_picture(project: str, entry_id: str, request: Request):
        kit_for(project)
        data = await request.body()
        try:
            with Image.open(io.BytesIO(data)) as image, io.BytesIO() as stream:
                image = ImageOps.exif_transpose(image).convert("RGBA")
                image.save(stream, "PNG")
                data = stream.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, "That file couldn’t be opened as a picture. Try a JPEG or PNG.") from None
        state = library.load(project)
        entry = library.entry(state, entry_id)
        picture = library.add_picture(project, entry, data, kept=True)
        library.save(project, state)
        return picture_view(project, entry, picture)

    picture_url = "/api/shows/{project}/entries/{entry_id}/pictures/{picture_id}"

    def get_picture(entry, picture_id):
        for picture in entry["pictures"]:
            if picture["id"] == picture_id:
                return picture
        raise HTTPException(404, "This picture is no longer in the show.")

    @app.get(picture_url)
    async def picture_file(project: str, entry_id: str, picture_id: str):
        kit_for(project)
        entry = library.entry(library.load(project), entry_id)
        picture = get_picture(entry, picture_id)
        return FileResponse(library.pictures(project, entry) / picture["id"], media_type="image/png")

    @app.patch(picture_url)
    async def keep_picture(project: str, entry_id: str, picture_id: str, change: PictureEdit):
        kit_for(project)
        state = library.load(project)
        entry = library.entry(state, entry_id)
        picture = get_picture(entry, picture_id)
        if change.kept is not None and change.kept != picture["kept"]:
            library.keep(library.pictures(project, entry), picture, change.kept)
            if not change.kept and entry["main"] == picture_id:
                entry["main"] = None
        if change.main:
            if not picture["kept"]:
                raise HTTPException(409, "Keep this picture before making it the main look.")
            entry["main"] = picture_id
        library.save(project, state)
        return picture_view(project, entry, picture)

    @app.post("/api/shows/{project}/entries/{entry_id}/finder", status_code=204)
    async def show_in_finder(project: str, entry_id: str):
        kit_for(project)
        entry = library.entry(library.load(project), entry_id)
        folder = library.pictures(project, entry) / "kept"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise HTTPException(500, "The lab couldn’t open Finder.") from None

    return app


app = create_app()

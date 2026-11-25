"""Plan a cut from kept pieces and render it locally."""

import asyncio
import json
import shutil
import subprocess
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from .audio import SHOW
from .canon import ask_claude
from .footage import SHAPE_NAMES

CUT = SHOW + "/cut"
FPS = 24
TOLERANCE = 0.125
SIZES = {
    "16:9": ((640, 360), (1920, 1080)),
    "9:16": ((360, 640), (1080, 1920)),
    "1:1": ((480, 480), (1080, 1080)),
}
FONT = Path(__file__).resolve().parents[1] / "assets/fonts/source-sans-3/SourceSans3-Semibold.otf"
KEEP_FIRST = "Keep a clip first. You can also keep any voice lines, sound effects or music you want Claude to use."
PLAN_ERROR = "Claude couldn't make that cut. Your last cut is still here."
FIRST_PLAN_ERROR = "Claude couldn't make that cut. Try again, or say it another way."
RENDER_ERROR = "The lab couldn’t make the file. Check that the kept clips and sounds still play, then try again."


class CutRequest(BaseModel):
    words: str
    shape: Literal["16:9", "9:16", "1:1"] = "16:9"


class ChangeRequest(BaseModel):
    words: str


class Picture(BaseModel):
    id: str
    trim: float
    length: float


class Card(BaseModel):
    words: str = Field(min_length=1, max_length=240)
    start: float
    length: float


class Sound(BaseModel):
    id: str
    start: float
    level: float = Field(ge=0, le=2)
    trim: float | None = None
    length: float | None = None


class CutPlan(BaseModel):
    clips: list[Picture]
    cards: list[Card]
    sounds: list[Sound]


def program(name):
    return shutil.which(name) or f"/opt/homebrew/bin/{name}"


def ffmpeg(args):
    return subprocess.run([program("ffmpeg"), "-nostdin", "-v", "error", *args],
                          capture_output=True, check=True, timeout=600).stdout


def probe(path):
    result = subprocess.run([program("ffprobe"), "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(path)], capture_output=True, check=True, timeout=30)
    return json.loads(result.stdout)


def kept_pieces(state, folder):
    pieces = {}

    def add(clips, kind, directory, description):
        for clip in clips:
            if clip["kept"]:
                name = clip["id"]
                pieces[name] = {"kind": kind, "description": description(clip), "path": folder / directory / name}

    for shot in state.get("footage", {}).get("shots", []):
        add(shot["clips"], "clip", "footage", lambda _: shot["title"])
    for entry in state["entries"]:
        if not entry.get("deleted"):
            add(entry.get("takes", []), "voice", "audio", lambda clip: clip["title"])
    for key, kind in (("effects", "effect"), ("music", "music")):
        add(state.get("sound", {}).get(key, []), kind, "audio", lambda clip: clip["title"])
    return pieces


def measure(pieces):
    measured = {}
    for name, piece in pieces.items():
        try:
            info = probe(piece["path"])
            stream_type = "video" if piece["kind"] == "clip" else "audio"
            stream = next(s for s in info["streams"] if s["codec_type"] == stream_type)
            length = stream.get("duration")
            if length and length != "N/A":
                seconds = float(length)
            elif timestamp := stream.get("tags", {}).get("DURATION"):
                hours, minutes, seconds = map(float, timestamp.split(":"))
                seconds += hours * 3600 + minutes * 60
            else:
                seconds = float(info["format"]["duration"])
            if not isfinite(seconds) or seconds < 1 / FPS:
                continue
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            continue  # An unreadable import can't be used in a cut.
        measured[name] = {**piece, "seconds": round(seconds, 6),
                          "has_audio": any(s["codec_type"] == "audio" for s in info["streams"])}
    return measured


def plan_with_claude(kit, words, shape, pieces, current=None):
    canon, outline, _ = kit.sources()
    prompt = """Edit a finished piece from the writer's kept clips and sounds. Return a full cut.
Use only the listed ids and their measured lengths. The clips list is in playback order.
A clip uses length seconds starting at trim seconds of the source. Cards are full-screen
black with white words, and take their own time. Place a card at the opening, between
whole clips, or at the end. Its start is measured from the start of the piece, including
all earlier cards. For example, a two-second opening card followed by a four-second clip
allows the next card to start at six seconds. Leave no gaps or overlaps in the picture.
At least one clip is required. Write short title cards that support the writer's request.
Sound cues start at an absolute time in the piece. A level of 1 means original volume,
0.3 means quieter, and 0 means silent. Clip sound is already included and music will
automatically dip under voice cues. For sound, use trim and length only to select part
of the source, otherwise leave them null. Sounds stop when the picture ends.
The writer's request and any change guide the edit. Use the canon, outline, and
descriptions as source material. For a change, keep what still fits and return the
entire revised cut using pieces still kept. Give times in seconds.

"""
    materials = {
        "request": current["words"] if current else words,
        "canon": canon, "outline": outline, "shape": SHAPE_NAMES[shape],
        "pieces": [{"id": name, **{key: piece[key] for key in ("kind", "description", "seconds")}}
                   for name, piece in pieces.items()],
    }
    if current:
        materials.update(change=words, current_timeline=current["timeline"])
    return ask_claude(prompt + json.dumps(materials, ensure_ascii=False), CutPlan)


def source_length(piece, trim, length, *, clamp=False):
    remaining = piece["seconds"] - trim
    if not isfinite(trim) or trim < 0 or remaining < 1 / FPS - 0.000001:
        raise ValueError("Invalid source trim")
    if length is None:
        return remaining
    if not isfinite(length) or length < 1 / FPS - 0.000001:
        raise ValueError("Invalid source length")
    if not clamp and length > remaining + TOLERANCE:
        raise ValueError("Invalid source length")
    return min(length, remaining)


def frames(seconds):
    frame = seconds * FPS
    if not isfinite(frame) or seconds < 0:
        raise ValueError("Invalid time")
    return round(frame)


def check_plan(result, pieces):
    plan = CutPlan.model_validate(result)
    if not plan.clips:
        raise ValueError("Missing picture")
    cards = iter(sorted(plan.cards, key=lambda card: card.start))
    card = next(cards, None)
    picture = []
    cursor = 0
    planned_start = 0.0

    def add_picture(item, length):
        nonlocal cursor
        count = frames(length)
        if not count:
            raise ValueError("Empty picture item")
        picture.append({**item, "start": cursor / FPS, "length": count / FPS})
        cursor += count

    # Clips arrive in playback order. Place cards at the nearest boundary on
    # the planner's clock so rounding the rendered clips can't shift their order.
    for clip in [*plan.clips, None]:
        while card and (clip is None or card.start <= planned_start + clip.length / 2):
            if not isfinite(card.start) or not card.words.strip():
                raise ValueError("Invalid title card")
            add_picture({"kind": "card", "words": card.words.strip()}, card.length)
            planned_start += card.length
            card = next(cards, None)
        if clip:
            piece = pieces.get(clip.id)
            if not piece or piece["kind"] != "clip":
                raise ValueError("Unknown clip")
            add_picture({"kind": "clip", "id": clip.id, "trim": clip.trim,
                         "description": piece["description"]}, source_length(piece, clip.trim, clip.length))
            planned_start += clip.length
    end = cursor / FPS
    sounds = []
    for sound in plan.sounds:
        piece = pieces.get(sound.id)
        if not piece or piece["kind"] == "clip":
            raise ValueError("Unknown sound")
        start = frames(sound.start) / FPS
        if start >= end:
            continue
        trim = sound.trim if sound.trim is not None else 0
        length = source_length(piece, trim, sound.length, clamp=True)
        sounds.append({"id": sound.id, "kind": piece["kind"], "description": piece["description"],
                       "start": start, "trim": trim, "length": min(length, end - start), "level": sound.level})
    return {"picture": picture, "sounds": sorted(sounds, key=lambda cue: cue["start"]), "seconds": end}


def draw_card(words, size, path):
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    # Wrap by measured width so the same words fit the chosen shape.
    for points in range(round(min(size) * 0.095), 0, -2):
        font = ImageFont.truetype(str(FONT), points)
        lines = []
        for paragraph in words.splitlines():
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}".strip()
                if line and font.getlength(candidate) > width * 0.8:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        text = "\n".join(lines)
        spacing = round(points * 0.25)
        box = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
        text_width, text_height = box[2] - box[0], box[3] - box[1]
        if text_width <= width * 0.8 and text_height <= height * 0.7:
            draw.multiline_text(((width - text_width) / 2 - box[0], (height - text_height) / 2 - box[1]),
                                text, font=font, spacing=spacing, align="center", fill="white")
            image.save(path)
            return
    raise ValueError("Title does not fit")


def render_cut(timeline, pieces, shape, target, *, final=False):
    size = SIZES[shape][int(final)]
    width, height = size
    end = timeline["seconds"]
    args = ["-filter_complex_threads", "2"]
    filters = []
    videos = []
    tracks = []
    voices = [cue for cue in timeline["sounds"] if cue["kind"] == "voice" and cue["level"] > 0]

    def add_audio(index, cue):
        trim, length = cue["trim"], cue["length"]
        delay = round(cue["start"] * 48000)
        output = f"a{index}"
        chain = (f"[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"atrim=start={trim}:duration={length},asetpts=PTS-STARTPTS,volume={cue['level']},"
                 f"adelay={delay}S:all=1")
        if cue["kind"] in ("music", "clip") and voices:
            # Use the placed voice lines as the ducking window, with a short ramp on each edge.
            windows = [f"clip(min((t-{v['start']}+0.1)/0.1,({v['start'] + v['length']}+0.1-t)/0.1),0,1)"
                       for v in voices]
            chain += f",volume='1-0.65*clip({'+'.join(windows)},0,1)':eval=frame"
        filters.append(chain + f"[{output}]")
        tracks.append(f"[{output}]")

    with TemporaryDirectory(dir=target.parent) as temp:
        for index, item in enumerate(timeline["picture"]):
            length = item["length"]
            trim = item.get("trim", 0)
            if item["kind"] == "card":
                path = Path(temp) / f"card-{index}.png"
                draw_card(item["words"], size, path)
                args.extend(["-framerate", str(FPS), "-i", str(path)])
            else:
                piece = pieces[item["id"]]
                args.extend(["-i", str(piece["path"])])
                if piece["has_audio"]:
                    add_audio(index, {**item, "level": 1})
            output = f"v{index}"
            filters.append(
                f"[{index}:v:0]setpts=PTS-STARTPTS,fps={FPS},"
                f"trim=start_frame={frames(trim)},setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
                f"force_divisible_by=2:reset_sar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"format=yuv420p,tpad=stop_mode=clone:stop_duration={length},"
                f"trim=end_frame={frames(length)}[{output}]"
            )
            videos.append(f"[{output}]")

        for index, cue in enumerate(timeline["sounds"], start=len(videos)):
            args.extend(["-i", str(pieces[cue["id"]]["path"])])
            add_audio(index, cue)

        filters.append(f"{''.join(videos)}concat=n={len(videos)}:v=1:a=0[picture]")
        filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={end}[silence]")
        filters.append(f"[silence]{''.join(tracks)}amix=inputs={len(tracks) + 1}:duration=first:"
                       "normalize=0,alimiter=limit=0.95:level=0:latency=1[sound]")
        output = Path(temp) / "cut.mp4"
        ffmpeg(["-abort_on", "empty_output_stream", *args, "-filter_complex", ";".join(filters), "-map", "[picture]", "-map", "[sound]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-threads", "4",
                "-preset", "fast" if final else "ultrafast", "-crf", "18" if final else "24",
                "-c:a", "aac", "-b:a", "192k" if final else "96k", "-movflags", "+faststart", str(output)])
        output.replace(target)


def register_cut(app, library, kit_for):
    active = set()

    def folder(project):
        return library.root / project / "cut"

    def view(project, state):
        cut = state.get("cut", {})
        if cut.get("job") and project not in active:
            cut.update(job=None, notice="")
            library.save(project, state)
            return {**cut, "notice": "The lab stopped before finishing. Try making the cut again."}
        return cut

    def update(project, **changes):
        state = library.load(project)
        cut = state.setdefault("cut", {})
        cut.update(changes)
        library.save(project, state)
        return cut

    def require_idle(project, cut):
        if cut.get("job") and project in active:
            raise HTTPException(409, "The lab is still working on this cut.")

    def next_final(directory):
        number = 1
        while (directory / f"Final {number}.mp4").exists():
            number += 1
        return f"Final {number}.mp4"

    async def finish(project, kit, pieces, current, words=None, shape=None):
        final = words is None
        notice = RENDER_ERROR if final else PLAN_ERROR
        target = None
        try:
            measured = await asyncio.to_thread(measure, pieces)
            if final:
                timeline, shape = current["timeline"], current["shape"]
            else:
                if not any(piece["kind"] == "clip" for piece in measured.values()):
                    raise ValueError("No readable clips")
                result = await asyncio.to_thread(plan_with_claude, kit, words, shape, measured, current)
                timeline = check_plan(result, measured)
            used = {item["id"] for item in [*timeline["picture"], *timeline["sounds"]] if "id" in item}

            def still_kept():
                available = kept_pieces(library.load(project), folder(project).parent)
                if not used <= available.keys() or not used <= measured.keys():
                    raise ValueError("A piece is no longer available")

            still_kept()
            directory = folder(project)
            directory.mkdir(exist_ok=True)
            name = next_final(directory) if final else f"Preview {uuid4().hex}.mp4"
            target = directory / name
            if not final:
                update(project, job="preview")
                notice = RENDER_ERROR
            await asyncio.to_thread(render_cut, timeline, measured, shape, target, final=final)
            still_kept()
            if final:
                update(project, current={**current, "final": name}, job=None, notice="")
            else:
                previous = library.load(project).get("cut", {}).get("current")
                update(project, current={"words": current["words"] if current else words, "shape": shape,
                                         "timeline": timeline, "preview": name},
                       previous=previous, job=None, notice="")
        except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, AttributeError):
            if target:
                target.unlink(missing_ok=True)
            if notice == PLAN_ERROR and not library.load(project).get("cut", {}).get("current"):
                notice = FIRST_PLAN_ERROR
            update(project, job=None, notice=notice)
        finally:
            active.discard(project)

    def start(project, background, *, words=None, shape=None, revise=False):
        kit = kit_for(project)
        state = library.load(project)
        cut = state.get("cut", {})
        require_idle(project, cut)
        current = cut.get("current")
        if (revise or words is None) and not current:
            raise HTTPException(422, "Make a cut first.")
        if words is not None:
            words = words.strip()
            if not words:
                raise HTTPException(422, "Write what should change." if revise else "Write what you’re making.")
        pieces = kept_pieces(state, folder(project).parent)
        if not any(piece["kind"] == "clip" for piece in pieces.values()):
            raise HTTPException(422, KEEP_FIRST)
        job = "final" if words is None else "cutting"
        saved = update(project, job=job, notice="")
        active.add(project)
        background.add_task(finish, project, kit, pieces, current if revise or words is None else None,
                            words, current["shape"] if revise else shape)
        return saved

    @app.get(CUT)
    async def get_cut(project: str):
        kit_for(project)
        return view(project, library.load(project))

    @app.post(CUT, status_code=202)
    async def make_cut(project: str, fields: CutRequest, background: BackgroundTasks):
        return start(project, background, words=fields.words, shape=fields.shape)

    @app.post(CUT + "/revise", status_code=202)
    async def revise_cut(project: str, fields: ChangeRequest, background: BackgroundTasks):
        return start(project, background, words=fields.words, revise=True)

    @app.post(CUT + "/restore")
    async def restore_cut(project: str):
        kit_for(project)
        cut = library.load(project).get("cut", {})
        require_idle(project, cut)
        if not cut.get("previous"):
            raise HTTPException(422, "There’s no earlier cut to put back.")
        return update(project, current=cut["previous"], previous=cut["current"], job=None, notice="")

    def output_file(project, name):
        kit_for(project)
        directory = folder(project)
        path = directory / name
        if (Path(name).suffix != ".mp4" or path.resolve().parent != directory.resolve() or not path.is_file()
                or not path.resolve().is_relative_to(library.root.resolve())):
            raise HTTPException(404, "The lab couldn’t find that file.")
        return path

    @app.get(CUT + "/files/{name}")
    async def cut_file(project: str, name: str):
        return FileResponse(output_file(project, name), media_type="video/mp4")

    @app.post(CUT + "/final", status_code=202)
    async def make_final(project: str, background: BackgroundTasks):
        return start(project, background)

    @app.post(CUT + "/finder", status_code=204)
    async def finder(project: str):
        kit_for(project)
        current = library.load(project).get("cut", {}).get("current") or {}
        if not current.get("final"):
            raise HTTPException(404, "Make the final file first.")
        path = output_file(project, current["final"])
        try:
            subprocess.Popen(["open", "-R", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise HTTPException(500, "The lab couldn’t open Finder.") from None

    return view

import asyncio
import base64
import io
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageOps


@dataclass(frozen=True)
class Model:
    text: str
    edit: str
    usd: Decimal


# Estimated cost per 1K picture, with web search off and standard thinking.
MODEL = Model("google/nano-banana-2.1", "google/nano-banana-2.1/edit", Decimal("0.05"))


# Kling 2.6 Pro, with native audio off. Both endpoints bill per output second.
# https://fal.ai/models/fal-ai/kling-video/v2.6/pro/image-to-video
VIDEO_TEXT = "fal-ai/kling-video/v2.6/pro/text-to-video"
VIDEO_IMAGE = "fal-ai/kling-video/v2.6/pro/image-to-video"
VIDEO_USD_PER_SECOND = Decimal("0.07")


class FalError(Exception):
    pass


class VideoFailed(FalError):
    pass


def reference_url(path):
    with Image.open(path) as image:
        if path.suffix.lower() == ".png" and max(image.size) <= 1536:
            data = path.read_bytes()
            mime = "image/png"
        else:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1536, 1536))
            with io.BytesIO() as stream:
                image.save(stream, "JPEG", quality=94)
                data = stream.getvalue()
            mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def billable_units(result):
    units = result.headers.get("X-Fal-Billable-Units")
    try:
        amount = Decimal(units) if units is not None else None
        if amount is not None and amount.is_finite() and amount >= 0:
            return amount
    except InvalidOperation:
        pass
    return None


class Fal:
    def __init__(self, transport=None):
        self.transport = transport

    def headers(self, missing="The picture service needs a key before it can make pictures."):
        key = os.getenv("FAL_KEY") or dotenv_values(
            Path.home() / "Projects/OpenMontage/.env", interpolate=False
        ).get("FAL_KEY")
        if not key:
            raise FalError(missing)
        return {"Authorization": f"Key {key}"}

    async def make(self, description, kind, count, reference, charge, save_picture, *, scene=""):
        headers = self.headers()
        model = MODEL.text
        payload = {"prompt": description, "num_images": count, "resolution": "1K",
                   "aspect_ratio": "3:4" if kind == "person" else "4:3",
                   "output_format": "png", "thinking_level": "medium", "enable_web_search": False}
        if reference:
            model = MODEL.edit
            subject = ("person, preserving the face and identity" if kind == "person"
                       else "place, preserving the layout and distinctive features")
            payload["prompt"] = f"Show the same {subject} from the reference picture. {description}"
            if not scene:
                variation = "camera angle, framing, and expression" if kind == "person" else "camera angle and framing"
                payload["prompt"] += f" Vary the {variation} across the batch."
            payload["image_urls"] = [await asyncio.to_thread(reference_url, reference)]
        if scene:
            payload["prompt"] += f" Follow this direction for the picture: {scene}"

        async with httpx.AsyncClient(transport=self.transport, timeout=45, follow_redirects=True) as client:
            submit = await client.post(f"https://queue.fal.run/{model}", json=payload, headers=headers)
            job = submit.raise_for_status().json()
            # An accepted job can still be billed if the connection drops before its result.
            charge(MODEL.usd * count, estimated=True)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                response = await client.get(job["status_url"], headers=headers)
                status = response.raise_for_status().json()
                if status["status"] == "COMPLETED":
                    break
                await asyncio.sleep(1.5)
            else:
                raise FalError("The picture service took too long to answer. The total includes an estimate for the request.")

            result = await client.get(job["response_url"], headers=headers)
            if status.get("error"):
                charge(Decimal(0), estimated=False)
                raise FalError("The picture service couldn’t make this batch. Try changing the description.")
            images = result.raise_for_status().json()["images"]
            billed = billable_units(result)
            # This model bills at one dollar per unit.
            amount = MODEL.usd * len(images) if billed is None else billed
            charge(amount, estimated=billed is None)

            missing = False
            for image in images:
                try:
                    picture = await client.get(image["url"])
                    save_picture(picture.raise_for_status().content)
                except (OSError, httpx.HTTPError):
                    missing = True
            if missing or len(images) < count:
                raise FalError("Some pictures didn’t come through. Any pictures that arrived are below.")

    async def submit_video(self, description, seconds, reference):
        headers = self.headers("The footage service needs a key before it can make a clip.")
        model = VIDEO_IMAGE if reference else VIDEO_TEXT
        payload = {"prompt": description, "duration": str(seconds), "generate_audio": False}
        if reference:
            payload["start_image_url"] = await asyncio.to_thread(reference_url, reference)
        async with httpx.AsyncClient(transport=self.transport, timeout=45, follow_redirects=True) as client:
            response = await client.post(f"https://queue.fal.run/{model}", headers=headers, json=payload)
            job = response.raise_for_status().json()
            return {key: job[key] for key in ("status_url", "response_url")}

    async def video_result(self, job):
        headers = self.headers("The footage service needs a key before it can check this clip.")
        async with httpx.AsyncClient(transport=self.transport, timeout=45, follow_redirects=True) as client:
            response = await client.get(job["status_url"], headers=headers)
            status = response.raise_for_status().json()
            if status.get("error"):
                raise VideoFailed("The footage service couldn’t make this clip. Try changing the description.")
            if status["status"] != "COMPLETED":
                return None
            result = await client.get(job["response_url"], headers=headers)
            if result.status_code == 422:
                raise VideoFailed("The footage service couldn’t make this clip. Try changing the description.")
            url = result.raise_for_status().json()["video"]["url"]
            units = billable_units(result)
            clip = await client.get(url)
            return clip.raise_for_status().content, units

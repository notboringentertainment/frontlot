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


class FalError(Exception):
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


class Fal:
    def __init__(self, transport=None):
        self.transport = transport

    def headers(self):
        key = os.getenv("FAL_KEY") or dotenv_values(
            Path.home() / "Projects/OpenMontage/.env", interpolate=False
        ).get("FAL_KEY")
        if not key:
            raise FalError("The picture service needs a key before it can make pictures.")
        return {"Authorization": f"Key {key}"}

    async def make(self, description, kind, count, reference, charge, save_picture):
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
            payload["image_urls"] = [await asyncio.to_thread(reference_url, reference)]

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
            units = result.headers.get("X-Fal-Billable-Units")
            try:
                billed = Decimal(units) if units is not None else None
                if billed is not None and (not billed.is_finite() or billed < 0):
                    billed = None
            except InvalidOperation:
                billed = None
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

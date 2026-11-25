import asyncio
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

VOICE_DESIGN = "eleven_multilingual_ttv_v2"
SPEECH = "eleven_flash_v2_5"
EFFECTS = "eleven_text_to_sound_v3"
MUSIC = "music_v2_5"
MP3 = {"output_format": "mp3_44100_128"}
MISSING_KEY = "ElevenLabs needs a key before it can make audio."


class ElevenLabsError(Exception):
    pass


class ElevenLabs:
    def __init__(self, transport=None):
        self.transport = transport
        self.generating = asyncio.Lock()

    def headers(self):
        key = os.getenv("ELEVENLABS_API_KEY") or dotenv_values(
            Path.home() / "Projects/OpenMontage/.env", interpolate=False
        ).get("ELEVENLABS_API_KEY")
        if not key:
            raise ElevenLabsError(MISSING_KEY)
        return {"xi-api-key": key}

    async def request(self, method, path, **kwargs):
        try:
            async with httpx.AsyncClient(base_url="https://api.elevenlabs.io",
                                         transport=self.transport, timeout=150) as client:
                response = await client.request(method, path, headers=self.headers(), **kwargs)
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in (401, 403):
                raise ElevenLabsError("ElevenLabs didn’t allow this request. Check the key and account access.") from None
            if status == 429:
                raise ElevenLabsError("ElevenLabs is busy. Try again in a moment.") from None
            raise ElevenLabsError(f"ElevenLabs couldn’t finish this request ({status}).") from None
        except httpx.HTTPError:
            raise ElevenLabsError("The lab couldn’t reach ElevenLabs. Check your connection and try again.") from None
        return response

    async def balance(self):
        try:
            response = await self.request("GET", "/v1/user/subscription", timeout=5)
            info = response.json()
            return info["character_count"], info["character_limit"]
        except (ElevenLabsError, ValueError, KeyError, TypeError):
            return None, None

    async def generate(self, path, payload, charge, *, product):
        async with self.generating:
            start = time.time_ns() // 1_000_000
            result = await self.request("POST", path, params=MP3, json=payload)
            end = time.time_ns() // 1_000_000
            cost = result.headers.get("character-cost", "")
            if cost.isdecimal():
                charge(int(cost), None)
            else:
                charge(None, {"product": product, "start": start, "end": end})
        return result

    async def usage(self, product, start, end):
        # Analytics can arrive later than the audio. Count only a single
        # matching generation, since the workspace can make sound elsewhere.
        try:
            response = await self.request("POST", "/v1/workspace/analytics/query/usage-by-product-over-time",
                                          json={"start_time": start, "end_time": end,
                                                "group_by": ["product_type"]}, timeout=5)
            data = response.json()
            rows = [dict(zip(data["columns"], row)) for row in data["rows"]]
            matches = [row for row in rows if row["product_type"] == product and row["usage_count"]]
            if len(matches) == 1 and matches[0]["usage_count"] == 1:
                return matches[0]["total_usage"]
        except (ElevenLabsError, ValueError, KeyError, TypeError):
            pass
        return None

    async def voices(self):
        params = {"page_size": 100, "include_total_count": "false"}
        voices = []
        while True:
            response = (await self.request("GET", "/v2/voices", params=params)).json()
            voices.extend(response["voices"])
            if not response.get("has_more") or not response.get("next_page_token"):
                return voices
            params["next_page_token"] = response["next_page_token"]

    async def voice(self, voice_id):
        return (await self.request("GET", "/v1/voices/" + quote(voice_id, safe=""))).json()

    async def sample(self, url):
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError:
            raise ElevenLabsError("ElevenLabs couldn’t open this voice sample.") from None

    async def speech(self, voice_id, text, charge):
        return await self.generate("/v1/text-to-speech/" + quote(voice_id, safe=""),
                                   {"text": text, "model_id": SPEECH}, charge, product="TTS")

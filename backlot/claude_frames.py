"""Frames between Front Lot's server and a Claude session broker.

Same 5-byte header as the signing terminal (backlot/tty.py) but its own types
and limits: conversation events and snapshots are far larger than the signing
program's 4 KB JSON cap, and widening tty.py would loosen the signing socket's
parser. Nothing here is shared with the signing path.
"""
from __future__ import annotations

import asyncio, json, struct

HEADER = struct.Struct("!BI")
HELLO, IN, OUT, RESIZE, STATUS, BYE, EVENT, ACTION = 1, 2, 3, 4, 5, 6, 7, 8
JSON_TYPES = frozenset({HELLO, RESIZE, STATUS, BYE, EVENT, ACTION})
LIMITS = {IN: 4096, OUT: 65536, **{t: 256 * 1024 for t in JSON_TYPES}}


class FrameError(ValueError):
    pass


def encode(frame_type: int, payload: bytes = b"") -> bytes:
    if frame_type not in LIMITS:
        raise FrameError("unknown frame type")
    payload = bytes(payload)
    if len(payload) > LIMITS[frame_type]:
        raise FrameError("oversized frame")
    return HEADER.pack(frame_type, len(payload)) + payload


def encode_json(frame_type: int, obj: dict) -> bytes:
    if frame_type not in JSON_TYPES:
        raise FrameError("frame type is not JSON")
    return encode(frame_type, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def decode_json(frame_type: int, payload: bytes) -> dict:
    if frame_type not in JSON_TYPES:
        raise FrameError("frame type is not JSON")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FrameError("malformed JSON frame") from exc
    if not isinstance(value, dict):
        raise FrameError("JSON frame must carry an object")
    return value


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Raises asyncio.IncompleteReadError on EOF, FrameError on a bad frame."""
    frame_type, length = HEADER.unpack(await reader.readexactly(HEADER.size))
    if frame_type not in LIMITS:
        raise FrameError("unknown frame type")
    if length > LIMITS[frame_type]:
        raise FrameError("oversized frame")
    return frame_type, await reader.readexactly(length)

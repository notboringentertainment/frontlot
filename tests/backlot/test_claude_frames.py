import asyncio

import pytest

from backlot import claude_frames as cf, tty


def test_event_frames_carry_large_json():
    payload = {"seq": 1, "event": {"kind": "row", "text": "x" * 100_000}}
    raw = cf.encode_json(cf.EVENT, payload)
    t, body = raw[0], raw[5:]
    assert t == cf.EVENT and cf.decode_json(cf.EVENT, body) == payload


def test_limits_and_unknown_types():
    with pytest.raises(cf.FrameError):
        cf.encode_json(cf.EVENT, {"x": "y" * (300 * 1024)})
    with pytest.raises(cf.FrameError):
        cf.encode(9, b"")
    with pytest.raises(cf.FrameError):
        cf.encode(cf.IN, b"x" * 5000)


def test_read_frame_round_trip():
    async def go():
        r = asyncio.StreamReader(); r.feed_data(cf.encode_json(cf.ACTION, {"type": "end"})); r.feed_eof()
        return await cf.read_frame(r)
    t, body = asyncio.run(go())
    assert t == cf.ACTION and cf.decode_json(t, body) == {"type": "end"}


def test_signing_framing_is_untouched():
    assert tty.MAX_JSON == 4096 and 7 not in tty._KNOWN_TYPES and 8 not in tty._KNOWN_TYPES

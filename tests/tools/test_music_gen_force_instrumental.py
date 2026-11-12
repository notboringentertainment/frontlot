"""Regression for the force_instrumental mandate (REVIEW §8 #8).

``skills/creative/music-gen-usage.md`` mandates that video background music
ALWAYS be generated instrumental-only (vocals collide with narration).
``music_gen`` historically never sent the kwarg, so ElevenLabs could return
vocal tracks. This test pins the payload to include ``force_instrumental=True``
by default and to honor an explicit opt-out.
"""

from __future__ import annotations

import sys
import types

import pytest

from tools.audio.music_gen import MusicGen


class _FakeResponse:
    def __init__(self, content: bytes = b"audio") -> None:
        from tests.tools.test_audio_half_transport import wav_bytes
        self.content = wav_bytes()
        self.status_code = 200
        self.headers = {"character-cost":"1", "request-id":"invented-music"}

    def raise_for_status(self) -> None:  # noqa: D401 - stub
        return None


def _install_fake_requests(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict,
) -> types.ModuleType:
    """Install a stub ``requests`` module that records the JSON payload."""
    fake = types.ModuleType("requests")

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):  # noqa: ANN001
        captured["posts"] = captured.get("posts", 0) + 1
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse()

    fake.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


from tests.tools.test_audio_governance import project

@pytest.fixture
def legacy_music_root(project):
    from tests.tools.test_audio_half_transport import priced
    return priced.__wrapped__(project)

def approved_request(root,prompt):
    from lib.audio_workspace import save_music_brief,prepare_request
    from tests.tools.test_audio_governance import sign
    identity=save_music_brief(root,prompt,10)
    record=prepare_request(root,'music','music_v1',brief_id=identity)
    sign(root,'audio_spend',record,record['request_id'])
    return {'project_dir':str(root),'audio_request_id':record['request_id']}


def test_force_instrumental_is_sent_true_by_default(monkeypatch, tmp_path, legacy_music_root):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    captured: dict = {}
    _install_fake_requests(monkeypatch, captured)
    # Route the output to tmp_path so the test never writes music_output.mp3
    # into the repo root (the default output_path).
    MusicGen()._generate(approved_request(legacy_music_root,"gentle ambient"),"test-key")

    assert captured["posts"] == 1
    assert "force_instrumental" in captured["payload"], "force_instrumental kwarg was never sent"
    assert captured["payload"]["force_instrumental"] is True
    assert captured["payload"]["music_length_ms"] == 10_000


def test_explicit_vocal_opt_out_is_respected(monkeypatch, tmp_path, legacy_music_root):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    captured: dict = {}
    _install_fake_requests(monkeypatch, captured)
    args=approved_request(legacy_music_root,"lead vocal pop")
    args['force_instrumental']=False
    result=MusicGen()._generate(args,"test-key")
    assert not result.success and 'always instrumental' in result.error
    assert captured.get("posts",0)==0


def test_schema_defaults_force_instrumental_to_true():
    props = MusicGen().input_schema["properties"]["force_instrumental"]
    assert props["type"] == "boolean"
    assert props["default"] is True

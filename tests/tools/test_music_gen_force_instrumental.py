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
        self.content = content

    def raise_for_status(self) -> None:  # noqa: D401 - stub
        return None


def _install_fake_requests(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict,
) -> types.ModuleType:
    """Install a stub ``requests`` module that records the JSON payload."""
    fake = types.ModuleType("requests")

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: ANN001
        captured["posts"] = captured.get("posts", 0) + 1
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse()

    fake.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


@pytest.fixture
def legacy_music_root(tmp_path):
    import hashlib, yaml
    from tests.tools.test_audio_governance import sign
    root = tmp_path / "older-music"
    root.mkdir()
    data = {"version": "1.0", "budget_usd_cap": 10, "wall_time_minutes": 10,
            "cast_cap": {"characters": 1, "locations": 1},
            "default_video_endpoint": "invented-video",
            "provider_egress": {"provider": "fal", "content_classes": ["prompts"]}}
    raw = yaml.safe_dump(data).encode()
    (root / "project.yaml").write_bytes(raw)
    sign(root, "config", {"config_sha256": hashlib.sha256(raw).hexdigest()}, "project_config")
    return root


def test_force_instrumental_is_sent_true_by_default(monkeypatch, tmp_path, legacy_music_root):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    captured: dict = {}
    _install_fake_requests(monkeypatch, captured)
    # Route the output to tmp_path so the test never writes music_output.mp3
    # into the repo root (the default output_path).
    out = legacy_music_root / "bg.mp3"
    MusicGen()._generate(
        {
            "prompt": "gentle ambient",
            "duration_seconds": 10,
            "output_path": str(out),
        },
        "test-key",
    )

    assert captured["posts"] == 1
    assert "force_instrumental" in captured["payload"], "force_instrumental kwarg was never sent"
    assert captured["payload"]["force_instrumental"] is True
    assert captured["payload"]["music_length_ms"] == 10_000


def test_explicit_vocal_opt_out_is_respected(monkeypatch, tmp_path, legacy_music_root):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    captured: dict = {}
    _install_fake_requests(monkeypatch, captured)
    out = legacy_music_root / "vocals.mp3"
    MusicGen()._generate(
        {
            "prompt": "lead vocal pop",
            "duration_seconds": 10,
            "force_instrumental": False,
            "output_path": str(out),
        },
        "test-key",
    )

    assert captured["posts"] == 1
    assert captured["payload"]["force_instrumental"] is False


def test_schema_defaults_force_instrumental_to_true():
    props = MusicGen().input_schema["properties"]["force_instrumental"]
    assert props["type"] == "boolean"
    assert props["default"] is True

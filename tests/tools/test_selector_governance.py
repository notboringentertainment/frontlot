"""Generic selectors run the governance boundary BEFORE any upload or delegation
(Codex inspection #9) and delegate governed calls only to governance-bound
providers. Registry flag coverage for the five bound tools is here too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tools.base_tool import ToolResult, ToolStatus
from tools.graphics.image_selector import ImageSelector
from tools.video import _shared
from tools.video.video_selector import VideoSelector

from tests.tools._authored_film_helpers import tiny_png_bytes
from tests.tools.test_look_governance import CHAR_REF, env, rendering_inputs, verifiers  # noqa: F401 — fixtures
from tests.tools.test_video_selector_routing import _ScoreStub


class _Provider:
    def __init__(self, name: str, provider: str, *, bound: bool, capability: str, props: tuple[str, ...] = ()):
        self.name, self.provider, self.governance_bound, self.capability = name, provider, bound, capability
        self.best_for = [name]
        self.quality_score = None
        self.supports = {"text_to_video": True, "image_to_video": True, "reference_to_video": True}
        self.input_schema = {"properties": {"prompt": {}, **{p: {} for p in props}}}
        self.calls: list[dict[str, Any]] = []

    def get_status(self):
        return ToolStatus.AVAILABLE

    def is_operation_available(self, operation):
        return True

    def get_info(self):
        return {"name": self.name, "provider": self.provider, "agent_skills": [], "best_for": self.best_for,
                "supports": self.supports, "quality_score": None}

    def estimate_cost(self, inputs):
        return 0.1

    def estimate_runtime(self, inputs):
        return 1.0

    def execute(self, inputs):
        self.calls.append(dict(inputs))
        return ToolResult(success=True, data={"output_path": "x"})


def _rank_first(monkeypatch, *tools: _Provider):
    """Deterministic ranking: ungoverned providers always outrank governance-bound ones."""

    def fake_rank(candidates, task_context):
        ordered = sorted(candidates, key=lambda t: (bool(getattr(t, "governance_bound", False)), t.name))
        return [_ScoreStub(t.name, t.provider, 1.0 - i / 10) for i, t in enumerate(ordered)]

    monkeypatch.setattr("lib.scoring.rank_providers", fake_rank)


@pytest.fixture
def video(monkeypatch):
    loose = _Provider("loose_video", "loose", bound=False, capability="video_generation", props=("image_url",))
    bound = _Provider("bound_video", "bound", bound=True, capability="video_generation", props=("reference_image_path",))
    _rank_first(monkeypatch, loose, bound)  # the ungoverned provider always ranks higher
    monkeypatch.setattr(VideoSelector, "_providers", lambda self: [loose, bound])
    return loose, bound


@pytest.fixture
def image(monkeypatch):
    loose = _Provider("loose_image", "loose", bound=False, capability="image_generation")
    bound = _Provider("bound_image", "bound", bound=True, capability="image_generation")
    _rank_first(monkeypatch, loose, bound)
    monkeypatch.setattr(ImageSelector, "_providers", lambda self: [loose, bound])
    return loose, bound


def test_legacy_call_without_project_is_unchanged(video, image):
    loose_v, bound_v = video
    assert VideoSelector().execute({"prompt": "fog"}).success and loose_v.calls and not bound_v.calls
    loose_i, bound_i = image
    assert ImageSelector().execute({"prompt": "fog"}).success and loose_i.calls and not bound_i.calls


@pytest.mark.parametrize('selector_cls, fixture, provider', [
    (VideoSelector, 'video', 'kling_reference_video'),
    (ImageSelector, 'image', 'seedream_image'),
])
@pytest.mark.parametrize('allowance', [.05, .20])
def test_supervised_selector_checks_chosen_provider_estimate(
        env, verifiers, request, selector_cls, fixture, provider, allowance):
    from lib.supervised_production import prepare
    loose, bound = request.getfixturevalue(fixture)
    bound.name = provider
    # The unbound provider ranks higher and is cheaper: its estimate must not
    # authorize the governed provider, which costs 10 cents.
    loose.estimate_cost = lambda inputs: 0.0
    (env / 'canon.md').write_text('Existing story.')
    prepare(env, {'shot_id': 'supervised', 'direction': 'Turn toward the camera.',
        'source_paths': ['canon.md'], 'spend_allowance_usd': allowance,
        'max_video_takes': 1, 'allowed_tools': [provider],
        'look_refs': [CHAR_REF], 'reference_manifest': []}, user_note='Try this shot.')
    result = selector_cls().execute({'project_dir': str(env), 'shot_id': 'supervised',
        'asset_class': 'supervised_shot', 'prompt': 'Turn toward the camera.',
        'look_refs': [CHAR_REF], 'reference_manifest': []})
    assert not loose.calls
    if allowance < .1:
        assert not result.success and 'allowance' in result.error
        assert not bound.calls
    else:
        assert result.success, result.error
        assert len(bound.calls) == 1


def _governed_inputs(verifiers, selector_cls, **extra):
    """Video calls carry look_refs; image calls are builder renderings (round 2 #6)."""
    if selector_cls is VideoSelector:
        return {"prompt": "fog", "look_refs": [CHAR_REF], **extra}
    return {**rendering_inputs(verifiers, "hero"), **extra}


@pytest.mark.parametrize("selector_cls, fixture", [(VideoSelector, "video"), (ImageSelector, "image")])
def test_governed_call_verified_before_delegation_and_bound_only(env, verifiers, request, selector_cls, fixture):
    loose, bound = request.getfixturevalue(fixture)
    inputs = _governed_inputs(verifiers, selector_cls, project_dir=str(env), stage="assets")
    ref = inputs["look_refs"][0]
    r = selector_cls().execute(inputs)
    assert r.success, r.error
    assert verifiers.look_calls == [("proj-quill", "character", "quill-marrow", ref["look_hash"])]
    assert not loose.calls, "an ungoverned provider was delegated a governed call"
    assert bound.calls and bound.calls[0]["look_refs"] == [ref]
    assert r.data["selected_tool"] == bound.name


def test_output_path_inside_pinned_project_is_governed(env, verifiers, image, monkeypatch):
    """Round 2 #9: the project is inferred from ANY path-like input before the legacy
    return; a call carrying only output_path under a 1.2-pinned project is governed by the
    signed pin, so an unbound provider is never delegated."""
    import sys

    loose, bound = image
    sys.modules["lib.look_ingest"].project_look_governed = lambda root, **k: True
    out = str(env / "renders" / "fog.png")
    r = ImageSelector().execute({"prompt": "fog", "output_path": out})
    assert not r.success and "refused before delegation" in r.error and "look_refs" in r.error
    assert not loose.calls and not bound.calls
    r = ImageSelector().execute({**rendering_inputs(verifiers, "hero"), "output_path": out})
    assert r.success, r.error
    assert not loose.calls and bound.calls and r.data["selected_tool"] == bound.name
    assert bound.calls[0]["project_dir"] == str(env)  # delegated call bound to the governing project
    # A registered legacy (unpinned) project reached only through output_path stays legacy.
    sys.modules["lib.look_ingest"].project_look_governed = lambda root, **k: False
    bound.calls.clear()
    r = ImageSelector().execute({"prompt": "fog", "output_path": out})
    assert r.success and loose.calls and not bound.calls


@pytest.mark.parametrize("selector_cls, fixture", [(VideoSelector, "video"), (ImageSelector, "image")])
def test_governance_refusal_stops_delegation(env, verifiers, request, selector_cls, fixture):
    loose, bound = request.getfixturevalue(fixture)
    verifiers.refuse_look = True
    with patch.object(_shared, "upload_image_fal") as upload:
        r = selector_cls().execute({"prompt": "fog", "project_dir": str(env), "look_refs": [CHAR_REF]})
    assert not r.success and "refused before delegation" in r.error and "no active look_lock" in r.error
    assert not loose.calls and not bound.calls
    upload.assert_not_called()
    # Governed keys without a resolvable project are refused too (no ungoverned escape).
    verifiers.refuse_look = False
    r = selector_cls().execute({"prompt": "fog", "look_refs": [CHAR_REF]})
    assert not r.success and "refused before delegation" in r.error and not loose.calls and not bound.calls


def test_governed_project_by_signed_pin_needs_no_keys(env, verifiers, image, monkeypatch):
    import sys

    loose, bound = image
    sys.modules["lib.look_ingest"].project_look_governed = lambda root, **k: True
    # No look_refs on a governed project: refused at the boundary, nothing delegated.
    r = ImageSelector().execute({"prompt": "fog", "project_dir": str(env)})
    assert not r.success and "look_refs" in r.error and not loose.calls and not bound.calls


def test_video_selector_never_uploads_on_governed_call_and_checks_lineage(env, verifiers, video):
    loose, bound = video
    ref = env / "canon" / "visual" / "objects" / "frame.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(tiny_png_bytes())
    inputs = {"prompt": "fog", "project_dir": str(env), "look_refs": [CHAR_REF], "operation": "image_to_video",
              "reference_image_path": str(ref)}
    with patch.object(_shared, "upload_image_fal") as upload:
        r = VideoSelector().execute(inputs)
    assert r.success, r.error
    upload.assert_not_called()
    assert verifiers.lineage_calls, "reference lineage was not verified before delegation"
    assert bound.calls and bound.calls[0]["reference_image_path"] == str(ref) and not loose.calls
    # Tainted (casting-inspiration) reference: refused before any delegation.
    from lib.pathsafe import sha256_file

    bound.calls.clear()
    verifiers.tainted.add(sha256_file(ref))
    with patch.object(_shared, "upload_image_fal") as upload:
        r = VideoSelector().execute(inputs)
    assert not r.success and "refused before delegation" in r.error and not bound.calls
    upload.assert_not_called()
    # Remote reference URLs are refused on governed calls.
    r = VideoSelector().execute({**inputs, "reference_image_path": None, "reference_image_urls": ["https://x/a.png"]})
    assert not r.success and "reference_image_urls refused" in r.error and not bound.calls


def test_governed_call_with_only_ungoverned_providers_is_refused(env, verifiers, monkeypatch):
    loose = _Provider("loose_image", "loose", bound=False, capability="image_generation")
    _rank_first(monkeypatch, loose)
    monkeypatch.setattr(ImageSelector, "_providers", lambda self: [loose])
    r = ImageSelector().execute({**rendering_inputs(verifiers, "hero"), "project_dir": str(env)})
    assert not r.success and "governance-bound" in r.error and not loose.calls


def test_registry_flags_governance_bound_providers():
    from tools.tool_registry import registry

    registry.ensure_discovered()
    bound = {"seedream_image", "kling_reference_video", "seedance_video", "title_card", "poster_composite"}
    for name in bound:
        assert registry.get(name).governance_bound is True, name
    for name in ("flux_image", "image_selector", "video_selector"):
        tool = registry.get(name)
        assert tool is not None and tool.governance_bound is False, name


def test_manifest_governed_stages_list_only_bound_providers():
    import yaml

    manifest = yaml.safe_load(Path("pipeline_defs/authored-film@1.2.yaml").read_text())
    stages = {s["name"]: s for s in manifest["stages"]}
    bound = {"seedream_image", "kling_reference_video", "seedance_video", "title_card", "poster_composite"}
    for name in ("look_lock", "headshots", "visual_bible"):
        tools = set(stages[name].get("tools_available") or []) | set(stages[name].get("optional_tools") or [])
        assert tools <= bound, (name, tools - bound)
    assets = set(stages["assets"]["tools_available"]) | set(stages["assets"].get("optional_tools") or [])
    assert not assets & {"image_selector", "video_selector", "flux_image"}
    visual = {t for t in assets if t in {"seedream_image", "kling_reference_video", "seedance_video", "title_card",
                                          "poster_composite", "flux_image", "image_selector", "video_selector"}}
    assert visual <= bound

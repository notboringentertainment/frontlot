"""Kling o3 pro reference-to-video (mocked FAL): payload vs fixture, caps, trust boundary, reservation states."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import lib.receipts as receipts_mod
from lib import gates
from lib.receipts import find_generation, generation_receipts_path
from lib.pathsafe import sha256_file
from lib.state_io import read_jsonl
from tools.cost_tracker import IndeterminatePaidCallError, load_reservations, resume_check
from tools.video import _shared
from tools.video.kling_reference_video import KlingReferenceVideo

from tests.tools._authored_film_helpers import (
    approve_storyboard_batch,
    make_verified_project,
    project_tracker,
    tiny_png_bytes,
    write_receipted_png,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "fal-ai-kling-video-o3-pro-reference-to-video.json").read_text()
)
PROBED = {"file_size_bytes": 8, "file_size_mb": 0.0, "duration_seconds": 5.0, "video_width": 1920,
          "video_height": 1080, "video_codec": "h264", "has_audio": False, "audio_codec": None}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch)
    (project / "assets" / "video").mkdir(parents=True)
    return project


def _inputs(project, **extra):
    base = {
        "prompt": "@Image1 turns toward the camera",
        "reference_image_urls": ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png"],
        "duration": "5",
        "aspect_ratio": "16:9",
        "output_path": str(project / "assets" / "video" / "shot.mp4"),
        "project_dir": str(project),
    }
    base.update(extra)
    return base


def _fake_download(url, dest, **kw):
    Path(dest).write_bytes(b"fake-mp4")
    return {"bytes": 8, "content_type": "video/mp4"}


def _happy_path(**wait_result):
    wait_result = wait_result or {"video": {"url": "https://v3.fal.media/o.mp4"}}
    return (
        patch.object(_shared, "fal_queue_wait", return_value=wait_result),
        patch.object(_shared, "fal_download", side_effect=_fake_download),
        patch.object(_shared, "verify_video_file", return_value=dict(PROBED)),
    )


def _png(color):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="PNG")
    return buf.getvalue()


# ---- registration + payload ----

def test_registry_discovers_kling_reference_video(monkeypatch):
    from tools.tool_registry import ToolRegistry

    monkeypatch.delenv("FAL_KEY", raising=False)
    reg = ToolRegistry()
    monkeypatch.setattr("tools.tool_registry.registry", reg)
    reg.discover("tools")
    tool = reg.get("kling_reference_video")
    assert tool is not None and isinstance(tool, KlingReferenceVideo)
    assert tool.capability == "video_generation"
    assert tool.emits_generation_receipt is True
    assert KlingReferenceVideo.retry_policy.max_retries == 0
    assert (Path(__file__).resolve().parents[2] / ".agents" / "skills" / "kling-o3-reference" / "SKILL.md").exists()


def test_payload_matches_fixture_shape(env):
    captured = {}

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["model_id"], captured["payload"], captured["key"] = model_id, payload, api_key
        return {"request_id": "req-k1"}

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), wait, dl, verify as verify_mock:
        result = KlingReferenceVideo().execute(_inputs(env))
    assert result.success, result.error
    assert captured["model_id"] == FIXTURE["model_id"]
    assert _shared.fal_app_id(FIXTURE["model_id"]) == FIXTURE["app_id"]
    assert captured["key"] == "test-key"
    assert set(captured["payload"]) <= set(FIXTURE["input"]["properties"])
    assert captured["payload"] == {
        "prompt": "@Image1 turns toward the camera",
        "image_urls": ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png"],
        "duration": "5",
        "aspect_ratio": "16:9",
        "generate_audio": False,
    }
    assert captured["payload"]["duration"] in FIXTURE["input"]["properties"]["duration"]["enum"]
    assert verify_mock.call_args.kwargs == {"require_audio": False}
    out = Path(result.data["output_path"])
    assert out.read_bytes() == b"fake-mp4"
    assert not any((env / ".staging").iterdir())
    assert result.metadata == {"model_endpoint": FIXTURE["model_id"], "provider_request_id": "req-k1",
                               "generator_kind": "model", "prompt": "@Image1 turns toward the camera", "seed": None,
                               "references_applied": []}
    receipt = find_generation(env, sha256_file(out))
    assert receipt and receipt["provider_request_id"] == "req-k1" and receipt["model_endpoint"] == FIXTURE["model_id"]
    assert gates.verify_generation_receipt(receipt)
    assert len(read_jsonl(generation_receipts_path(env))) == 1
    assert not list((Path(os.environ["OPENMONTAGE_GATES_DIR"]) / "generation-wal").glob("*.json"))
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "completed" and res["provider_request_id"] == "req-k1"
    assert res["endpoint"] == FIXTURE["model_id"]
    price = FIXTURE["pricing"]["usd_per_second"]
    assert res["reserved_usd"] == pytest.approx(price["audio_off"] * 5, abs=1e-4)
    assert result.cost_usd == pytest.approx(price["audio_off"] * 5, abs=1e-4)
    assert project_tracker(env).budget_spent_usd == pytest.approx(result.cost_usd, abs=1e-4)


def test_audio_on_uses_audio_rate_and_requires_audio_stream(env):
    captured = {}
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", side_effect=lambda m, p, **k: captured.update(payload=p) or {"request_id": "r"}), \
         wait, dl, verify as verify_mock:
        r = KlingReferenceVideo().execute(_inputs(env, generate_audio=True, duration="10", shot_type="customize",
                                                  start_image_url="https://v3.fal.media/s.png"))
    assert r.success, r.error
    assert captured["payload"]["generate_audio"] is True and captured["payload"]["shot_type"] == "customize"
    assert captured["payload"]["start_image_url"] == "https://v3.fal.media/s.png"
    assert verify_mock.call_args.kwargs == {"require_audio": True}
    assert r.cost_usd == pytest.approx(FIXTURE["pricing"]["usd_per_second"]["audio_on"] * 10, abs=1e-4)


def test_multi_prompt_payload_and_cost(env):
    captured = {}
    wait, dl, verify = _happy_path()
    segs = [{"prompt": "@Image1 stands still", "duration": "3"}, {"prompt": "@Image1 walks off", "duration": "4"}]
    with patch.object(_shared, "fal_queue_submit", side_effect=lambda m, p, **k: captured.update(payload=p) or {"request_id": "r"}), \
         wait, dl, verify:
        r = KlingReferenceVideo().execute(_inputs(env, prompt=None, multi_prompt=segs))
    assert r.success, r.error
    assert captured["payload"]["multi_prompt"] == segs and "prompt" not in captured["payload"] and "duration" not in captured["payload"]
    assert r.cost_usd == pytest.approx(0.112 * 7, abs=1e-4)


def test_cost_estimate_from_fixture():
    tool = KlingReferenceVideo()
    p = FIXTURE["pricing"]["usd_per_second"]
    assert tool.estimate_cost({"duration": "10"}) == pytest.approx(p["audio_off"] * 10)
    assert tool.estimate_cost({"duration": "10", "generate_audio": True}) == pytest.approx(p["audio_on"] * 10)


def _urls(n):
    return [f"https://fal.media/{i}.png" for i in range(n)]


@pytest.mark.parametrize("kw,msg", [
    ({"reference_image_urls": _urls(10)}, "at most 9 references"),
    ({"reference_image_urls": _urls(9), "asset_class": "shot_visual", "shot_id": "s", "storyboard_frame_sha256": "ab" * 32},
     "at most 9 references"),
    ({"reference_image_urls": _urls(5), "reference_form": "elements", "reference_video_url": "https://fal.media/v.mp4"},
     "at most 4 references in total"),
    ({"reference_image_urls": _urls(1), "reference_form": "elements", "reference_video_url": "https://fal.media/v.mp4"},
     "needs at least one element"),
    ({"reference_video_url": "https://fal.media/v.mp4"}, "only accepted in the elements form"),
    ({"prompt": None}, "exactly one of prompt or multi_prompt"),
    ({"multi_prompt": [{"prompt": "x", "duration": "3"}]}, "exactly one of prompt or multi_prompt"),
    ({"prompt": None, "multi_prompt": [{"prompt": "x", "duration": "16"}]}, "multi_prompt[0]"),
])
def test_preflight_rejects_without_network(env, kw, msg):
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as up:
        result = KlingReferenceVideo().execute(_inputs(env, **kw))
    assert not result.success and msg in result.error, result.error
    submit.assert_not_called()
    up.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_video_cap_counts_elements_plus_images():
    tool = KlingReferenceVideo()
    manifest = [{"visual_bible_entity_id": f"char-{i}", "role": "hero"} for i in range(3)]
    ok = {"prompt": "p", "reference_form": "elements", "reference_manifest": manifest,
          "reference_image_paths": ["a", "b", "c"], "reference_image_urls": _urls(1),
          "reference_video_url": "https://fal.media/v.mp4"}
    assert tool._preflight_error(ok) is None  # 3 elements + 1 image = 4
    over = {**ok, "reference_image_urls": _urls(2)}
    assert "at most 4" in tool._preflight_error(over)


# ---- trust boundary ----

def test_unregistered_project_dir_rejected_before_any_network(env, tmp_path):
    stray = tmp_path.parent / f"{tmp_path.name}-stray"
    stray.mkdir()
    (stray / "project.yaml").write_bytes((env / "project.yaml").read_bytes())
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as up:
        r = KlingReferenceVideo().execute(_inputs(env, project_dir=str(stray), output_path=str(stray / "s.mp4")))
        assert not r.success and "preflight" in r.error and "registered project" in r.error
    submit.assert_not_called()
    up.assert_not_called()


def test_unapproved_project_config_rejected(env):
    cfg = env / "project.yaml"
    cfg.write_bytes(cfg.read_bytes().replace(b"budget_usd_cap: 50.0", b"budget_usd_cap: 5000.0"))
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env))
    assert not r.success and "approval receipt" in r.error
    submit.assert_not_called()


def test_egress_prompts_only_blocks_local_reference_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, "proj-hermit", egress_classes=("prompts",))
    (project / "assets" / "video").mkdir(parents=True)
    ref = project / "assets" / "ref.png"
    ref.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"),
                                                  reference_image_paths=[str(ref)]))
    assert not r.success and "reference_images" in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_reference_path_outside_project_rejected_before_upload(env, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, reference_image_paths=[str(outside)]))
    assert not r.success and "preflight" in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_non_shot_local_reference_requires_manifest(env):
    hero = write_receipted_png(env, "canon/visual/objects/h.png")
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, reference_image_urls=[], reference_image_paths=[str(hero["path"])]))
    assert not r.success and "reference_manifest" in r.error
    up.assert_not_called()
    submit.assert_not_called()


# ---- storyboard: required for shot_visual, frame packed last ----

def _shot_setup(project, *, approve=True, extra_manifest=()):
    hero = write_receipted_png(project, "canon/visual/objects/hero.png", _png((1, 2, 3)))
    frame = write_receipted_png(project, "assets/storyboards/sh-1.png", _png((9, 8, 7)))
    if approve:
        approve_storyboard_batch(project, {"sh-1": frame["sha256"]})
    manifest = [{"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png", "role": "hero",
                 "visual_bible_entity_id": "char-01-aaaaaaaa"}]
    paths = [str(hero["path"])]
    for name, role, entity in extra_manifest:
        img = write_receipted_png(project, f"canon/visual/objects/{name}.png", _png((len(name), (sum(map(ord, name)) % 251) + 1, (sum(map(ord, role)) % 251) + 1)))
        manifest.append({"asset_id": img["sha256"], "path": f"canon/visual/objects/{name}.png", "role": role,
                         "visual_bible_entity_id": entity})
        paths.append(str(img["path"]))
    inputs = dict(
        reference_image_urls=[], reference_image_paths=paths, reference_manifest=manifest,
        asset_class="shot_visual", shot_id="sh-1", storyboard_frame_sha256=frame["sha256"],
        storyboard_frame_path="assets/storyboards/sh-1.png",
    )
    expected = manifest + [{"asset_id": frame["sha256"], "path": "assets/storyboards/sh-1.png",
                            "role": "storyboard", "shot_id": "sh-1"}]
    return inputs, expected, hero, frame


def test_shot_visual_requires_shot_id_and_frame_hash(env, monkeypatch):
    calls = []
    monkeypatch.setattr(receipts_mod, "require_storyboard_receipt", lambda *a: calls.append(a), raising=False)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, asset_class="shot_visual"))
        assert not r.success and "shot_id" in r.error
        r = KlingReferenceVideo().execute(_inputs(env, asset_class="shot_visual", shot_id="s1"))
        assert not r.success and "storyboard_frame_sha256" in r.error
    submit.assert_not_called()
    assert calls == []


def test_shot_visual_missing_receipt_fails_preflight(env):
    shot, *_ = _shot_setup(env, approve=False)
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, **shot))
    assert not r.success and "preflight" in r.error and "storyboard" in r.error
    up.assert_not_called()
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_shot_visual_refuses_unprovable_urls(env):
    shot, *_ = _shot_setup(env)
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        for key in ("reference_image_urls", "start_image_url"):
            val = ["https://v3.fal.media/x.png"] if key == "reference_image_urls" else "https://v3.fal.media/x.png"
            r = KlingReferenceVideo().execute(_inputs(env, **dict(shot, **{key: val})))
            assert not r.success and key in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_shot_visual_happy_path_packs_verified_frame_last_and_seals_references(env):
    shot, expected, hero, frame = _shot_setup(env)
    uploads, captured = [], {}

    def fake_upload(path):
        uploads.append(Path(path))
        return f"https://v3.fal.media/up{len(uploads)}.png"

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "upload_image_fal", side_effect=fake_upload), \
         patch.object(_shared, "fal_queue_submit", side_effect=lambda m, p, **k: captured.update(payload=p) or {"request_id": "req-sb"}), \
         wait, dl, verify:
        r = KlingReferenceVideo().execute(_inputs(env, **shot))
    assert r.success, r.error
    assert uploads == [hero["path"].resolve(), frame["path"].resolve()]
    assert captured["payload"]["image_urls"] == ["https://v3.fal.media/up1.png", "https://v3.fal.media/up2.png"]
    assert "elements" not in captured["payload"]
    assert r.metadata["references_applied"] == expected
    receipt = find_generation(env, sha256_file(Path(r.data["output_path"])))
    assert receipt["references_applied"] == expected
    assert gates.verify_generation_receipt(receipt)


def test_shot_visual_frame_hash_mismatch_rejected_before_upload(env):
    shot, _, _, frame = _shot_setup(env)
    frame["path"].write_bytes(_png((0, 0, 0)))
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, **shot))
    assert not r.success and "not the file that would be uploaded" in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_reference_manifest_hash_mismatch_rejected_before_upload(env):
    shot, *_ = _shot_setup(env)
    bad = [dict(shot["reference_manifest"][0], asset_id="e" * 64)]
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, **dict(shot, reference_manifest=bad)))
    assert not r.success and "does not match the file" in r.error
    up.assert_not_called()
    submit.assert_not_called()


# ---- elements form ----

def test_elements_form_groups_manifest_by_entity_with_frame_as_image(env):
    shot, expected, hero, frame = _shot_setup(
        env, extra_manifest=[("wardrobe", "wardrobe", "char-01-aaaaaaaa"), ("plate", "establishing", "loc-01-bbbbbbbb")])
    uploads, captured = [], {}

    def fake_upload(path):
        uploads.append(Path(path))
        return f"https://v3.fal.media/up{len(uploads)}.png"

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "upload_image_fal", side_effect=fake_upload), \
         patch.object(_shared, "fal_queue_submit", side_effect=lambda m, p, **k: captured.update(payload=p) or {"request_id": "req-el"}), \
         wait, dl, verify:
        r = KlingReferenceVideo().execute(_inputs(env, **shot, reference_form="elements",
                                                  prompt="@Element1 walks into @Image1's composition"))
    assert not r.success and "no role 'hero'" in r.error  # the location entity has no frontal
    assert captured == {}

    # a location is not an element: keep it out of the manifest for elements form
    shot2, expected2, *_ = _shot_setup(env, extra_manifest=[("wardrobe2", "wardrobe", "char-01-aaaaaaaa")])
    uploads.clear()
    with patch.object(_shared, "upload_image_fal", side_effect=fake_upload), \
         patch.object(_shared, "fal_queue_submit", side_effect=lambda m, p, **k: captured.update(payload=p) or {"request_id": "req-el"}), \
         wait, dl, verify:
        r = KlingReferenceVideo().execute(_inputs(env, **shot2, reference_form="elements",
                                                  reference_video_url="https://fal.media/motion.mp4",
                                                  prompt="@Element1 walks; match @Image1"))
    assert r.success, r.error
    payload = captured["payload"]
    assert payload["elements"] == [{"frontal_image_url": "https://v3.fal.media/up1.png",
                                    "reference_image_urls": ["https://v3.fal.media/up2.png"],
                                    "video_url": "https://fal.media/motion.mp4"}]
    assert payload["image_urls"] == ["https://v3.fal.media/up3.png"]  # storyboard frame only
    element_keys = set(FIXTURE["input"]["properties"]["elements"]["items"]["properties"])
    assert all(set(e) <= element_keys for e in payload["elements"])
    assert r.metadata["references_applied"] == expected2


def test_elements_form_rejects_more_than_three_references_per_element(env):
    shot, *_ = _shot_setup(env, extra_manifest=[(f"v{i}", "angle", "char-01-aaaaaaaa") for i in range(4)])
    with patch.object(_shared, "upload_image_fal", return_value="https://v3.fal.media/u.png"), \
         patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env, **shot, reference_form="elements"))
    assert not r.success and "at most 3" in r.error
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


# ---- reservation states ----

def test_crash_between_submit_and_attach_leaves_reservation_submitting(env):
    with patch.object(_shared, "fal_queue_submit", side_effect=ConnectionError("socket died mid-flight")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        result = KlingReferenceVideo().execute(_inputs(env))
    assert not result.success
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


def test_attach_request_id_failure_stays_submitting(env):
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-lost"}), \
         patch("tools.cost_tracker.attach_request_id", side_effect=OSError("disk full")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        result = KlingReferenceVideo().execute(_inputs(env))
    assert not result.success and "disk full" in result.error
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


@pytest.mark.parametrize("stage", ["wait", "download", "verify"])
def test_failure_after_acceptance_is_pending_billing(env, stage):
    wait = patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalDeadlineExceeded("late")) if stage == "wait" \
        else patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/o.mp4"}})
    dl = patch.object(_shared, "fal_download", side_effect=_shared.FalDownloadError("too big")) if stage == "download" \
        else patch.object(_shared, "fal_download", side_effect=_fake_download)
    verify = patch.object(_shared, "verify_video_file", side_effect=_shared.VideoVerificationError("no video stream")) if stage == "verify" \
        else patch.object(_shared, "verify_video_file", return_value=dict(PROBED))
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-x"}), wait, dl, verify:
        result = KlingReferenceVideo().execute(_inputs(env))
    assert not result.success
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "pending_billing" and res["provider_request_id"] == "req-x"
    assert res["actual_usd"] == pytest.approx(res["reserved_usd"])
    assert project_tracker(env).budget_spent_usd == pytest.approx(res["reserved_usd"], abs=1e-4)
    assert not (env / "assets" / "video" / "shot.mp4").exists()
    assert not (env / ".staging").exists() or not any((env / ".staging").iterdir())
    with pytest.raises(IndeterminatePaidCallError, match="pending_billing"):
        resume_check(env)


def test_crash_after_staging_is_replayed_by_resume_check(env):
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-crash"}), wait, dl, verify, \
         patch.object(receipts_mod, "complete_generation", side_effect=OSError("power cut")):
        r = KlingReferenceVideo().execute(_inputs(env))
    assert not r.success and "power cut" in r.error
    out = env / "assets" / "video" / "shot.mp4"
    assert find_generation(env, sha256_file(out)) is None
    assert list(load_reservations(env).values())[0]["state"] == "submitting"
    resume_check(env)
    receipt = find_generation(env, sha256_file(out))
    assert receipt and receipt["provider_request_id"] == "req-crash" and receipt["tool"] == "kling_reference_video"
    assert list(load_reservations(env).values())[0]["state"] == "completed"


def test_nonterminal_reservation_blocks_new_paid_call_before_any_upload(env):
    from tests.tools._authored_film_helpers import make_tracker
    from tools.cost_tracker import reserve_paid_call

    reserve_paid_call(make_tracker(env), env, tool="kling_reference_video", endpoint="x/y",
                      normalized_inputs_hash="ab" * 32, reserved_usd=0.5)
    with patch.object(_shared, "upload_image_fal") as upload, patch.object(_shared, "fal_queue_submit") as submit:
        r = KlingReferenceVideo().execute(_inputs(env))
    assert not r.success and "reconcile" in r.error.lower()
    upload.assert_not_called()
    submit.assert_not_called()
    assert len(load_reservations(env)) == 1

"""Seedance 2.5 reference-to-video path (mocked FAL), trust boundary, and 2.0 routing."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import lib.receipts as receipts_mod
from lib import gates
from lib.receipts import GenerationWalError, find_generation, generation_receipts_path
from lib.pathsafe import sha256_file
from lib.state_io import read_jsonl
from tools.cost_tracker import IndeterminatePaidCallError, load_reservations, resume_check
from tools.video import _shared
from tools.video.seedance_video import SeedanceVideo

from tests.tools._authored_film_helpers import (
    approve_storyboard_batch,
    make_verified_project,
    project_tracker,
    tiny_png_bytes,
    write_receipted_png,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "bytedance-seedance-2.5-reference-to-video.json").read_text()
)
PROBED = {"file_size_bytes": 8, "file_size_mb": 0.0, "duration_seconds": 6.0, "video_width": 1280,
          "video_height": 720, "video_codec": "h264", "has_audio": True, "audio_codec": "aac"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch)
    (project / "assets" / "video").mkdir(parents=True)
    return project


@pytest.fixture
def legacy_env(monkeypatch, tmp_path):
    """A registered project WITHOUT project.yaml and without a pipeline pin (legacy 1.1)."""
    from tests.tools._authored_film_helpers import make_project

    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_project(tmp_path, monkeypatch, "proj-legacy")
    (project / "assets" / "video").mkdir(parents=True)
    return project


def _inputs(project, **extra):
    base = {
        "prompt": "@Image1 walks through fog",
        "operation": "reference_to_video",
        "reference_image_urls": ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png"],
        "duration": "6",
        "resolution": "720p",
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
    wait_result = wait_result or {"video": {"url": "https://v3.fal.media/o.mp4"}, "seed": 4}
    return (
        patch.object(_shared, "fal_queue_wait", return_value=wait_result),
        patch.object(_shared, "fal_download", side_effect=_fake_download),
        patch.object(_shared, "verify_video_file", return_value=dict(PROBED)),
    )


def test_v25_payload_matches_fixture_shape(env):
    captured = {}

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["model_id"], captured["payload"], captured["key"] = model_id, payload, api_key
        return {"request_id": "req-25"}

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), wait, dl, verify as verify_mock:
        result = SeedanceVideo().execute(_inputs(env, reference_video_urls=["https://fal.media/ref.mp4"],
                                                 reference_audio_urls=["https://fal.media/ref.mp3"]))
    assert result.success, result.error
    assert captured["model_id"] == FIXTURE["model_id"]
    assert captured["key"] == "test-key"
    allowed = set(FIXTURE["input"]["properties"])
    assert set(captured["payload"]) <= allowed
    assert captured["payload"] == {
        "prompt": "@Image1 walks through fog",
        "image_urls": ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png"],
        "video_urls": ["https://fal.media/ref.mp4"],
        "audio_urls": ["https://fal.media/ref.mp3"],
        "resolution": "720p",
        "duration": "6",
        "aspect_ratio": "16:9",
        "generate_audio": True,
        "bitrate_mode": "standard",
    }
    assert "image_url" not in captured["payload"]
    verify_mock.assert_called_once()
    assert verify_mock.call_args.kwargs == {"require_audio": True}
    out = Path(result.data["output_path"])
    assert out.read_bytes() == b"fake-mp4"
    assert not any((env / ".staging").iterdir())
    assert result.metadata == {"model_endpoint": FIXTURE["model_id"], "provider_request_id": "req-25",
                               "generator_kind": "model", "prompt": "@Image1 walks through fog", "seed": 4,
                               "references_applied": []}
    receipt = find_generation(env, sha256_file(out))
    assert receipt and receipt["provider_request_id"] == "req-25"
    assert receipt["prompt"] == "@Image1 walks through fog" and receipt["seed"] == 4
    assert receipt["references_applied"] == []
    # exactly one receipt row: the in-tool WAL completion and the wrapper agree
    assert len(read_jsonl(generation_receipts_path(env))) == 1
    assert not list((Path(os.environ["OPENMONTAGE_GATES_DIR"]) / "generation-wal").glob("*.json"))
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "completed" and res["provider_request_id"] == "req-25"
    assert res["output_hint"] == {"kind": "video", "output_path": str(out), "generate_audio": True}
    assert res["reserved_usd"] == pytest.approx(0.4730 * 6 * 0.6, abs=1e-4)
    assert result.cost_usd == pytest.approx(0.4730 * 6 * 0.6, abs=1e-4)
    # the budget came from the verified project.yaml, not from the caller
    assert project_tracker(env).budget_spent_usd == pytest.approx(result.cost_usd, abs=1e-4)


def test_no_retries_advertised_for_paid_tool():
    assert SeedanceVideo.retry_policy.max_retries == 0


def test_v25_cost_estimate_from_fixture():
    tool = SeedanceVideo()
    p = FIXTURE["pricing"]["usd_per_second"]
    assert tool.estimate_cost({"duration": "10", "resolution": "720p"}) == pytest.approx(p["720p"] * 10)
    assert tool.estimate_cost({"duration": "10", "resolution": "480p"}) == pytest.approx(p["480p"] * 10)
    assert tool.estimate_cost({"duration": "auto"}) == pytest.approx(p["720p"] * 5)


def _urls(n, ext):
    return [f"https://fal.media/{i}.{ext}" for i in range(n)]


@pytest.mark.parametrize("kw,msg", [
    ({"reference_image_urls": _urls(31, "png")}, "at most 30 reference images"),
    ({"reference_image_urls": _urls(30, "png"), "reference_image_paths": ["x.png"]}, "at most 30 reference images"),
    ({"reference_video_urls": _urls(11, "mp4")}, "at most 10 reference videos"),
    ({"reference_audio_urls": _urls(11, "mp3")}, "at most 10 reference audio"),
    ({"operation": "image_to_video", "image_url": "https://fal.media/s.png"}, "no start/end-frame"),
])
def test_v25_preflight_rejects_over_cap_without_network(env, kw, msg):
    with patch.object(_shared, "fal_queue_submit") as submit:
        result = SeedanceVideo().execute(_inputs(env, **kw))
    assert not result.success and msg in result.error
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_v25_total_refs_cap_is_50(env):
    tool = SeedanceVideo()
    at_cap = {"operation": "reference_to_video", "reference_image_urls": _urls(30, "png"),
              "reference_video_urls": _urls(10, "mp4"), "reference_audio_urls": _urls(10, "mp3")}
    assert tool._v25_preflight_error(at_cap) is None
    tool.V25_LIMITS = {**tool.V25_LIMITS, "audios": 11}
    over = {**at_cap, "reference_audio_urls": _urls(11, "mp3")}
    assert "50 reference files in total" in tool._v25_preflight_error(over)


# ---- trust boundary (inspection #2) ----

def test_unregistered_project_dir_rejected_before_any_network(env, tmp_path, monkeypatch):
    import lib.paths as paths_mod

    stray = tmp_path.parent / f"{tmp_path.name}-stray"
    stray.mkdir()
    (stray / "project.yaml").write_bytes((env / "project.yaml").read_bytes())
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as up:
        r = SeedanceVideo().execute(_inputs(env, project_dir=str(stray), output_path=str(stray / "s.mp4")))
        assert not r.success and "preflight" in r.error and "registered project" in r.error
        r = SeedanceVideo().execute({**_inputs(env), "project_dir": ""})
        assert not r.success and "project_dir" in r.error
    submit.assert_not_called()
    up.assert_not_called()
    assert not (stray / "cost-reservations.jsonl").exists()


def test_caller_injected_tracker_and_cap_are_ignored(env, tmp_path, monkeypatch):
    """Budget comes only from the approved project.yaml (cap 0.5 here)."""
    from tests.tools._authored_film_helpers import make_tracker

    project = make_verified_project(tmp_path, monkeypatch, "proj-thrift", budget=0.5)
    (project / "assets" / "video").mkdir(parents=True)
    generous = make_tracker(project, budget=1000.0)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"),
                                            cost_tracker=generous, budget_usd_cap=1000.0))
    assert not r.success and "exceeds usable budget" in r.error
    submit.assert_not_called()


def test_unapproved_project_config_rejected(env, tmp_path, monkeypatch):
    """Editing project.yaml after approval invalidates it (digest no longer bound)."""
    cfg = env / "project.yaml"
    cfg.write_bytes(cfg.read_bytes().replace(b"budget_usd_cap: 50.0", b"budget_usd_cap: 5000.0"))
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env))
    assert not r.success and "approval receipt" in r.error
    submit.assert_not_called()


def test_egress_prompts_only_blocks_local_reference_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, "proj-hermit", egress_classes=("prompts",))
    (project / "assets" / "video").mkdir(parents=True)
    ref = project / "assets" / "ref.png"
    ref.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"),
                                            reference_image_paths=[str(ref)]))
    assert not r.success and "reference_images" in r.error
    up.assert_not_called()
    submit.assert_not_called()
    # URL-only references need only the prompts class
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-p"}), wait, dl, verify:
        r = SeedanceVideo().execute(_inputs(project, output_path=str(project / "assets" / "video" / "s.mp4")))
    assert r.success, r.error


def test_reference_path_outside_project_rejected_before_upload(env, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, reference_image_paths=[str(outside)]))
    assert not r.success and "preflight" in r.error
    up.assert_not_called()
    submit.assert_not_called()


# ---- storyboard receipt + frame proven in the payload (inspection #6, Codex R2 #4) ----

def _png(color):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="PNG")
    return buf.getvalue()


def _shot_setup(project, *, approve=True):
    """One approved hero sheet + one approved storyboard frame for shot 'sh-1'.
    Returns (shot inputs, expected references_applied)."""
    hero = write_receipted_png(project, "canon/visual/objects/hero.png", _png((1, 2, 3)))
    frame = write_receipted_png(project, "assets/storyboards/sh-1.png", _png((9, 8, 7)))
    if approve:
        approve_storyboard_batch(project, {"sh-1": frame["sha256"]})
    manifest = [{"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png", "role": "hero",
                 "visual_bible_entity_id": "char-01-aaaaaaaa"}]
    inputs = dict(
        reference_image_urls=[], reference_image_paths=[str(hero["path"])], reference_manifest=manifest,
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
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual"))
        assert not r.success and "shot_id" in r.error
        r = SeedanceVideo().execute(_inputs(env, asset_class="shot_visual", shot_id="s1"))
        assert not r.success and "storyboard_frame_sha256" in r.error
    submit.assert_not_called()
    assert calls == []


def test_shot_visual_missing_receipt_fails_preflight(env, monkeypatch):
    def deny(project_root, shot_id, frame_sha):
        raise ValueError(f"no signed storyboard approval covers {shot_id}/{frame_sha[:8]}")

    monkeypatch.setattr(receipts_mod, "require_storyboard_receipt", deny, raising=False)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, reference_image_urls=[], asset_class="shot_visual", shot_id="s1",
                                            storyboard_frame_sha256="ab" * 32))
    assert not r.success and "preflight" in r.error and "storyboard approval" in r.error
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_shot_visual_checker_absent_fails_closed(env, monkeypatch):
    monkeypatch.delattr(receipts_mod, "require_storyboard_receipt", raising=False)
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, reference_image_urls=[], asset_class="shot_visual", shot_id="s1",
                                            storyboard_frame_sha256="ab" * 32))
    assert not r.success and "require_storyboard_receipt" in r.error
    submit.assert_not_called()


def test_shot_visual_happy_path_packs_verified_frame_last_and_seals_references(env):
    shot, expected, hero, frame = _shot_setup(env)
    uploads, captured = [], {}

    def fake_upload(path):
        uploads.append(Path(path))
        return f"https://v3.fal.media/up{len(uploads)}.png"

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["payload"] = payload
        return {"request_id": "req-sb"}

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "upload_image_fal", side_effect=fake_upload), \
         patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), wait, dl, verify:
        r = SeedanceVideo().execute(_inputs(env, **shot))
    assert r.success, r.error
    assert uploads == [hero["path"].resolve(), frame["path"].resolve()]
    assert captured["payload"]["image_urls"] == ["https://v3.fal.media/up1.png", "https://v3.fal.media/up2.png"]
    assert r.metadata["references_applied"] == expected
    receipt = find_generation(env, sha256_file(Path(r.data["output_path"])))
    assert receipt["references_applied"] == expected
    assert gates.verify_generation_receipt(receipt)


def test_shot_visual_frame_located_by_hash_in_objects_when_path_omitted(env):
    shot, expected, hero, frame = _shot_setup(env)
    objects = env / "canon" / "visual" / "objects" / f"{frame['sha256']}.png"
    objects.write_bytes(frame["path"].read_bytes())
    shot.pop("storyboard_frame_path")
    uploads = []
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "upload_image_fal", side_effect=lambda p: uploads.append(Path(p)) or "https://v3.fal.media/u.png"), \
         patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-obj"}), wait, dl, verify:
        r = SeedanceVideo().execute(_inputs(env, **shot))
    assert r.success, r.error
    assert uploads[-1] == objects.resolve()
    assert r.metadata["references_applied"][-1]["path"] == f"canon/visual/objects/{frame['sha256']}.png"


def test_shot_visual_frame_hash_mismatch_rejected_before_upload(env):
    shot, _, _, frame = _shot_setup(env)
    frame["path"].write_bytes(_png((0, 0, 0)))  # approved hash, different bytes
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, **shot))
    assert not r.success and "preflight" in r.error and "not the file that would be uploaded" in r.error
    up.assert_not_called()
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_shot_visual_missing_frame_file_rejected(env):
    shot, _, _, frame = _shot_setup(env)
    frame["path"].unlink()
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, **shot))
    assert not r.success and "not a project-local file" in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_shot_visual_refuses_unprovable_url_references(env):
    shot, *_ = _shot_setup(env)
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, **dict(shot, reference_image_urls=["https://v3.fal.media/x.png"])))
    assert not r.success and "reference_image_urls" in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_reference_manifest_hash_mismatch_rejected_before_upload(env):
    shot, _, hero, _ = _shot_setup(env)
    bad = [dict(shot["reference_manifest"][0], asset_id="e" * 64)]
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, **dict(shot, reference_manifest=bad)))
        assert not r.success and "does not match the file" in r.error
        r = SeedanceVideo().execute(_inputs(env, **dict(shot, reference_manifest=[])))
        assert not r.success and "reference_manifest must list one object" in r.error
        other = write_receipted_png(env, "canon/visual/objects/other.png", _png((5, 5, 5)))
        swapped = [dict(shot["reference_manifest"][0], path="canon/visual/objects/other.png", asset_id=other["sha256"])]
        r = SeedanceVideo().execute(_inputs(env, **dict(shot, reference_manifest=swapped)))
        assert not r.success and "is not reference_image_paths[0]" in r.error
        board_in_manifest = [{"asset_id": hero["sha256"], "path": "canon/visual/objects/hero.png",
                              "role": "storyboard", "shot_id": "sh-1"}]
        r = SeedanceVideo().execute(_inputs(env, **dict(shot, reference_manifest=board_in_manifest)))
        assert not r.success and "packs the approved frame itself" in r.error
    up.assert_not_called()
    submit.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_non_shot_local_reference_requires_manifest(env):
    hero = write_receipted_png(env, "canon/visual/objects/h.png")
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedanceVideo().execute(_inputs(env, reference_image_urls=[], reference_image_paths=[str(hero["path"])]))
    assert not r.success and "reference_manifest" in r.error
    up.assert_not_called()
    submit.assert_not_called()


def test_storyboard_frame_counts_against_image_cap(env):
    tool = SeedanceVideo()
    at_cap = {"operation": "reference_to_video", "reference_image_paths": [f"{i}.png" for i in range(30)]}
    assert tool._v25_preflight_error(at_cap) is None
    assert "at most 30 reference images" in tool._v25_preflight_error({**at_cap, "asset_class": "shot_visual"})


# ---- crash-safe completion: generation WAL (Codex R2 #5) ----

def _wal_entries():
    return list((Path(os.environ["OPENMONTAGE_GATES_DIR"]) / "generation-wal").glob("*.json"))


def test_crash_after_staging_is_replayed_by_resume_check(env):
    """Crash after the output landed but before receipt/ledger/terminal state:
    resume_check replays the WAL — receipt + ledger + completed reservation."""
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-crash"}), wait, dl, verify, \
         patch.object(receipts_mod, "complete_generation", side_effect=OSError("power cut")):
        r = SeedanceVideo().execute(_inputs(env))
    assert not r.success and "power cut" in r.error
    out = env / "assets" / "video" / "shot.mp4"
    assert out.read_bytes() == b"fake-mp4"  # the paid output survived
    assert find_generation(env, sha256_file(out)) is None
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] == "req-crash"
    assert len(_wal_entries()) == 1

    resume_check(env)  # replays instead of blocking
    receipt = find_generation(env, sha256_file(out))
    assert receipt and receipt["provider_request_id"] == "req-crash" and receipt["prompt"] == "@Image1 walks through fog"
    assert gates.verify_generation_receipt(receipt)
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "completed" and res["actual_usd"] == pytest.approx(res["reserved_usd"])
    assert project_tracker(env).budget_spent_usd == pytest.approx(res["reserved_usd"], abs=1e-4)
    assert _wal_entries() == []
    resume_check(env)  # nothing left, nothing raised


def test_crash_after_receipt_replay_is_idempotent(env):
    """Receipt + ledger + terminal state written, WAL delete lost: the replay
    adds no second receipt, no second ledger row, no re-reconcile."""
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-half"}), wait, dl, verify, \
         patch.object(gates, "generation_wal_delete", side_effect=OSError("disk yanked")):
        r = SeedanceVideo().execute(_inputs(env))
    assert not r.success and "disk yanked" in r.error
    out = env / "assets" / "video" / "shot.mp4"
    first = find_generation(env, sha256_file(out))
    assert first is not None
    assert list(load_reservations(env).values())[0]["state"] == "completed"
    assert len(_wal_entries()) == 1
    spent = project_tracker(env).budget_spent_usd

    replayed = receipts_mod.recover_generation_wal(env)
    assert [r["receipt_id"] for r in replayed] == [first["receipt_id"]]
    assert _wal_entries() == []
    assert len(read_jsonl(generation_receipts_path(env))) == 1
    ledger = [row for row in read_jsonl(gates.generation_ledger_path()) if row.get("receipt_id") == first["receipt_id"]]
    assert len(ledger) == 1
    assert project_tracker(env).budget_spent_usd == pytest.approx(spent)
    resume_check(env)


def test_missing_staged_output_blocks_resume_check_and_new_paid_calls(env):
    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-lost"}), wait, dl, verify, \
         patch.object(receipts_mod, "complete_generation", side_effect=OSError("power cut")):
        assert not SeedanceVideo().execute(_inputs(env)).success
    (env / "assets" / "video" / "shot.mp4").unlink()
    with pytest.raises(GenerationWalError, match="missing"):
        resume_check(env)
    assert len(_wal_entries()) == 1
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as up:
        r = SeedanceVideo().execute(_inputs(env))
    assert not r.success and "missing" in r.error
    submit.assert_not_called()
    up.assert_not_called()
    assert len(load_reservations(env)) == 1


def test_crash_before_move_replays_from_staging(env):
    """WAL written, process died before atomic_move: the staged file is moved
    into place by the replay (hash-checked) and receipted."""
    from lib.state_io import atomic_move

    wait, dl, verify = _happy_path()
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-stage"}), wait, dl, verify, \
         patch("tools.video.seedance_video.atomic_move", side_effect=OSError("died"), create=True), \
         patch("lib.state_io.atomic_move", side_effect=OSError("died")):
        assert not SeedanceVideo().execute(_inputs(env)).success
    out = env / "assets" / "video" / "shot.mp4"
    assert not out.exists() and any((env / ".staging").iterdir())
    resume_check(env)
    assert out.read_bytes() == b"fake-mp4"
    assert find_generation(env, sha256_file(out))["provider_request_id"] == "req-stage"
    assert not any((env / ".staging").iterdir())
    assert _wal_entries() == []


# ---- reservation states (inspection #3/#4/#5) ----

def test_crash_between_submit_and_attach_leaves_reservation_submitting(env):
    with patch.object(_shared, "fal_queue_submit", side_effect=ConnectionError("socket died mid-flight")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        result = SeedanceVideo().execute(_inputs(env))
    assert not result.success
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


def test_attach_request_id_failure_stays_submitting_and_blocks_resume(env):
    import tools.video.seedance_video as mod

    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-lost"}), \
         patch("tools.cost_tracker.attach_request_id", side_effect=OSError("disk full")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        result = SeedanceVideo().execute(_inputs(env))
    assert not result.success and "disk full" in result.error
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    assert project_tracker(env).budget_reserved_usd > 0  # never reconciled as failed
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)


@pytest.mark.parametrize("stage", ["wait", "download", "verify"])
def test_failure_after_acceptance_is_pending_billing_and_retains_reservation(env, stage):
    wait = patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalDeadlineExceeded("late")) if stage == "wait" \
        else patch.object(_shared, "fal_queue_wait", return_value={"video": {"url": "https://v3.fal.media/o.mp4"}})
    dl = patch.object(_shared, "fal_download", side_effect=_shared.FalDownloadError("too big")) if stage == "download" \
        else patch.object(_shared, "fal_download", side_effect=_fake_download)
    verify = patch.object(_shared, "verify_video_file", side_effect=_shared.VideoVerificationError("no video stream")) if stage == "verify" \
        else patch.object(_shared, "verify_video_file", return_value=dict(PROBED))
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-x"}), wait, dl, verify:
        result = SeedanceVideo().execute(_inputs(env))
    assert not result.success
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "pending_billing" and res["provider_request_id"] == "req-x"
    assert res["actual_usd"] == pytest.approx(res["reserved_usd"])
    tracker = project_tracker(env)
    assert tracker.budget_spent_usd == pytest.approx(res["reserved_usd"], abs=1e-4)  # no refund
    assert not (env / "assets" / "video" / "shot.mp4").exists()
    assert not (env / ".staging").exists() or not any((env / ".staging").iterdir())
    with pytest.raises(IndeterminatePaidCallError, match="pending_billing"):
        resume_check(env)
    assert not find_generation(env, sha256_file(__file__))


def test_pending_billing_blocks_repeated_calls_at_the_boundary(tmp_path, monkeypatch):
    """A pending_billing reservation retains its budget AND halts every later call
    before submission (resume_check at the paid-call boundary)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, "proj-miser", budget=5.0)
    (project / "assets" / "video").mkdir(parents=True)
    inputs = _inputs(project, output_path=str(project / "assets" / "video" / "s.mp4"))  # ~$2.84 each
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-1"}) as submit, \
         patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalQueueError("poll broke")):
        assert not SeedanceVideo().execute(inputs).success
        r = SeedanceVideo().execute(inputs)
    assert not r.success and "no terminal outcome" in r.error
    assert submit.call_count == 1
    assert len(load_reservations(project)) == 1
    t = project_tracker(project)
    assert t.budget_reserved_usd + t.budget_spent_usd > 0  # retained, never refunded


# ---- 2.0: no ungoverned path (inspection #8) ----

def _assert_nothing_left_the_machine(submit, upload, raw_post):
    submit.assert_not_called()
    upload.assert_not_called()
    raw_post.assert_not_called()


def test_v20_cost_estimate_still_answers():
    assert SeedanceVideo().estimate_cost({"model_version": "2.0", "duration": "5"}) == pytest.approx(0.3034 * 5, abs=0.01)


def test_v20_refused_without_a_resolvable_project(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    with patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "upload_image_fal") as upload, \
         patch("requests.post") as raw_post:
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "output_path": str(tmp_path / "x.mp4")})
    assert not r.success and "registered project" in r.error and "2.5" in r.error
    _assert_nothing_left_the_machine(submit, upload, raw_post)


def test_v20_refused_for_governed_project_by_signed_pin(env, monkeypatch):
    """Governance is decided from the signed pin (lib.look_ingest.project_look_governed), not project.yaml."""
    monkeypatch.setattr(_shared, "project_look_governed", lambda root: True)
    out = env / "assets" / "video" / "legacy.mp4"
    with patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "upload_image_fal") as upload, \
         patch("requests.post") as raw_post:
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "output_path": str(out), "project_dir": str(env)})
    assert not r.success and "look-governed per the signed pipeline pin" in r.error and "2.5" in r.error
    assert "project.yaml" not in r.error
    _assert_nothing_left_the_machine(submit, upload, raw_post)


def test_v20_refused_for_unpinned_project_too(legacy_env):
    """A resolvable project without project.yaml and without a pin (1.1) is refused just the same."""
    out = legacy_env / "assets" / "video" / "legacy.mp4"
    with patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "upload_image_fal") as upload, \
         patch("requests.post") as raw_post:
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "operation": "image_to_video",
                                     "image_path": str(legacy_env / "anything.png"), "output_path": str(out)})
    assert not r.success and "not look-governed per the signed pipeline pin" in r.error and "kling_reference_video" in r.error
    _assert_nothing_left_the_machine(submit, upload, raw_post)
    assert not (legacy_env / "cost-reservations.jsonl").exists()


def test_v20_undecidable_pin_is_treated_as_governed(env, monkeypatch):
    def boom(root):
        raise RuntimeError("chain violation")

    monkeypatch.setattr(_shared, "project_look_governed", boom)
    with patch.object(_shared, "fal_queue_submit") as submit, \
         patch.object(_shared, "upload_image_fal") as upload, \
         patch("requests.post") as raw_post:
        r = SeedanceVideo().execute({"prompt": "p", "model_version": "2.0", "project_dir": str(env),
                                     "output_path": str(env / "assets" / "video" / "l.mp4")})
    assert not r.success and "look-governed per the signed pipeline pin" in r.error
    _assert_nothing_left_the_machine(submit, upload, raw_post)


def test_nonterminal_reservation_blocks_new_paid_call_before_any_upload(env, monkeypatch):
    from tests.tools._authored_film_helpers import make_tracker
    from tools.cost_tracker import reserve_paid_call

    reserve_paid_call(make_tracker(env), env, tool="seedance_video", endpoint="x/y",
                      normalized_inputs_hash="ab" * 32, reserved_usd=0.5)
    ref = env / "ref.png"
    ref.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as upload, \
         patch.object(_shared, "fal_queue_submit") as submit, \
         patch("requests.post") as raw_post, patch("requests.get") as raw_get:
        r = SeedanceVideo().execute(_inputs(env, reference_image_urls=[], reference_image_paths=[str(ref)]))
    assert not r.success and "reconcile" in r.error.lower()
    upload.assert_not_called()
    submit.assert_not_called()
    raw_post.assert_not_called()
    raw_get.assert_not_called()
    assert len(load_reservations(env)) == 1  # no second reservation written

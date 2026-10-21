"""Seedream 5 Pro tool (mocked FAL): payload shape, caps, content-addressed output, trust boundary."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.pathsafe import sha256_file
from lib.receipts import find_generation
from tools.cost_tracker import IndeterminatePaidCallError, load_reservations, resume_check
from tools.graphics.seedream_image import SeedreamImage
from tools.video import _shared

from tests.tools._authored_film_helpers import make_verified_project, project_tracker, tiny_png_bytes

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "bytedance-seedream-v5-pro-edit.json").read_text()
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    return make_verified_project(tmp_path, monkeypatch, "proj-quill")


def _fake_download(url, dest, **kw):
    Path(dest).write_bytes(tiny_png_bytes())
    return {"bytes": 1, "content_type": "image/png"}


def test_registered_by_discovery():
    from tools.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover("tools.graphics")
    assert reg.get("seedream_image") is not None
    assert reg.get("title_card") is not None
    assert reg.get("poster_composite") is not None
    assert SeedreamImage.retry_policy.max_retries == 0


def test_edit_payload_matches_fixture_and_stores_content_addressed(env):
    ref = env / "assets" / "images" / "ref.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(tiny_png_bytes())
    captured = {}
    order = []

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        order.append("submit")
        captured["model_id"], captured["payload"] = model_id, payload
        return {"request_id": "req-sd"}

    def fake_upload(path):
        order.append("upload")
        return "https://v3.fal.media/up.png"

    with patch.object(_shared, "upload_image_fal", side_effect=fake_upload) as up, \
         patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), \
         patch.object(_shared, "fal_queue_wait", return_value={"images": [{"url": "https://v3.fal.media/o1.png"}, {"url": "https://v3.fal.media/o2.png"}]}), \
         patch.object(_shared, "fal_download", side_effect=_fake_download):
        result = SeedreamImage().execute({
            "prompt": "turnaround sheet",
            "operation": "edit",
            "reference_image_paths": [str(ref)],
            "reference_image_urls": ["https://v3.fal.media/other.png"],
            "num_images": 2,
            "image_size": "square_hd",
            "project_dir": str(env),
        })
    assert result.success, result.error
    assert order == ["upload", "submit"]
    assert captured["model_id"] == FIXTURE["model_id"]
    assert set(captured["payload"]) <= set(FIXTURE["input"]["properties"])
    assert captured["payload"] == {
        "prompt": "turnaround sheet",
        "image_size": "square_hd",
        "num_images": 2,
        "output_format": "png",
        "image_urls": ["https://v3.fal.media/other.png", "https://v3.fal.media/up.png"],
    }
    up.assert_called_once_with(str(ref.resolve()))
    assert len(result.data["output_paths"]) == 2
    ids = result.data["asset_ids"]
    assert ids[0] == ids[1]
    stored = env / "canon" / "visual" / "objects" / f"{ids[0]}.png"
    assert stored.exists() and sha256_file(stored) == ids[0]
    assert not any((env / ".staging").iterdir())
    assert result.metadata["model_endpoint"] == FIXTURE["model_id"]
    assert result.metadata["provider_request_id"] == "req-sd"
    assert find_generation(env, ids[0])["provider_request_id"] == "req-sd"
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "completed" and res["endpoint"] == FIXTURE["model_id"]
    assert res["output_hint"] == {"kind": "image", "objects_dir": str(env / "canon" / "visual" / "objects")}
    assert res["reserved_usd"] == pytest.approx(0.0675 * 2 + 0.0045, abs=1e-4)


def test_text_to_image_uses_t2i_endpoint_without_image_urls(env):
    captured = {}

    def fake_submit(model_id, payload, *, api_key, timeout_s=30.0):
        captured["model_id"], captured["payload"] = model_id, payload
        return {"request_id": "req-t"}

    with patch.object(_shared, "fal_queue_submit", side_effect=fake_submit), \
         patch.object(_shared, "fal_queue_wait", return_value={"images": [{"url": "https://fal.media/o.png"}]}), \
         patch.object(_shared, "fal_download", side_effect=_fake_download):
        result = SeedreamImage().execute({"prompt": "p", "project_dir": str(env)})
    assert result.success, result.error
    assert captured["model_id"] == FIXTURE["text_to_image_model_id"]
    assert "image_urls" not in captured["payload"]
    assert captured["payload"]["image_size"] == "auto_2K"


def test_cost_estimate_matches_fixture_pricing():
    tool = SeedreamImage()
    pr = FIXTURE["pricing"]
    assert tool.estimate_cost({"num_images": 1}) == pytest.approx(pr["usd_per_image_le_2048"])
    assert tool.estimate_cost({"num_images": 3, "image_size": {"width": 1536, "height": 1024}}) == pytest.approx(pr["usd_per_image_le_1536"] * 3)
    assert tool.estimate_cost({"operation": "edit", "reference_image_urls": ["a"] * 4, "image_size": "auto_1K"}) == pytest.approx(
        pr["usd_per_image_le_1536"] + 3 * pr["usd_per_additional_input_image"])


def test_preflight_rejects_over_ten_refs_and_bad_counts(env):
    with patch.object(_shared, "fal_queue_submit") as submit, patch.object(_shared, "upload_image_fal") as up:
        r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(env),
                                     "reference_image_urls": [f"https://fal.media/{i}.png" for i in range(11)]})
        assert not r.success and "at most 10" in r.error
        r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(env)})
        assert not r.success and "at least one" in r.error
        r = SeedreamImage().execute({"prompt": "p", "num_images": 7, "project_dir": str(env)})
        assert not r.success and "num_images" in r.error
        r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(env),
                                     "reference_image_paths": [str(env.parent / "outside.png")]})
        assert not r.success and "preflight" in r.error
    submit.assert_not_called()
    up.assert_not_called()
    assert not (env / "cost-reservations.jsonl").exists()


def test_unregistered_or_unapproved_project_rejected(env, tmp_path, monkeypatch):
    stray = tmp_path.parent / f"{tmp_path.name}-stray"
    stray.mkdir()
    (stray / "project.yaml").write_bytes((env / "project.yaml").read_bytes())
    with patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedreamImage().execute({"prompt": "p", "project_dir": str(stray), "cost_tracker": object(), "budget_usd_cap": 99})
        assert not r.success and "registered project" in r.error
        unapproved = make_verified_project(tmp_path, monkeypatch, "proj-unbound")
        (unapproved / "project.yaml").write_text((unapproved / "project.yaml").read_text() + "# edited\n")
        r = SeedreamImage().execute({"prompt": "p", "project_dir": str(unapproved)})
        assert not r.success and "approval receipt" in r.error
    submit.assert_not_called()


def test_egress_classes_enforced_before_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    project = make_verified_project(tmp_path, monkeypatch, "proj-hermit", egress_classes=("prompts",))
    ref = project / "ref.png"
    ref.write_bytes(tiny_png_bytes())
    with patch.object(_shared, "upload_image_fal") as up, patch.object(_shared, "fal_queue_submit") as submit:
        r = SeedreamImage().execute({"prompt": "p", "operation": "edit", "project_dir": str(project),
                                     "reference_image_paths": [str(ref)]})
    assert not r.success and "reference_images" in r.error
    up.assert_not_called()
    submit.assert_not_called()
    assert not (project / "cost-reservations.jsonl").exists()


def test_submit_crash_is_indeterminate_and_wait_failure_is_pending_billing(env, tmp_path, monkeypatch):
    with patch.object(_shared, "fal_queue_submit", side_effect=ConnectionError("gone")):
        r = SeedreamImage().execute({"prompt": "p", "project_dir": str(env)})
    assert not r.success
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)

    env2 = make_verified_project(tmp_path, monkeypatch, "proj-quill-2")
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-f"}), \
         patch.object(_shared, "fal_queue_wait", side_effect=_shared.FalQueueError("failed")):
        r = SeedreamImage().execute({"prompt": "p", "project_dir": str(env2)})
    assert not r.success
    res = list(load_reservations(env2).values())[0]
    assert res["state"] == "pending_billing" and res["provider_request_id"] == "req-f"
    assert project_tracker(env2).budget_spent_usd == pytest.approx(res["reserved_usd"], abs=1e-4)
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env2)


def test_attach_failure_leaves_submitting(env):
    with patch.object(_shared, "fal_queue_submit", return_value={"request_id": "req-lost"}), \
         patch("tools.cost_tracker.attach_request_id", side_effect=OSError("disk full")), \
         patch.object(_shared, "fal_queue_wait") as wait:
        r = SeedreamImage().execute({"prompt": "p", "project_dir": str(env)})
    assert not r.success and "disk full" in r.error
    wait.assert_not_called()
    res = list(load_reservations(env).values())[0]
    assert res["state"] == "submitting" and res["provider_request_id"] is None
    with pytest.raises(IndeterminatePaidCallError):
        resume_check(env)

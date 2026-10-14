#!/usr/bin/env python3
"""Probe B: photoreal reference pair through Kling o3 reference-to-video on FAL.
Governed: verified config, egress check, reservation before submit, no-retry, hardened
download, ffprobe. Probe only — no canon receipt (nothing here becomes canon)."""
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.env_loader import load_env  # noqa: E402
from lib import pathsafe  # noqa: E402
from tools.video import _shared  # noqa: E402
from tools.cost_tracker import attach_request_id, reconcile_paid_call, reserve_paid_call  # noqa: E402
from tools.base_tool import normalized_inputs_hash  # noqa: E402

MODEL = "fal-ai/kling-video/o3/pro/reference-to-video"
PROJECT = REPO / "projects" / "smoke-visual-bible"
HERO = "16dffd68983fa62ce445f0cb7941b77397e9e3ba3b65694d45dec954e0db622c.png"
VIEW = "ba8371963f9742f287371543eb3c922e079024c7cfcef4849eae64157cd96878.png"

def main():
    load_env()
    import os
    api_key = os.environ["FAL_KEY"]
    inputs = {"project_dir": str(PROJECT), "prompt": "@Image1 and @Image2 show the same lighthouse keeper. He turns his head slowly toward the camera, wind in his beard, grey studio background, 5 seconds, no dialogue.", "duration": "5"}
    root, tracker, config = _shared.paid_call_context(inputs)
    config.require_egress("fal", "prompts", "reference_images")
    refs = [pathsafe.resolve_input(str(root / "canon/visual/objects" / n), root) for n in (HERO, VIEW)]
    out_dir = root / "assets" / "smoke"; out_dir.mkdir(parents=True, exist_ok=True)
    output_path = pathsafe.validate_output_parent(str(out_dir / "probe_b_kling.mp4"), root)
    estimate = 5 * 0.112
    rid = reserve_paid_call(tracker, root, tool="probe_b_kling", endpoint=MODEL, normalized_inputs_hash=normalized_inputs_hash(inputs), reserved_usd=estimate, output_hint={"kind": "video", "output_path": str(output_path), "generate_audio": False})
    image_urls = [_shared.upload_image_fal(str(p)) for p in refs]
    payload = {"prompt": inputs["prompt"], "image_urls": image_urls, "duration": "5", "aspect_ratio": "16:9", "generate_audio": False}
    state, actual = "failed", 0.0
    try:
        sub = _shared.fal_queue_submit(MODEL, payload, api_key=api_key)
        request_id = str(sub["request_id"]); attach_request_id(root, rid, request_id)
        state, actual = "pending_billing", estimate
        print("submitted", request_id)
        data = _shared.fal_queue_wait(MODEL, request_id, api_key=api_key, deadline_s=900, poll_s=5)
        staging = pathsafe.staging_file(root, ".mp4")
        _shared.fal_download(data["video"]["url"], staging, max_bytes=200_000_000, allowed_mime_prefixes=("video/", "application/octet-stream"))
        _shared.verify_video_file(staging, require_audio=False)
        pathsafe.atomic_move(staging, output_path) if hasattr(pathsafe, "atomic_move") else staging.replace(output_path)
        state = "completed"
        print("video:", output_path)
    finally:
        reconcile_paid_call(root, rid, actual, state, tracker)
        print("reservation", rid, state, actual)

if __name__ == "__main__":
    main()

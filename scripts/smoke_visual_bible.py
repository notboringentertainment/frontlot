#!/usr/bin/env python3
"""Paid smoke test for the visual_bible toolchain (PLAN §0). Spends real money.

Runs, in order, against projects/smoke-visual-bible (cap $5, config must already be
approved via scripts/gate_approve.py):
  1. seedream text_to_image  — one 1K hero portrait of an invented character (~$0.07)
  2. seedream edit           — one derived view using (1) as reference (~$0.07)
  3. seedance 2.5 reference_to_video — one 4s 480p clip with (1) and (2) as references (~$0.90)
Prints each generation receipt id and the reservation ledger. Aborts on the first error.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.env_loader import load_env  # noqa: E402
from lib.project_config import load_verified_project_config  # noqa: E402
from tools.graphics.seedream_image import SeedreamImage  # noqa: E402
from tools.video.seedance_video import SeedanceVideo  # noqa: E402

TOOLS = {"seedream_image": SeedreamImage, "seedance_video": SeedanceVideo}

def get_tool(name):
    return TOOLS[name]()

PROJECT = REPO / "projects" / "smoke-visual-bible"

def run(tool_name, inputs):
    tool = get_tool(tool_name)
    res = tool.execute(inputs)
    if not res.success:
        sys.exit(f"{tool_name} failed: {res.error}")
    print(f"[{tool_name}] {res.output_path}  cost=${res.cost_usd:.3f}  receipt={res.metadata.get('generation_receipt_ids') or res.metadata.get('generation_receipt_id')}")
    return res

def main():
    load_env()
    cfg = load_verified_project_config(PROJECT)
    print(f"config ok: cap ${cfg.budget_usd_cap}, egress {sorted(cfg.egress_classes)}")
    out = PROJECT / "assets" / "smoke"; out.mkdir(parents=True, exist_ok=True)
    hero = run("seedream_image", {"operation": "text_to_image", "prompt": "studio portrait of a weathered lighthouse keeper in a wool coat, neutral grey background, soft key light, photographic", "image_size": "auto_1K", "num_images": 1, "output_format": "png", "output_dir": str(out), "project_dir": str(PROJECT)})
    view = run("seedream_image", {"operation": "edit", "prompt": "same person, three-quarter view, same coat and lighting, neutral grey background", "reference_image_paths": [hero.output_path], "image_size": "auto_1K", "num_images": 1, "output_format": "png", "output_dir": str(out), "project_dir": str(PROJECT)})
    clip = run("seedance_video", {"operation": "reference_to_video", "model_version": "2.5", "prompt": "@Image1 and @Image2 are the same lighthouse keeper. He turns his head slowly toward the camera, wind in his hair, 4 seconds, no dialogue.", "reference_image_paths": [hero.output_path, view.output_path], "resolution": "480p", "duration": "4", "aspect_ratio": "16:9", "generate_audio": False, "output_path": str(out / "smoke.mp4"), "project_dir": str(PROJECT)})
    print(json.dumps({"hero": hero.output_path, "view": view.output_path, "clip": clip.output_path}, indent=2))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate Ace Handler's character sheet views through the governed Seedream path.
Each call: prompt_recipe from the ratified look, look_refs + headshot_ref verified, the
approved hero as the only reference, receipts sealed. Spends real money (~$0.07/view)."""
from __future__ import annotations
import sys, json
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(REPO))
from lib.env_loader import load_env
from lib import look_ingest as li, headshots as hs
from tools import prompt_builder as pb
from tools.graphics.seedream_image import SeedreamImage

PROJECT = REPO / "projects" / "bloodless"
PALETTE = ["clinical white", "nitrile blue", "sodium orange"]  # treatment c1, The Locked Frame
ROLES = sys.argv[1:] or ["front"]

def main():
    load_env()
    active = li.active_look_for(PROJECT, "character", "ace-handler")
    heads = hs.active_headshots(PROJECT); ah = heads["ace-handler"] if isinstance(heads, dict) else [x for x in heads if x.entity_id == "ace-handler"][0]
    hero_path = PROJECT / "canon" / "visual" / "objects" / f"{ah.asset_id}.png"
    receipt_id = getattr(ah, "approval_receipt_id", None) or getattr(ah, "receipt_id")
    out_dir = PROJECT / "assets" / "visual-bible" / "ace-handler"; out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for role in ROLES:
        built = pb.build_prompt(active.payload, role=role, palette=PALETTE)
        inputs = {
            "operation": "edit", "prompt": built["prompt"], "prompt_recipe": built["prompt_recipe"],
            "asset_role": role, "stage": "visual_bible",
            "look_refs": [{"entity_kind": "character", "entity_id": "ace-handler", "look_hash": active.look_hash}],
            "headshot_ref": {"entity_id": "ace-handler", "asset_id": ah.asset_id, "approval_receipt_id": receipt_id},
            "reference_image_paths": [str(hero_path)],
            "reference_manifest": [{"asset_id": ah.asset_id, "path": str(hero_path), "role": "hero", "visual_bible_entity_id": "ace-handler"}],
            "palette": PALETTE, "image_size": "auto_1K", "num_images": 1, "output_format": "png",
            "output_dir": str(out_dir), "project_dir": str(PROJECT),
        }
        r = SeedreamImage().execute(inputs)
        if not r.success:
            print(f"[{role}] FAILED: {r.error}"); sys.exit(1)
        path = r.data.get("output_path"); results[role] = path
        print(f"[{role}] {path}  cost=${r.cost_usd:.3f}")
    print(json.dumps(results, indent=1))

if __name__ == "__main__":
    main()

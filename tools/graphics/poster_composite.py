"""Local poster compositor: key art + title card → ``poster_final`` PNG (PLAN §10, inspection #11).

Purely local (PIL), deterministic, receipted. The title card (an RGBA PNG from
the ``title_card`` tool) is scaled to ``title_scale`` × key-art width with a
fixed resampling filter and alpha-composited onto the key art at
``(title_x, title_y)`` (fractions of the key-art size, centre-anchored). Same
inputs → same bytes → same content-addressed ``asset_id``.

Both inputs must resolve (strict, no symlinks) inside the project root and
each must carry a verified generation receipt (``lib.receipts.find_generation``)
— an unreceipted image is refused, so compositing cannot launder an import. The
output goes staging → deterministic PNG re-encode → ``<objects_dir>/<sha256>.png``
(default ``<project_dir>/canon/visual/objects``). Cost is 0; the receipt is
written with ``generator_kind="local"``, ``local_tool="poster_composite"``, a
``parameters_hash`` over the layout inputs plus both input sha256s, and
``input_asset_ids=[key_art_sha256, title_card_sha256]`` so enforcement can
prove ``poster_final`` derives from the approved key art and title card.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)


class PosterComposite(BaseTool):
    name = "poster_composite"
    version = "1.0"
    tier = ToolTier.GENERATE
    capability = "poster_composite"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:PIL"]
    install_instructions = "pip install Pillow"

    capabilities = ["poster_composite", "image_composite"]
    supports = {"alpha_composite": True, "custom_position": True, "custom_scale": True}
    best_for = ["poster_final: approved key art + locally rendered title card"]
    not_good_for = ["generating imagery", "rendering text (use title_card)"]

    LOCAL_TOOL_VERSION = "1.0"
    DEFAULT_OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"

    input_schema = {
        "type": "object",
        "required": ["key_art_path", "title_card_path", "project_dir"],
        "properties": {
            "key_art_path": {"type": "string", "description": "Approved key art PNG inside the project root."},
            "title_card_path": {"type": "string", "description": "title_card output PNG (RGBA) inside the project root."},
            "title_x": {"type": "number", "default": 0.5, "description": "Title centre, fraction of key-art width (0..1)."},
            "title_y": {"type": "number", "default": 0.85, "description": "Title centre, fraction of key-art height (0..1)."},
            "title_scale": {"type": "number", "default": 0.8, "description": "Title width as a fraction of key-art width (0 < s <= 1)."},
            "project_dir": {"type": "string"},
            "objects_dir": {"type": "string", "description": "Default <project_dir>/canon/visual/objects."},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100)
    idempotency_key_fields = ["key_art_path", "title_card_path", "title_x", "title_y", "title_scale"]
    side_effects = ["writes PNG object under project_dir"]
    user_visible_verification = ["Check title placement, legibility and that key art is unaltered"]
    emits_generation_receipt = True

    @staticmethod
    def _params(inputs: dict[str, Any]) -> dict[str, Any]:
        params = {
            "title_x": float(inputs.get("title_x", 0.5)),
            "title_y": float(inputs.get("title_y", 0.85)),
            "title_scale": float(inputs.get("title_scale", 0.8)),
        }
        for key in ("title_x", "title_y"):
            if not 0.0 <= params[key] <= 1.0:
                raise ValueError(f"{key} must be within 0..1")
        if not 0.0 < params["title_scale"] <= 1.0:
            raise ValueError("title_scale must be within (0, 1]")
        return params

    @staticmethod
    def _render(key_art: Path, title_card: Path, params: dict[str, Any]) -> bytes:
        import io

        from PIL import Image

        with Image.open(key_art) as ka:
            ka.load()
            base = ka.convert("RGBA")
        with Image.open(title_card) as tc:
            tc.load()
            title = tc.convert("RGBA")
        target_w = max(1, int(round(base.width * params["title_scale"])))
        target_h = max(1, int(round(title.height * target_w / max(title.width, 1))))
        title = title.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)
        cx = int(round(base.width * params["title_x"]))
        cy = int(round(base.height * params["title_y"]))
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        layer.paste(title, (cx - target_w // 2, cy - target_h // 2), title)
        out = Image.alpha_composite(base, layer)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from lib import pathsafe
        from lib.canonical_json import record_sha256
        from lib.receipts import find_generation
        from lib.state_io import atomic_write_bytes

        start = time.time()
        try:
            if not inputs.get("project_dir"):
                raise ValueError("poster_composite requires inputs['project_dir']")
            project_root = Path(inputs["project_dir"]).resolve()
            if not project_root.is_dir():
                raise ValueError(f"project_dir does not exist: {project_root}")
            params = self._params(inputs)
            key_art = pathsafe.resolve_input(str(inputs["key_art_path"]), project_root)
            title_card = pathsafe.resolve_input(str(inputs["title_card_path"]), project_root)
            key_art_sha, title_card_sha = pathsafe.sha256_file(key_art), pathsafe.sha256_file(title_card)
            # Local derivation launders nothing (Codex R2 #7): both inputs must
            # already be receipted (signed + ledgered) pipeline outputs.
            for label, path, sha in (("key_art", key_art, key_art_sha), ("title_card", title_card, title_card_sha)):
                if find_generation(project_root, sha) is None:
                    raise ValueError(
                        f"{label} {path} (sha256 {sha}) has no verified generation receipt — "
                        f"only receipted pipeline outputs may be composited; an imported image is refused"
                    )
            objects_dir = (
                pathsafe.validate_output_parent(Path(inputs["objects_dir"]) / "x", project_root).parent
                if inputs.get("objects_dir")
                else project_root / self.DEFAULT_OBJECTS_SUBDIR
            )
            png = self._render(key_art, title_card, params)
            staging = pathsafe.staging_file(project_root, ".png")
            atomic_write_bytes(staging, png)
            asset_id, final_path = pathsafe.store_content_addressed(staging, objects_dir, ".png")
        except Exception as exc:
            return ToolResult(success=False, error=f"poster_composite failed: {exc}")

        parameters_hash = record_sha256(
            {**params, "key_art_sha256": key_art_sha, "title_card_sha256": title_card_sha}
        )
        return ToolResult(
            success=True,
            data={
                "provider": "openmontage",
                "asset_id": asset_id,
                "output_path": str(final_path),
                "output": str(final_path),
                "key_art_asset_id": key_art_sha,
                "title_card_asset_id": title_card_sha,
            },
            artifacts=[str(final_path)],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            metadata={
                "generator_kind": "local",
                "local_tool": "poster_composite",
                "local_tool_version": self.LOCAL_TOOL_VERSION,
                "parameters_hash": parameters_hash,
                "input_asset_ids": [key_art_sha, title_card_sha],
            },
        )

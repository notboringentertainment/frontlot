"""Local title-card renderer: exact text → transparent PNG via PIL (PLAN §9).

No image model ever touches typography. Text is rendered from a licensed font
file with fixed parameters, so the same inputs yield the same bytes and the
same content-addressed ``asset_id``. Output goes staging → deterministic PNG
re-encode → ``<objects_dir>/<sha256>.png`` (default
``<project_dir>/canon/visual/objects``).

``font_path`` must resolve (strict, no symlinks) either inside the project
root or inside the repo's ``assets/fonts/`` directory when that exists;
anything else is rejected. Cost is 0; the receipt is written with
``generator_kind="local"``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lib.paths import REPO_ROOT
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

REPO_FONTS_DIR = REPO_ROOT / "assets" / "fonts"
ALIGNMENTS = ("left", "center", "right")


class TitleCard(BaseTool):
    name = "title_card"
    version = "1.0"
    tier = ToolTier.GENERATE
    capability = "title_card"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:PIL"]
    install_instructions = "pip install Pillow"

    capabilities = ["title_card", "text_render", "typography"]
    supports = {"transparent_background": True, "multiline": True, "custom_size": True}
    best_for = ["exact on-screen text: titles, cards, credits, lower thirds"]
    not_good_for = ["illustration", "photoreal imagery"]

    LOCAL_TOOL_VERSION = "1.0"
    DEFAULT_OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"

    input_schema = {
        "type": "object",
        "required": ["text", "font_path", "project_dir"],
        "properties": {
            "text": {"type": "string", "description": "Rendered verbatim; newlines make lines."},
            "font_path": {"type": "string", "description": "TTF/OTF inside the project root or assets/fonts/."},
            "font_size": {"type": "integer", "default": 96},
            "color": {"type": "string", "default": "#FFFFFF", "description": "Any PIL color string."},
            "width": {"type": "integer", "default": 1920},
            "height": {"type": "integer", "default": 1080},
            "alignment": {"type": "string", "enum": list(ALIGNMENTS), "default": "center"},
            "line_spacing": {"type": "integer", "default": 8},
            "project_dir": {"type": "string"},
            "objects_dir": {"type": "string", "description": "Default <project_dir>/canon/visual/objects."},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50)
    idempotency_key_fields = ["text", "font_size", "color", "width", "height", "alignment", "line_spacing"]
    side_effects = ["writes PNG object under project_dir"]
    user_visible_verification = ["Check spelling and kerning of the rendered text"]
    emits_generation_receipt = True

    # ---- helpers ----

    @staticmethod
    def _params(inputs: dict[str, Any]) -> dict[str, Any]:
        """The rendering parameters that fully determine the output pixels."""
        return {
            "text": str(inputs["text"]),
            "font_size": int(inputs.get("font_size", 96)),
            "color": str(inputs.get("color", "#FFFFFF")),
            "width": int(inputs.get("width", 1920)),
            "height": int(inputs.get("height", 1080)),
            "alignment": str(inputs.get("alignment", "center")),
            "line_spacing": int(inputs.get("line_spacing", 8)),
        }

    @staticmethod
    def _resolve_font(font_path: str, project_root: Path) -> Path:
        from lib import pathsafe

        errors: list[str] = []
        try:
            return pathsafe.resolve_input(font_path, project_root)
        except pathsafe.PathSafetyError as exc:
            errors.append(str(exc))
        if REPO_FONTS_DIR.is_dir():
            try:
                return pathsafe.resolve_input(font_path, REPO_FONTS_DIR)
            except pathsafe.PathSafetyError as exc:
                errors.append(str(exc))
        raise ValueError(
            f"font_path must live inside the project root or {REPO_FONTS_DIR}: " + "; ".join(errors)
        )

    def _render(self, params: dict[str, Any], font_file: Path) -> bytes:
        import io

        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(str(font_file), params["font_size"])
        image = Image.new("RGBA", (params["width"], params["height"]), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        align = params["alignment"]
        anchor_x = {"left": "l", "center": "m", "right": "r"}[align]
        x = {"left": 0, "center": params["width"] / 2, "right": params["width"]}[align]
        draw.multiline_text(
            (x, params["height"] / 2),
            params["text"],
            font=font,
            fill=params["color"],
            anchor=f"{anchor_x}m",
            align=align,
            spacing=params["line_spacing"],
        )
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    # ---- execute ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from lib import pathsafe
        from lib.canonical_json import record_sha256
        from lib.state_io import atomic_write_bytes

        start = time.time()
        try:
            if not inputs.get("project_dir"):
                raise ValueError("title_card requires inputs['project_dir']")
            project_root = Path(inputs["project_dir"]).resolve()
            if not project_root.is_dir():
                raise ValueError(f"project_dir does not exist: {project_root}")
            params = self._params(inputs)
            if params["alignment"] not in ALIGNMENTS:
                raise ValueError(f"alignment must be one of {ALIGNMENTS}")
            if not params["text"]:
                raise ValueError("text must be non-empty")
            font_file = self._resolve_font(str(inputs["font_path"]), project_root)
            objects_dir = (
                pathsafe.validate_output_parent(Path(inputs["objects_dir"]) / "x", project_root).parent
                if inputs.get("objects_dir")
                else project_root / self.DEFAULT_OBJECTS_SUBDIR
            )
            png = self._render(params, font_file)
            staging = pathsafe.staging_file(project_root, ".png")
            atomic_write_bytes(staging, png)
            asset_id, final_path = pathsafe.store_content_addressed(staging, objects_dir, ".png")
        except Exception as exc:
            return ToolResult(success=False, error=f"title_card failed: {exc}")

        parameters_hash = record_sha256({**params, "font_sha256": pathsafe.sha256_file(font_file)})
        return ToolResult(
            success=True,
            data={
                "provider": "openmontage",
                "asset_id": asset_id,
                "output_path": str(final_path),
                "output": str(final_path),
                "width": params["width"],
                "height": params["height"],
                "text": params["text"],
            },
            artifacts=[str(final_path)],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            metadata={
                "generator_kind": "local",
                "local_tool": "title_card",
                "local_tool_version": self.LOCAL_TOOL_VERSION,
                "parameters_hash": parameters_hash,
                "input_asset_ids": [],
            },
        )

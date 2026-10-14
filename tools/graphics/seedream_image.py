"""Seedream 5 Pro (ByteDance) image generation and multi-reference edit via fal.ai.

Fixture: ``tests/fixtures/providers/bytedance-seedream-v5-pro-edit.json``.

- ``text_to_image`` → ``bytedance/seedream/v5/pro/text-to-image``
- ``edit``          → ``bytedance/seedream/v5/pro/edit`` with 1..10 ``image_urls``

Every call is a paid call: a reservation is persisted before submit, the
provider request id is attached right after submit, and reconciliation runs
in ``finally`` (a failure between submit and attach is left ``submitting`` —
indeterminate — for human reconciliation). Outputs are staged under the
project, deterministically re-encoded as PNG and stored content-addressed
under ``<project_root>/canon/visual/objects/<sha256>.png`` (``objects_dir``
overrides, still inside the project). This tool is the sheet generator; Flux
Kontext (single reference) is the weaker fallback.

Trust boundary (inspection #2/#3/#5): the project root comes only from
``lib.events.infer_project_dir`` and must be a registered project; the budget
cap and egress consent come only from the human-approved ``project.yaml``
(``prompts`` before any submit, ``reference_images`` before any upload); a
failure after the provider accepted the job reconciles ``pending_billing``
with the reservation retained.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

IMAGE_SIZE_ENUM = [
    "square_hd", "square", "portrait_4_3", "portrait_16_9",
    "landscape_4_3", "landscape_16_9", "auto_1K", "auto_2K",
]
# Longest-edge pixels per preset, used only to pick the pricing tier.
_PRESET_LONG_EDGE = {
    "square": 512, "square_hd": 1024, "portrait_4_3": 1024, "portrait_16_9": 1024,
    "landscape_4_3": 1024, "landscape_16_9": 1024, "auto_1K": 1024, "auto_2K": 2048,
}


class SeedreamImage(BaseTool):
    name = "seedream_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "seedream"
    governance_bound = True  # governed boundary runs before any upload (inspection #9)
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # FAL_KEY checked dynamically
    install_instructions = (
        "Set FAL_KEY to your fal.ai API key.\n"
        "  Get one at https://fal.ai/dashboard/keys"
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["generate_image", "text_to_image", "image_edit", "multi_reference_edit"]
    supports = {
        "reference_image": True,
        "multiple_reference_images": True,
        "custom_size": True,
        "seed": False,
    }
    best_for = [
        "character / location sheets conditioned on up to 10 reference images",
        "identity-consistent edits across a visual bible",
        "high-resolution (2K) stills",
    ]
    not_good_for = ["typography (use title_card)", "offline generation", "seeded reproducibility"]
    fallback_tools = ["flux_image"]

    MODEL_IDS = {
        "text_to_image": "bytedance/seedream/v5/pro/text-to-image",
        "edit": "bytedance/seedream/v5/pro/edit",
    }
    MAX_REFERENCE_IMAGES = 10
    MAX_NUM_IMAGES = 6
    USD_PER_IMAGE_LE_1536 = 0.0675
    USD_PER_IMAGE_LE_2048 = 0.135
    USD_PER_ADDITIONAL_INPUT_IMAGE = 0.0045
    MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
    DEFAULT_DEADLINE_S = 600.0
    DEFAULT_OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"

    input_schema = {
        "type": "object",
        "required": ["prompt", "project_dir"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {"type": "string", "enum": ["text_to_image", "edit"], "default": "text_to_image"},
            "project_dir": {"type": "string", "description": "Project root; reservations, staging, objects and receipts live here."},
            "objects_dir": {"type": "string", "description": "Content-addressed store (default <project_dir>/canon/visual/objects)."},
            "reference_image_paths": {
                "type": "array", "items": {"type": "string"},
                "description": "edit only: 1..10 local references inside the project, uploaded to fal storage.",
            },
            "reference_image_urls": {
                "type": "array", "items": {"type": "string"},
                "description": "edit only: 1..10 already-hosted reference URLs.",
            },
            "image_size": {
                "description": "Preset name or {width, height}.",
                "oneOf": [
                    {"type": "string", "enum": IMAGE_SIZE_ENUM},
                    {"type": "object", "required": ["width", "height"],
                     "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}}},
                ],
                "default": "auto_2K",
            },
            "num_images": {"type": "integer", "minimum": 1, "maximum": 6, "default": 1},
            "output_format": {"type": "string", "enum": ["jpeg", "png"], "default": "png"},
            "reference_manifest": {
                "type": "array", "items": {"type": "object"},
                "description": "Optional {asset_id, path, role, visual_bible_entity_id} per reference_image_paths entry, "
                               "same order; when omitted the tool records {asset_id, path, role: reference} itself.",
            },
            "look_refs": {
                "type": "array", "items": {"type": "object"},
                "description": "Governed calls: [{entity_kind, entity_id, look_hash}] verified against active look_lock "
                               "receipts before upload and bound into the receipt. Omit only for a shot_id whose "
                               "approved scene_plan record is entity_free.",
            },
            "stage": {"type": "string", "description": "Pipeline stage making the call; visual_bible calls can never be entity-free and require prompt_recipe."},
            "asset_role": {
                "type": "string",
                "enum": ["hero", "turnaround", "front", "three_quarter", "profile", "full_body", "expressions", "wardrobe", "establishing", "detail", "time_variant", "key_art"],
                "description": "Sheet roles (turnaround, expressions, wardrobe, front, three_quarter, profile, full_body) require headshot_ref.",
            },
            "headshot_ref": {"type": "object", "description": "{entity_id, asset_id, approval_receipt_id} of the approved hero (lib.headshots.verify_headshot_ref)."},
            "prompt_recipe": {"type": "object", "description": "tools.prompt_builder recipe; prompt must hash to rendered_sha256."},
            "asset_class": {
                "type": "string",
                "description": "'storyboard_frame' for a pipeline frame, or 'supervised_shot' for a saved supervised "
                               "shot brief. A supervised shot requires shot_id and uses its cumulative dollar "
                               "allowance; image calls never consume a video take.",
            },
            "shot_id": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True)
    retry_policy = RetryPolicy(max_retries=0)  # paid + no client idempotency key: never auto-retry
    idempotency_key_fields = ["prompt", "operation", "image_size", "num_images", "reference_image_urls"]
    side_effects = ["writes PNG objects under project_dir", "calls fal.ai API"]
    user_visible_verification = ["Inspect generated image(s) for identity match against the references"]
    emits_generation_receipt = True

    def _get_api_key(self) -> str | None:
        return os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    # ---- cost ----

    @classmethod
    def _long_edge(cls, image_size: Any) -> int:
        if isinstance(image_size, dict):
            return max(int(image_size.get("width", 0)), int(image_size.get("height", 0)))
        return _PRESET_LONG_EDGE.get(str(image_size), 2048)

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        n = int(inputs.get("num_images", 1))
        per_image = (
            self.USD_PER_IMAGE_LE_1536
            if self._long_edge(inputs.get("image_size", "auto_2K")) <= 1536
            else self.USD_PER_IMAGE_LE_2048
        )
        refs = self._reference_count(inputs)
        extra = max(refs - 1, 0) * self.USD_PER_ADDITIONAL_INPUT_IMAGE
        return round(per_image * n + extra, 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 30.0 * int(inputs.get("num_images", 1))

    # ---- preflight ----

    @staticmethod
    def _reference_count(inputs: dict[str, Any]) -> int:
        return len(inputs.get("reference_image_paths") or []) + len(inputs.get("reference_image_urls") or [])

    def _preflight_error(self, inputs: dict[str, Any]) -> str | None:
        operation = inputs.get("operation", "text_to_image")
        if operation not in self.MODEL_IDS:
            return f"unknown operation {operation!r}"
        refs = self._reference_count(inputs)
        if refs > self.MAX_REFERENCE_IMAGES:
            return f"Seedream edit accepts at most {self.MAX_REFERENCE_IMAGES} reference images; got {refs}"
        if operation == "edit" and refs == 0:
            return "Seedream edit requires at least one reference image"
        if operation == "text_to_image" and refs:
            return "text_to_image takes no reference images; use operation='edit'"
        n = int(inputs.get("num_images", 1))
        if not 1 <= n <= self.MAX_NUM_IMAGES:
            return f"num_images must be 1..{self.MAX_NUM_IMAGES}; got {n}"
        size = inputs.get("image_size", "auto_2K")
        if isinstance(size, dict):
            if not (size.get("width") and size.get("height")):
                return "custom image_size needs width and height"
        elif size not in IMAGE_SIZE_ENUM:
            return f"image_size must be one of {IMAGE_SIZE_ENUM} or {{width, height}}"
        if inputs.get("output_format", "png") not in ("jpeg", "png"):
            return "output_format must be 'jpeg' or 'png'"
        return None

    def _build_payload(self, inputs: dict[str, Any], image_urls: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": inputs["prompt"],
            "image_size": inputs.get("image_size", "auto_2K"),
            "num_images": int(inputs.get("num_images", 1)),
            "output_format": inputs.get("output_format", "png"),
        }
        if inputs.get("operation", "text_to_image") == "edit":
            payload["image_urls"] = list(image_urls)
        return payload

    # ---- execute ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="No fal.ai API key found. " + self.install_instructions)

        from lib import pathsafe, receipts
        from tools.base_tool import current_execution_id, normalized_inputs_hash
        from tools.cost_tracker import attach_request_id, reconcile_paid_call, reserve_paid_call
        from tools.video import _shared

        start = time.time()
        started_at = _shared.utc_now_iso()
        error = self._preflight_error(inputs)
        if error:
            return ToolResult(success=False, error=error)
        operation = inputs.get("operation", "text_to_image")
        model_id = self.MODEL_IDS[operation]

        governance: dict[str, Any] = {}
        try:
            # Look governance (look_refs / headshot_ref / prompt_recipe) and
            # the per-shot allowance (C2) are both verified inside
            # paid_call_context, before any upload; the allowance check needs
            # this call's own estimate to measure the shot's dollars.
            estimate = self.estimate_cost(inputs)
            project_root, tracker, config = _shared.paid_call_context(
                inputs, governance=governance, estimated_usd=estimate,
            )
            config.require_egress("fal", "prompts")
            local_refs = [pathsafe.resolve_input(p, project_root) for p in inputs.get("reference_image_paths") or []]
            # Reference provenance (R3#3): remote URLs refused and every local
            # reference's receipted lineage verified before upload on governed calls.
            _shared.verify_reference_lineage(inputs, project_root, local_refs, governed=governance.get("governed", False))
            references_applied = self._references_applied(inputs, project_root, local_refs)
            if local_refs:
                config.require_egress("fal", "prompts", "reference_images")
            objects_dir = (
                pathsafe.validate_output_parent(Path(inputs["objects_dir"]) / "x", project_root).parent
                if inputs.get("objects_dir")
                else project_root / self.DEFAULT_OBJECTS_SUBDIR
            )
            image_urls = list(inputs.get("reference_image_urls") or [])
            for resolved in local_refs:
                image_urls.append(_shared.upload_image_fal(str(resolved)))
            payload = self._build_payload(inputs, image_urls)
            reservation_id = reserve_paid_call(
                tracker,
                project_root,
                tool=self.name,
                endpoint=model_id,
                normalized_inputs_hash=normalized_inputs_hash(inputs),
                reserved_usd=estimate,
                output_hint={"kind": "image", "objects_dir": str(objects_dir)},
                inputs=inputs,
                kind="image",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Seedream preflight failed: {exc}")

        request_id: str | None = None
        request_id_persisted = False
        completion_started = False
        state = "failed"
        actual_usd = 0.0
        asset_ids: list[str] = []
        output_paths: list[str] = []
        execution_id = current_execution_id() or f"seedream-{reservation_id}"
        # D19.5: the caller (scripts/sheet_run.py) records the signed
        # attempt_started row here — after the reservation exists, before any
        # provider submission — so every candidate counts. A refusal (cap hit)
        # settles the reservation as failed: nothing was submitted.
        try:
            _shared.run_pre_submit(project_root, reservation_id)
        except Exception as exc:
            reconcile_paid_call(project_root, reservation_id, 0.0, "failed", tracker)
            return ToolResult(success=False, error=f"Seedream pre-submit hook refused: {exc}")
        try:
            submitted = _shared.fal_queue_submit(model_id, payload, api_key=api_key)
            request_id = str(submitted["request_id"])
            attach_request_id(project_root, reservation_id, request_id)
            request_id_persisted = True
            # Provider accepted: any later failure keeps the reservation charged.
            state, actual_usd = "pending_billing", estimate
            data = _shared.fal_queue_wait(
                model_id,
                request_id,
                api_key=api_key,
                deadline_s=float(inputs.get("deadline_s", self.DEFAULT_DEADLINE_S)),
                poll_s=float(inputs.get("poll_s", 3.0)),
            )
            images = data.get("images") or []
            if not images:
                raise RuntimeError("provider returned no images")
            import hashlib

            staged: list[dict[str, Any]] = []
            try:
                for item in images:
                    staging = pathsafe.staging_file(project_root, ".img")
                    entry = {"staging_path": staging}
                    staged.append(entry)  # registered first so a failed download is cleaned up
                    _shared.fal_download(
                        item["url"],
                        staging,
                        max_bytes=self.MAX_DOWNLOAD_BYTES,
                        allowed_mime_prefixes=("image/",),
                    )
                    # The content address is the hash of the deterministic PNG
                    # re-encode (what store_content_addressed will produce).
                    asset_id = hashlib.sha256(pathsafe.reencode_png_bytes(staging)).hexdigest()
                    entry.update({"output_path": objects_dir / f"{asset_id}.png", "output_sha256": asset_id})
                # Crash-safe completion (Codex R2 #5): WAL before the moves, then
                # receipt → ledger → terminal reservation → WAL delete.
                receipts.stage_generation_wal(
                    project_root,
                    execution_id=execution_id,
                    reservation_id=reservation_id,
                    actual_usd=actual_usd,
                    outputs=staged,
                    receipt={
                        "tool": self.name,
                        "generator_kind": "model",
                        "model_endpoint": model_id,
                        "provider_request_id": request_id,
                        "normalized_inputs_hash": normalized_inputs_hash(inputs),
                        "cost_usd": actual_usd,
                        "started_at": started_at,
                        "prompt": inputs["prompt"],
                        "seed": data.get("seed"),
                        "references_applied": references_applied,
                        **_shared.receipt_governance_fields(governance),
                    },
                )
                completion_started = True
                for entry in staged:
                    asset_id, final_path = pathsafe.store_content_addressed(entry["staging_path"], objects_dir, ".png")
                    if asset_id != entry["output_sha256"]:
                        raise RuntimeError(f"content address drift: staged {entry['output_sha256']}, stored {asset_id}")
                    asset_ids.append(asset_id)
                    output_paths.append(str(final_path))
            finally:
                if not completion_started:
                    for entry in staged:
                        try:
                            Path(entry["staging_path"]).unlink()
                        except FileNotFoundError:
                            pass
            receipts.complete_generation(project_root, execution_id, tracker=tracker)
            state = "completed"
        except Exception as exc:
            return ToolResult(success=False, error=f"Seedream generation failed: {exc}")
        finally:
            # No persisted request id (submit crashed, or attach_request_id
            # itself failed) is indeterminate: leave it `submitting`.
            # Once the WAL entry exists the reservation belongs to the replay
            # path (complete_generation / recover_generation_wal), not to us.
            if request_id_persisted and not completion_started:
                reconcile_paid_call(project_root, reservation_id, actual_usd, state, tracker)

        return ToolResult(
            success=True,
            data={
                "provider": "seedream",
                "model": model_id,
                "operation": operation,
                "prompt": inputs["prompt"],
                "asset_ids": asset_ids,
                "output_paths": output_paths,
                "output_path": output_paths[0],
                "output": output_paths[0],
                "reservation_id": reservation_id,
                "seed": data.get("seed"),
                "references_applied": references_applied,
                "look_refs": governance.get("look_refs"),
                "headshot_ref": governance.get("headshot_ref"),
                "prompt_recipe": governance.get("prompt_recipe"),
            },
            artifacts=output_paths,
            cost_usd=actual_usd,
            duration_seconds=round(time.time() - start, 2),
            seed=data.get("seed"),
            model=model_id,
            metadata={
                "model_endpoint": model_id,
                "provider_request_id": request_id,
                "generator_kind": "model",
                "prompt": inputs["prompt"],
                "seed": data.get("seed"),
                "references_applied": references_applied,
                **_shared.receipt_governance_fields(governance),
            },
        )

    @staticmethod
    def _references_applied(inputs: dict[str, Any], project_root: Path, local_refs: list[Path]) -> list[dict[str, Any]]:
        """The exact reference list this call uploads, recorded in the signed
        receipt (Slice A step 7). With a ``reference_manifest`` the shared
        hash-binding applies; otherwise each local file is recorded as
        ``{asset_id: sha256, path: project-relative, role: reference}``. Remote
        URLs (legacy projects only) are recorded as ``role: remote_url`` with
        the URL's sha256 as asset_id so the receipt never claims a local root."""
        import hashlib

        from lib import pathsafe
        from tools.video import _shared

        # Same order as image_urls: already-hosted URLs first, then local uploads.
        applied = [
            {"asset_id": hashlib.sha256(str(url).encode("utf-8")).hexdigest(), "role": "remote_url"}
            for url in inputs.get("reference_image_urls") or []
        ]
        if inputs.get("reference_manifest"):
            applied.extend(_shared.bind_reference_manifest(inputs, project_root, local_refs))
        else:
            applied.extend(
                {
                    "asset_id": pathsafe.sha256_file(p),
                    "path": p.relative_to(project_root).as_posix(),
                    "role": "reference",
                }
                for p in local_refs
            )
        return applied

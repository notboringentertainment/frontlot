"""Seedance (ByteDance) video generation via fal.ai API.

Best for cinematic clips with native audio, director-level camera control,
and lip-sync from quoted dialogue in prompts.

``model_version`` selects the endpoint family:

- ``"2.0"`` — the original code path (text/image/reference-to-video), kept
  byte-for-byte in behavior.
- ``"2.5"`` (default) — ``bytedance/seedance-2.5/reference-to-video`` per
  ``tests/fixtures/providers/bytedance-seedance-2.5-reference-to-video.json``:
  prompt-addressable references (``@Image1`` ...), up to 30 images / 10 videos /
  10 audios, 50 files total, NO start-frame parameter. Every 2.5 call is a
  paid call routed through the FAL queue helpers with a persisted reservation
  (reserve → submit → attach request id → wait → download → reconcile).

Trust boundary (inspection #2/#3/#5/#6/#15/#16): the project root comes only
from ``lib.events.infer_project_dir`` and must be a registered project; the
budget cap and provider-egress consent come only from the human-approved
``project.yaml``; a ``shot_visual`` take needs a signed storyboard receipt
for its ``shot_id``/frame before reservation AND the approved frame file
(re-hashed) is what gets uploaded, packed last; every local reference is
bound by hash to a caller-supplied ``reference_manifest`` and the uploaded
list is sealed into the generation receipt; no retries on any paid path; a
failure after the provider accepted the job is reconciled ``pending_billing``
(reserved amount retained) and only ``scripts/reconcile_paid_calls.py`` may
settle it; every downloaded file must pass ffprobe before it is moved.
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


class SeedanceVideo(BaseTool):
    name = "seedance_video"
    version = "0.2.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "seedance"
    governance_bound = True  # governed boundary runs before any upload (inspection #9)
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set FAL_KEY to your fal.ai API key.\n"
        "  Get one at https://fal.ai/dashboard/keys"
    )
    agent_skills = ["seedance-2-0", "ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "reference_to_video": True,
        "multiple_reference_images": True,
        "reference_image": True,
        "native_audio": True,
        "cinematic_quality": True,
        "camera_direction": True,
        "lip_sync": True,
        "multi_shot": True,
        "aspect_ratio": True,
        "seed": True,
    }
    best_for = [
        "preferred premium video gen when FAL_KEY is available",
        "cinematic trailers, teasers, and high-fidelity clips with native synchronized audio",
        "director-level camera control and multi-shot editing in a single generation",
        "lip-sync from quoted dialogue in prompts",
        "reference-conditioned generation (up to 9 images + 3 video clips + 3 audio clips)",
        "consistent character identity across shots",
    ]
    not_good_for = ["offline generation", "budget-constrained projects"]
    fallback_tools = ["veo_video", "kling_video", "minimax_video"]
    # Premium model — beat out "experimental stability" baseline. The scoring
    # engine reads quality_score directly when present (see lib/scoring.py).
    quality_score = 0.95

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video", "reference_to_video"],
                "default": "text_to_video",
            },
            "model_version": {
                "type": "string",
                "enum": ["2.0", "2.5"],
                "default": "2.5",
                "description": (
                    "2.5 = bytedance/seedance-2.5/reference-to-video (references only, "
                    "no start frame; needs project_dir + budget). 2.0 = legacy endpoints."
                ),
            },
            "project_dir": {
                "type": "string",
                "description": "Project root (required for 2.5): reservations, staging, receipts live here.",
            },
            "asset_class": {
                "type": "string",
                "description": (
                    "2.5 only. 'shot_visual' (every shot take the asset director generates) requires "
                    "shot_id + storyboard_frame_sha256 and a signed storyboard approval covering them."
                ),
            },
            "shot_id": {"type": "string", "description": "Required when asset_class == 'shot_visual'."},
            "storyboard_frame_sha256": {
                "type": "string",
                "description": "sha256 (asset_id) of the approved storyboard frame for shot_id; required for shot_visual.",
            },
            "storyboard_frame_path": {
                "type": "string",
                "description": (
                    "shot_visual only. Project-local path of the approved storyboard frame file. Optional: "
                    "when omitted the frame is located at <project>/canon/visual/objects/<sha>.png. Either "
                    "way the file is re-hashed and must equal storyboard_frame_sha256; it is uploaded and "
                    "packed LAST in image_urls."
                ),
            },
            "reference_manifest": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "2.5 only; required whenever reference_image_paths is non-empty. One object per local "
                    "reference, in the same order: {asset_id, path, role, visual_bible_entity_id}. Each "
                    "path is re-hashed before upload and must equal its asset_id (a mismatch fails "
                    "preflight). The storyboard frame is NOT listed here — the tool appends it itself. "
                    "The list the tool actually uploaded is returned in metadata.references_applied and "
                    "sealed into the generation receipt."
                ),
            },
            "bitrate_mode": {
                "type": "string",
                "enum": ["standard", "high"],
                "default": "standard",
                "description": "2.5 only.",
            },
            "model_variant": {
                "type": "string",
                "enum": ["standard", "fast"],
                "default": "standard",
                "description": "standard = highest quality, fast = lower latency and cost",
            },
            "duration": {
                "type": "string",
                "enum": ["auto", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"],
                "default": "5",
                "description": "Duration in seconds. 'auto' lets the model decide.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                "default": "16:9",
            },
            "resolution": {
                "type": "string",
                "enum": ["480p", "720p", "1080p"],
                "default": "720p",
                "description": "1080p is 2.5 only.",
            },
            "generate_audio": {
                "type": "boolean",
                "default": True,
                "description": "Generate synchronized audio (speech, SFX, ambient)",
            },
            "image_url": {
                "type": "string",
                "description": "Start frame image URL for image_to_video (jpg, png, webp)",
            },
            "image_path": {
                "type": "string",
                "description": "Local start-frame path for image_to_video. Auto-uploaded to fal.ai storage.",
            },
            "end_image_url": {
                "type": "string",
                "description": "Optional end frame URL for image_to_video",
            },
            "reference_image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 9 reference image URLs for reference_to_video (identity / wardrobe / setting / style anchors).",
            },
            "reference_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local reference image paths for reference_to_video. Auto-uploaded to fal.ai storage.",
            },
            "reference_video_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 3 reference video clip URLs for reference_to_video (motion / camera / pacing anchors).",
            },
            "reference_audio_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 3 reference audio clip URLs for reference_to_video (voice / music / ambience anchors).",
            },
            "seed": {
                "type": "integer",
                "description": "Optional seed for reproducibility",
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True
    )
    # Paid provider calls are never retried (Act 3 no-retry decision): a retry
    # can double-bill. Both 2.0 and 2.5 also send X-Fal-No-Retry via the queue helpers.
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = ["prompt", "model_variant", "operation", "duration", "seed"]
    side_effects = ["writes video file to output_path", "calls fal.ai API"]
    emits_generation_receipt = True
    user_visible_verification = [
        "Watch generated clip for motion coherence, audio sync, and visual quality"
    ]

    def _get_api_key(self) -> str | None:
        return os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY")

    def get_status(self) -> ToolStatus:
        if self._get_api_key():
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        if str(inputs.get("model_version", "2.5")) == "2.5":
            return self._estimate_cost_v25(inputs)
        variant = inputs.get("model_variant", "standard")
        duration = inputs.get("duration", "5")
        secs = 5 if duration == "auto" else int(duration)
        rate = 0.2419 if variant == "fast" else 0.3034
        return round(rate * secs, 2)

    # ---- Seedance 2.5 (fixture-verified) ----

    V25_MODEL_ID = "bytedance/seedance-2.5/reference-to-video"
    V25_LIMITS = {"images": 30, "videos": 10, "audios": 10, "total": 50}
    # pricing.usd_per_second from the fixture; 1080p is token-billed (not
    # per-second) so it is approximated at 2x the 720p rate for reservation.
    V25_USD_PER_SECOND = {"480p": 0.2205, "720p": 0.4730, "1080p": 0.4730 * 2}
    V25_VIDEO_INPUT_MULTIPLIER = 0.6
    V25_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
    V25_DEFAULT_DEADLINE_S = 900.0

    def _estimate_cost_v25(self, inputs: dict[str, Any]) -> float:
        duration = str(inputs.get("duration", "5"))
        secs = 5 if duration == "auto" else int(duration)
        rate = self.V25_USD_PER_SECOND.get(str(inputs.get("resolution", "720p")), 0.4730)
        cost = rate * secs
        if inputs.get("reference_video_urls") or inputs.get("reference_video_paths"):
            cost *= self.V25_VIDEO_INPUT_MULTIPLIER
        return round(cost, 4)

    def _v25_reference_counts(self, inputs: dict[str, Any]) -> dict[str, int]:
        images = len(inputs.get("reference_image_urls") or []) + len(inputs.get("reference_image_paths") or [])
        if inputs.get("asset_class") == "shot_visual":
            images += 1  # the approved storyboard frame is packed last
        videos = len(inputs.get("reference_video_urls") or [])
        audios = len(inputs.get("reference_audio_urls") or [])
        return {"images": images, "videos": videos, "audios": audios, "total": images + videos + audios}

    def _v25_preflight_error(self, inputs: dict[str, Any]) -> str | None:
        """Reject over-cap reference sets outright — never truncate silently."""
        operation = inputs.get("operation", "text_to_video")
        if operation == "image_to_video" or inputs.get("image_url") or inputs.get("image_path") or inputs.get("end_image_url"):
            return (
                "Seedance 2.5 has no start/end-frame parameter; use reference_image_* "
                "(prompt-addressable @ImageN) or model_version='2.0' for image_to_video"
            )
        counts = self._v25_reference_counts(inputs)
        for key, label in (("images", "reference images"), ("videos", "reference videos"), ("audios", "reference audio clips")):
            if counts[key] > self.V25_LIMITS[key]:
                return f"Seedance 2.5 accepts at most {self.V25_LIMITS[key]} {label}; got {counts[key]}"
        if counts["total"] > self.V25_LIMITS["total"]:
            return f"Seedance 2.5 accepts at most {self.V25_LIMITS['total']} reference files in total; got {counts['total']}"
        return None

    def _build_v25_payload(self, inputs: dict[str, Any], image_urls: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {"prompt": inputs["prompt"]}
        if image_urls:
            payload["image_urls"] = list(image_urls)
        if inputs.get("reference_video_urls"):
            payload["video_urls"] = list(inputs["reference_video_urls"])
        if inputs.get("reference_audio_urls"):
            payload["audio_urls"] = list(inputs["reference_audio_urls"])
        payload["resolution"] = str(inputs.get("resolution", "720p"))
        payload["duration"] = str(inputs.get("duration", "5"))
        payload["aspect_ratio"] = str(inputs.get("aspect_ratio", "16:9"))
        payload["generate_audio"] = bool(inputs.get("generate_audio", True))
        payload["bitrate_mode"] = str(inputs.get("bitrate_mode", "standard"))
        return payload

    STORYBOARD_OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"

    def _v25_storyboard_preflight(self, inputs: dict[str, Any], project_root: Path) -> Path | None:
        """Shared with kling_reference_video: see ``_shared.storyboard_preflight``."""
        from tools.video import _shared

        return _shared.storyboard_preflight(inputs, project_root, model_label="Seedance 2.5")

    @staticmethod
    def _v25_reference_manifest(
        inputs: dict[str, Any], project_root: Path, local_refs: list[Path]
    ) -> list[dict[str, Any]]:
        """Shared with kling_reference_video: see ``_shared.bind_reference_manifest``."""
        from tools.video import _shared

        return _shared.bind_reference_manifest(inputs, project_root, local_refs)

    def _execute_v25(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        from lib import pathsafe, receipts
        from lib.state_io import atomic_move
        from tools.base_tool import current_execution_id, normalized_inputs_hash
        from tools.cost_tracker import attach_request_id, reconcile_paid_call, reserve_paid_call
        from tools.video import _shared

        start = time.time()
        started_at = _shared.utc_now_iso()
        error = self._v25_preflight_error(inputs)
        if error:
            return ToolResult(success=False, error=error)
        if not inputs.get("output_path"):
            return ToolResult(success=False, error="Seedance 2.5 requires an explicit output_path under the project")

        governance: dict[str, Any] = {}
        try:
            # Look governance (look_refs verified before any upload) and the
            # per-shot allowance (C2) both run inside paid_call_context, which
            # needs this call's own estimate to measure the shot's dollars.
            estimate = self._estimate_cost_v25(inputs)
            project_root, tracker, config = _shared.paid_call_context(
                inputs, governance=governance, media="video", estimated_usd=estimate,
            )
            # Prompts always leave the machine; reference images only when consented.
            config.require_egress("fal", "prompts")
            local_refs = [pathsafe.resolve_input(p, project_root) for p in inputs.get("reference_image_paths") or []]
            frame = self._v25_storyboard_preflight(inputs, project_root)
            _shared.verify_reference_lineage(inputs, project_root, local_refs, governed=governance.get("governed", False))
            if local_refs or frame is not None:
                config.require_egress("fal", "prompts", "reference_images")
            references_applied = self._v25_reference_manifest(inputs, project_root, local_refs)
            output_path = pathsafe.validate_output_parent(inputs["output_path"], project_root)
            image_urls = list(inputs.get("reference_image_urls") or [])
            for resolved in local_refs:
                image_urls.append(_shared.upload_image_fal(str(resolved)))
            if frame is not None:
                # Packing policy (asset-director.md §2): the storyboard frame is LAST.
                image_urls.append(_shared.upload_image_fal(str(frame)))
                references_applied.append({
                    "asset_id": str(inputs["storyboard_frame_sha256"]),
                    "path": frame.relative_to(project_root).as_posix(),
                    "role": "storyboard",
                    "shot_id": str(inputs["shot_id"]),
                })
            payload = self._build_v25_payload(inputs, image_urls)
            reservation_id = reserve_paid_call(
                tracker,
                project_root,
                tool=self.name,
                endpoint=self.V25_MODEL_ID,
                normalized_inputs_hash=normalized_inputs_hash(inputs),
                reserved_usd=estimate,
                output_hint={"kind": "video", "output_path": str(output_path),
                             "generate_audio": bool(payload["generate_audio"])},
                inputs=inputs,
                kind="video",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Seedance 2.5 preflight failed: {exc}")

        request_id: str | None = None
        request_id_persisted = False
        completion_started = False
        state = "failed"
        actual_usd = 0.0
        staging: Path | None = None
        execution_id = current_execution_id() or f"seedance-{reservation_id}"
        try:
            submitted = _shared.fal_queue_submit(self.V25_MODEL_ID, payload, api_key=api_key)
            request_id = str(submitted["request_id"])
            attach_request_id(project_root, reservation_id, request_id)
            request_id_persisted = True
            # From here the provider has accepted (and may bill) the job: any
            # failure is pending_billing with the reservation retained.
            state, actual_usd = "pending_billing", estimate
            data = _shared.fal_queue_wait(
                self.V25_MODEL_ID,
                request_id,
                api_key=api_key,
                deadline_s=float(inputs.get("deadline_s", self.V25_DEFAULT_DEADLINE_S)),
                poll_s=float(inputs.get("poll_s", 5.0)),
            )
            video_url = data["video"]["url"]
            staging = pathsafe.staging_file(project_root, ".mp4")
            _shared.fal_download(
                video_url,
                staging,
                max_bytes=self.V25_MAX_DOWNLOAD_BYTES,
                allowed_mime_prefixes=("video/", "application/octet-stream"),
            )
            probed = _shared.verify_video_file(staging, require_audio=bool(payload["generate_audio"]))
            # Crash-safe completion (Codex R2 #5): WAL first, then move, then
            # receipt → ledger → terminal reservation → WAL delete. Anything
            # interrupted after this point is replayed by resume_check.
            receipts.stage_generation_wal(
                project_root,
                execution_id=execution_id,
                reservation_id=reservation_id,
                actual_usd=actual_usd,
                outputs=[{"staging_path": staging, "output_path": output_path,
                          "output_sha256": pathsafe.sha256_file(staging)}],
                receipt={
                    "tool": self.name,
                    "generator_kind": "model",
                    "model_endpoint": self.V25_MODEL_ID,
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
            atomic_move(staging, output_path)
            staging = None
            receipts.complete_generation(project_root, execution_id, tracker=tracker)
            state = "completed"
        except Exception as exc:
            if staging is not None and not completion_started:
                try:
                    staging.unlink()
                except FileNotFoundError:
                    pass
            return ToolResult(success=False, error=f"Seedance 2.5 video generation failed: {exc}")
        finally:
            # Submit-without-persisted-request-id is INDETERMINATE (the provider
            # may have accepted the job, or attach_request_id itself failed):
            # leave the reservation `submitting` so resume_check halts for the
            # human reconciler instead of marking it failed and inviting a resubmit.
            # Once the WAL entry exists the reservation belongs to the replay
            # path (complete_generation / recover_generation_wal), not to us.
            if request_id_persisted and not completion_started:
                reconcile_paid_call(project_root, reservation_id, actual_usd, state, tracker)

        return ToolResult(
            success=True,
            data={
                "provider": "seedance",
                "model": self.V25_MODEL_ID,
                "model_version": "2.5",
                "prompt": inputs["prompt"],
                "operation": "reference_to_video",
                "aspect_ratio": payload["aspect_ratio"],
                "resolution": payload["resolution"],
                "generate_audio": payload["generate_audio"],
                "seed": data.get("seed"),
                "output": str(output_path),
                "output_path": str(output_path),
                "format": "mp4",
                "reservation_id": reservation_id,
                **probed,
            },
            artifacts=[str(output_path)],
            cost_usd=actual_usd,
            duration_seconds=round(time.time() - start, 2),
            seed=data.get("seed"),
            model=self.V25_MODEL_ID,
            metadata={
                "model_endpoint": self.V25_MODEL_ID,
                "provider_request_id": request_id,
                "generator_kind": "model",
                "prompt": inputs["prompt"],
                "seed": data.get("seed"),
                "references_applied": references_applied,
                **_shared.receipt_governance_fields(governance),
            },
        )

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        variant = inputs.get("model_variant", "standard")
        return 60.0 if variant == "fast" else 120.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error="FAL_KEY not set. " + self.install_instructions,
            )

        if str(inputs.get("model_version", "2.5")) == "2.5":
            return self._execute_v25(inputs, api_key)

        # Seedance 2.0 (inspection #8): there is no ungoverned path. Project
        # resolution is required for every call, governance is decided from the
        # signed pipeline pin (lib.look_ingest.project_look_governed via
        # _shared) — never from a project.yaml-presence heuristic — and the
        # 2.0 endpoint is refused for every resolvable project. Nothing is
        # uploaded or submitted on this branch.
        from lib.events import infer_project_dir
        from tools.video import _shared

        project_root = infer_project_dir(inputs) if isinstance(inputs, dict) else None
        if project_root is None:
            return ToolResult(
                success=False,
                error=(
                    "Seedance 2.0 requires a resolvable registered project (inputs['project_dir']) — "
                    "there is no ungoverned generation path; use model_version '2.5' or kling_reference_video."
                ),
            )
        try:
            governed = _shared.project_look_governed(project_root)
        except Exception:  # noqa: BLE001 — an undecidable pin is treated as governed
            governed = True
        return ToolResult(
            success=False,
            error=(
                f"Seedance 2.0 is refused for project {Path(project_root).name!r} "
                f"({'look-governed' if governed else 'not look-governed'} per the signed pipeline pin): "
                f"use model_version '2.5' or kling_reference_video, which run through the governed "
                f"budget/egress/look/storyboard path."
            ),
        )

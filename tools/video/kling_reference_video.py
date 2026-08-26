"""Kling o3 pro reference-to-video via fal.ai — the authored-film default video endpoint.

Endpoint ``fal-ai/kling-video/o3/pro/reference-to-video`` per
``tests/fixtures/providers/fal-ai-kling-video-o3-pro-reference-to-video.json``
(verified 2026-08-25). It replaced Seedance 2.5 as the project default (PLAN.md
D4 revised) because Seedance on FAL rejects any recognisable human face as a
reference (``PLAN-REVIEW-LOG.md`` → "Probes"), while Kling accepted the same
pipeline-generated portraits and held identity (``scripts/probe_b_kling.py``).

Contract facts that shape this tool:

- References are prompt-addressable: ``image_urls`` → ``@Image1..@ImageN``,
  ``elements`` → ``@Element1..@ElementN`` (an element is ``{frontal_image_url,
  reference_image_urls[1..3], video_url?, voice_id?}``; only one element may
  carry a video).
- ``prompt`` XOR ``multi_prompt`` (list of ``{prompt, duration}``).
- Optional ``start_image_url`` / ``end_image_url`` exist, but the storyboard
  frame is still packed as the LAST ``@ImageN`` reference (D5: frames steer
  composition; a start frame is a separate, opt-in decision).
- Reference caps: the doc states "maximum 4 total (elements + reference
  images) when using video". The cap WITHOUT a video is **unconfirmed** by the
  doc, so this tool applies a conservative cap of 9 images/elements
  (``CONSERVATIVE_IMAGE_CAP``) until a live probe proves a higher number.
- Pricing: $0.112/s with audio off, $0.14/s with audio on. ``generate_audio``
  defaults to False (the endpoint default).

Governance mirrors ``tools/video/seedance_video.py`` (2.5 path) exactly and
reuses its helpers in ``tools/video/_shared``: registered project + verified
``project.yaml`` + ``resume_check`` (``paid_call_context``); egress consent
before any upload; ``reference_image_paths`` bound by hash to
``reference_manifest``; a ``shot_visual`` take needs a signed storyboard
receipt and the re-hashed approved frame is uploaded last; the reservation is
persisted ``submitting`` before ``fal_queue_submit`` (X-Fal-No-Retry) and the
request id attached on return; a failure after acceptance is
``pending_billing``; the download is host-allowlisted and ffprobed
(``require_audio == generate_audio``); completion is WAL → move → receipt →
ledger → terminal reservation. No retries on any paid path.
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


class KlingReferenceVideo(BaseTool):
    name = "kling_reference_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "kling_fal"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set FAL_KEY to your fal.ai API key.\n"
        "  Get one at https://fal.ai/dashboard/keys"
    )
    agent_skills = ["kling-o3-reference", "ai-video-gen"]

    capabilities = ["reference_to_video"]
    supports = {
        "reference_to_video": True,
        "multiple_reference_images": True,
        "reference_image": True,
        "native_audio": True,
        "aspect_ratio": True,
        "human_face_references": True,
        "start_frame": True,
    }
    best_for = [
        "authored-film default: identity-holding shots from approved character/location sheets",
        "reference-conditioned video where the references contain human faces (Seedance on FAL refuses these)",
        "prompt-addressable references (@ImageN) and element packs (@ElementN)",
    ]
    not_good_for = ["offline generation", "ungoverned quick tests (use kling_video)"]
    fallback_tools = ["seedance_video", "kling_video"]
    quality_score = 0.93

    MODEL_ID = "fal-ai/kling-video/o3/pro/reference-to-video"
    USD_PER_SECOND = {"audio_off": 0.112, "audio_on": 0.14}
    MAX_TOTAL_REFS_WITH_VIDEO = 4  # doc-confirmed
    CONSERVATIVE_IMAGE_CAP = 9     # doc does NOT confirm a no-video cap; conservative until probed
    MAX_ELEMENT_REFERENCES = 3
    MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
    DEFAULT_DEADLINE_S = 900.0
    STORYBOARD_OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"

    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Addresses references as @Image1..@ImageN / @Element1..@ElementN. XOR multi_prompt."},
            "multi_prompt": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of {prompt, duration '1'..'15'}; XOR prompt.",
            },
            "project_dir": {"type": "string", "description": "Registered project root: reservations, staging, receipts live here."},
            "asset_class": {
                "type": "string",
                "description": "'shot_visual' (every shot take) requires shot_id + storyboard_frame_sha256 and a signed storyboard approval.",
            },
            "shot_id": {"type": "string"},
            "storyboard_frame_sha256": {"type": "string"},
            "storyboard_frame_path": {"type": "string", "description": "Optional project-local path of the approved frame; located by hash under canon/visual/objects otherwise. Re-hashed; uploaded LAST as @ImageN."},
            "reference_image_paths": {"type": "array", "items": {"type": "string"}, "description": "Local references (project-local, re-hashed). Requires reference_manifest."},
            "reference_manifest": {
                "type": "array",
                "items": {"type": "object"},
                "description": "One {asset_id, path, role, visual_bible_entity_id} per reference_image_paths entry, same order. Required together.",
            },
            "reference_image_urls": {"type": "array", "items": {"type": "string"}, "description": "Already-hosted references (refused on shot_visual)."},
            "reference_form": {
                "type": "string",
                "enum": ["image_urls", "elements"],
                "default": "image_urls",
                "description": (
                    "image_urls (default): every reference is an @ImageN. elements: manifest entries are grouped by "
                    "visual_bible_entity_id into @ElementN packs (role 'hero' → frontal_image_url, other roles → "
                    "reference_image_urls, 1–3); the storyboard frame stays an @ImageN."
                ),
            },
            "reference_video_url": {"type": "string", "description": "elements form only: attached as video_url of @Element1 (one element may carry a video). Total refs then capped at 4."},
            "start_image_url": {"type": "string", "description": "Optional start frame (refused on shot_visual)."},
            "end_image_url": {"type": "string", "description": "Optional end frame (refused on shot_visual)."},
            "duration": {"type": "string", "enum": [str(i) for i in range(3, 16)], "default": "5"},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"], "default": "16:9"},
            "generate_audio": {"type": "boolean", "default": False},
            "shot_type": {"type": "string", "enum": ["customize", "intelligent"]},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True)
    # Paid provider calls are never retried: a retry can double-bill.
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = ["prompt", "multi_prompt", "duration", "aspect_ratio", "generate_audio"]
    side_effects = ["writes video file to output_path", "calls fal.ai API"]
    emits_generation_receipt = True
    user_visible_verification = [
        "Watch the clip: identity matches the approved hero/wardrobe views, one action, no rendered text"
    ]

    def _get_api_key(self) -> str | None:
        return os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    # ---- cost ----

    @staticmethod
    def _duration_seconds(inputs: dict[str, Any]) -> int:
        if inputs.get("multi_prompt"):
            return sum(int(seg.get("duration", 5)) for seg in inputs["multi_prompt"])
        return int(inputs.get("duration", "5"))

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        rate = self.USD_PER_SECOND["audio_on" if inputs.get("generate_audio", False) else "audio_off"]
        return round(rate * self._duration_seconds(inputs), 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 180.0

    # ---- preflight (no network) ----

    def _reference_counts(self, inputs: dict[str, Any]) -> dict[str, int]:
        manifest = inputs.get("reference_manifest") or []
        local = len(inputs.get("reference_image_paths") or [])
        urls = len(inputs.get("reference_image_urls") or [])
        frame = 1 if inputs.get("asset_class") == "shot_visual" else 0
        if inputs.get("reference_form") == "elements":
            entities = {str(m.get("visual_bible_entity_id")) for m in manifest if isinstance(m, dict)}
            elements = len(entities) if manifest else 0
            images = urls + frame
        else:
            elements = 0
            images = local + urls + frame
        videos = 1 if inputs.get("reference_video_url") else 0
        return {"images": images, "elements": elements, "videos": videos, "total": images + elements}

    def _preflight_error(self, inputs: dict[str, Any]) -> str | None:
        """Reject malformed or over-cap requests outright — never truncate silently."""
        has_prompt = bool(inputs.get("prompt"))
        has_multi = bool(inputs.get("multi_prompt"))
        if has_prompt == has_multi:
            return "Kling o3 requires exactly one of prompt or multi_prompt"
        if has_multi:
            for i, seg in enumerate(inputs["multi_prompt"]):
                if not isinstance(seg, dict) or not seg.get("prompt") or str(seg.get("duration", "")) not in {str(n) for n in range(1, 16)}:
                    return f"multi_prompt[{i}] must be {{prompt, duration '1'..'15'}}"
        if inputs.get("reference_video_url") and inputs.get("reference_form") != "elements":
            return "reference_video_url is only accepted in the elements form (it rides on @Element1)"
        counts = self._reference_counts(inputs)
        if counts["videos"]:
            if counts["total"] > self.MAX_TOTAL_REFS_WITH_VIDEO:
                return (
                    f"Kling o3 accepts at most {self.MAX_TOTAL_REFS_WITH_VIDEO} references in total "
                    f"(elements + images) when a video reference is present; got {counts['total']}"
                )
            if not counts["elements"]:
                return "reference_video_url needs at least one element to ride on (reference_manifest is empty)"
        elif counts["total"] > self.CONSERVATIVE_IMAGE_CAP:
            return (
                f"Kling o3 reference cap without video is unconfirmed; this tool allows at most "
                f"{self.CONSERVATIVE_IMAGE_CAP} references (elements + images); got {counts['total']}"
            )
        return None

    # ---- payload ----

    def _build_elements(self, references_applied: list[dict[str, Any]], uploaded: list[str],
                        video_url: str | None) -> list[dict[str, Any]]:
        """Group manifest entries by entity (first-appearance order) into element packs.

        ``hero`` role → ``frontal_image_url``; every other role → ``reference_image_urls``
        (1–3). An entity with only a hero uses it as its single reference too. The
        optional video rides on the first element (the contract allows one video).
        """
        groups: dict[str, dict[str, Any]] = {}
        for ref, url in zip(references_applied, uploaded):
            entity = str(ref["visual_bible_entity_id"])
            g = groups.setdefault(entity, {"frontal": None, "refs": []})
            if ref.get("role") == "hero":
                if g["frontal"] is not None:
                    raise ValueError(f"element for {entity!r} has two hero references; exactly one frontal is allowed")
                g["frontal"] = url
            else:
                g["refs"].append(url)
        elements: list[dict[str, Any]] = []
        for entity, g in groups.items():
            if g["frontal"] is None:
                raise ValueError(f"element for {entity!r} has no role 'hero' reference to use as frontal_image_url")
            refs = g["refs"] or [g["frontal"]]
            if len(refs) > self.MAX_ELEMENT_REFERENCES:
                raise ValueError(
                    f"element for {entity!r} has {len(refs)} reference images; the contract allows at most "
                    f"{self.MAX_ELEMENT_REFERENCES}"
                )
            element: dict[str, Any] = {"frontal_image_url": g["frontal"], "reference_image_urls": refs}
            if video_url and not elements:
                element["video_url"] = video_url
            elements.append(element)
        if video_url and not elements:
            raise ValueError("reference_video_url needs at least one element to ride on")
        return elements

    def _build_payload(self, inputs: dict[str, Any], image_urls: list[str],
                       elements: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if inputs.get("multi_prompt"):
            payload["multi_prompt"] = [{"prompt": str(s["prompt"]), "duration": str(s["duration"])} for s in inputs["multi_prompt"]]
        else:
            payload["prompt"] = inputs["prompt"]
        if inputs.get("start_image_url"):
            payload["start_image_url"] = inputs["start_image_url"]
        if inputs.get("end_image_url"):
            payload["end_image_url"] = inputs["end_image_url"]
        if image_urls:
            payload["image_urls"] = list(image_urls)
        if elements:
            payload["elements"] = elements
        if not inputs.get("multi_prompt"):
            payload["duration"] = str(inputs.get("duration", "5"))
        payload["aspect_ratio"] = str(inputs.get("aspect_ratio", "16:9"))
        payload["generate_audio"] = bool(inputs.get("generate_audio", False))
        if inputs.get("shot_type"):
            payload["shot_type"] = str(inputs["shot_type"])
        return payload

    # ---- execute ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="FAL_KEY not set. " + self.install_instructions)

        from lib import pathsafe, receipts
        from lib.state_io import atomic_move
        from tools.base_tool import current_execution_id, normalized_inputs_hash
        from tools.cost_tracker import attach_request_id, reconcile_paid_call, reserve_paid_call
        from tools.video import _shared

        start = time.time()
        started_at = _shared.utc_now_iso()
        error = self._preflight_error(inputs)
        if error:
            return ToolResult(success=False, error=error)
        if not inputs.get("output_path"):
            return ToolResult(success=False, error="Kling o3 requires an explicit output_path under the project")

        try:
            project_root, tracker, config = _shared.paid_call_context(inputs)
            # Prompts always leave the machine; reference images only when consented.
            config.require_egress("fal", "prompts")
            local_refs = [pathsafe.resolve_input(p, project_root) for p in inputs.get("reference_image_paths") or []]
            frame = _shared.storyboard_preflight(inputs, project_root, model_label="Kling o3")
            if local_refs or frame is not None:
                config.require_egress("fal", "prompts", "reference_images")
            references_applied = _shared.bind_reference_manifest(inputs, project_root, local_refs)
            output_path = pathsafe.validate_output_parent(inputs["output_path"], project_root)
            image_urls = list(inputs.get("reference_image_urls") or [])
            uploaded = [_shared.upload_image_fal(str(resolved)) for resolved in local_refs]
            elements: list[dict[str, Any]] = []
            if inputs.get("reference_form") == "elements":
                elements = self._build_elements(references_applied, uploaded, inputs.get("reference_video_url"))
            else:
                image_urls.extend(uploaded)
            if frame is not None:
                # Packing policy (asset-director.md §2): the storyboard frame is the LAST @ImageN.
                image_urls.append(_shared.upload_image_fal(str(frame)))
                references_applied.append({
                    "asset_id": str(inputs["storyboard_frame_sha256"]),
                    "path": frame.relative_to(project_root).as_posix(),
                    "role": "storyboard",
                    "shot_id": str(inputs["shot_id"]),
                })
            payload = self._build_payload(inputs, image_urls, elements)
            estimate = self.estimate_cost(inputs)
            reservation_id = reserve_paid_call(
                tracker,
                project_root,
                tool=self.name,
                endpoint=self.MODEL_ID,
                normalized_inputs_hash=normalized_inputs_hash(inputs),
                reserved_usd=estimate,
                output_hint={"kind": "video", "output_path": str(output_path),
                             "generate_audio": bool(payload["generate_audio"])},
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Kling o3 preflight failed: {exc}")

        request_id: str | None = None
        request_id_persisted = False
        completion_started = False
        state = "failed"
        actual_usd = 0.0
        staging: Path | None = None
        execution_id = current_execution_id() or f"kling-{reservation_id}"
        prompt_text = inputs.get("prompt") or " | ".join(s["prompt"] for s in inputs.get("multi_prompt") or [])
        try:
            submitted = _shared.fal_queue_submit(self.MODEL_ID, payload, api_key=api_key)
            request_id = str(submitted["request_id"])
            attach_request_id(project_root, reservation_id, request_id)
            request_id_persisted = True
            # From here the provider has accepted (and may bill) the job.
            state, actual_usd = "pending_billing", estimate
            data = _shared.fal_queue_wait(
                self.MODEL_ID,
                request_id,
                api_key=api_key,
                deadline_s=float(inputs.get("deadline_s", self.DEFAULT_DEADLINE_S)),
                poll_s=float(inputs.get("poll_s", 5.0)),
            )
            video_url = data["video"]["url"]
            staging = pathsafe.staging_file(project_root, ".mp4")
            _shared.fal_download(
                video_url,
                staging,
                max_bytes=self.MAX_DOWNLOAD_BYTES,
                allowed_mime_prefixes=("video/", "application/octet-stream"),
            )
            probed = _shared.verify_video_file(staging, require_audio=bool(payload["generate_audio"]))
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
                    "model_endpoint": self.MODEL_ID,
                    "provider_request_id": request_id,
                    "normalized_inputs_hash": normalized_inputs_hash(inputs),
                    "cost_usd": actual_usd,
                    "started_at": started_at,
                    "prompt": prompt_text,
                    "seed": data.get("seed"),
                    "references_applied": references_applied,
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
            return ToolResult(success=False, error=f"Kling o3 video generation failed: {exc}")
        finally:
            # Submit without a persisted request id is INDETERMINATE: leave the
            # reservation `submitting` for the human reconciler. Once the WAL
            # exists, completion belongs to the replay path.
            if request_id_persisted and not completion_started:
                reconcile_paid_call(project_root, reservation_id, actual_usd, state, tracker)

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": self.MODEL_ID,
                "prompt": prompt_text,
                "operation": "reference_to_video",
                "aspect_ratio": payload["aspect_ratio"],
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
            model=self.MODEL_ID,
            metadata={
                "model_endpoint": self.MODEL_ID,
                "provider_request_id": request_id,
                "generator_kind": "model",
                "prompt": prompt_text,
                "seed": data.get("seed"),
                "references_applied": references_applied,
            },
        )

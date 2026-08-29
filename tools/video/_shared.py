"""Shared helpers for provider-specific video generation tools."""

from __future__ import annotations

from contextlib import contextmanager

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.base_tool import ToolResult, ToolStatus


HEYGEN_PROVIDERS = {
    "veo_3_1": {"name": "Google VEO 3.1", "quality": "highest", "speed": "slow"},
    "veo_3_1_fast": {"name": "Google VEO 3.1 Fast", "quality": "high", "speed": "medium"},
    "veo3": {"name": "Google VEO 3", "quality": "high", "speed": "slow"},
    "veo3_fast": {"name": "Google VEO 3 Fast", "quality": "high", "speed": "medium"},
    "veo2": {"name": "Google VEO 2", "quality": "medium", "speed": "medium"},
    "kling_pro": {"name": "Kling Pro", "quality": "high", "speed": "medium"},
    "kling_v2": {"name": "Kling v2", "quality": "medium", "speed": "fast"},
    "sora_v2": {"name": "Sora v2", "quality": "high", "speed": "slow"},
    "sora_v2_pro": {"name": "Sora v2 Pro", "quality": "highest", "speed": "slow"},
    "runway_gen4": {"name": "Runway Gen-4", "quality": "high", "speed": "medium"},
    # NOTE: HeyGen's `seedance_lite` / `seedance_pro` provider strings map to
    # Seedance 1.x. Seedance 2.0 on HeyGen is exposed through Video Agent and
    # Avatar Shots endpoints, NOT via the workflow provider parameter. For 2.0
    # access today, use `seedance_video` (fal.ai) or `seedance_replicate`.
    "seedance_lite": {"name": "Seedance Lite (1.x)", "quality": "medium", "speed": "fast"},
    "seedance_pro": {"name": "Seedance Pro (1.x)", "quality": "high", "speed": "medium"},
    "ltx_distilled": {"name": "LTX Distilled", "quality": "low", "speed": "fastest"},
}

WAN_VARIANTS = {
    "wan2.1-1.3b": {
        "name": "Wan 2.1 (1.3B)",
        "hf_id": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "hf_i2v_id": "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers",
        "pipeline_class": "WanPipeline",
        "vram_mb": 8000,
        "quality": "high",
        "speed": "medium",
        "t2v": True,
        "i2v": True,
        "license": "Apache-2.0",
        "default_width": 832,
        "default_height": 480,
        "default_num_frames": 81,
        "fps": 16,
    },
    "wan2.1-14b": {
        "name": "Wan 2.1 (14B)",
        "hf_id": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
        "hf_i2v_id": "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers",
        "pipeline_class": "WanPipeline",
        "vram_mb": 24000,
        "quality": "highest",
        "speed": "slow",
        "t2v": True,
        "i2v": True,
        "license": "Apache-2.0",
        "default_width": 1280,
        "default_height": 720,
        "default_num_frames": 81,
        "fps": 16,
    },
}

HUNYUAN_VARIANTS = {
    "hunyuan-1.5": {
        "name": "HunyuanVideo 1.5",
        "hf_id": "tencent/HunyuanVideo-1.5",
        "pipeline_class": "HunyuanVideoPipeline",
        "vram_mb": 14000,
        "quality": "high",
        "speed": "medium",
        "t2v": True,
        "i2v": True,
        "license": "Apache-2.0",
        "default_width": 848,
        "default_height": 480,
        "default_num_frames": 121,
        "fps": 24,
    },
}

LTX_LOCAL_VARIANTS = {
    "ltx2-local": {
        "name": "LTX-2 (Local)",
        "hf_id": "Lightricks/LTX-2",
        "pipeline_class": "LTXPipeline",
        "vram_mb": 12000,
        "quality": "high",
        "speed": "medium",
        "t2v": True,
        "i2v": True,
        "license": "LTX-2-Community",
        "default_width": 768,
        "default_height": 512,
        "default_num_frames": 121,
        "fps": 30,
    },
}

COGVIDEO_VARIANTS = {
    "cogvideo-5b": {
        "name": "CogVideoX 1.5 (5B)",
        "hf_id": "THUDM/CogVideoX-5b",
        "pipeline_class": "CogVideoXPipeline",
        "vram_mb": 12000,
        "quality": "medium",
        "speed": "medium",
        "t2v": True,
        "i2v": True,
        "license": "Apache-2.0",
        "default_width": 720,
        "default_height": 480,
        "default_num_frames": 49,
        "fps": 8,
    },
    "cogvideo-2b": {
        "name": "CogVideoX (2B)",
        "hf_id": "THUDM/CogVideoX-2b",
        "pipeline_class": "CogVideoXPipeline",
        "vram_mb": 6000,
        "quality": "medium",
        "speed": "fast",
        "t2v": True,
        "i2v": False,
        "license": "Apache-2.0",
        "default_width": 720,
        "default_height": 480,
        "default_num_frames": 49,
        "fps": 8,
    },
}

LTX2_FRAME_COUNTS = {
    "1s": 25,
    "2s": 49,
    "3s": 73,
    "4s": 97,
    "5s": 121,
    "6.7s": 161,
    "8s": 193,
}


def get_torch_device() -> str:
    """Return best available torch device: cuda > mps (Apple Silicon Metal) > cpu.

    Priority order:
      1. cuda  — NVIDIA GPU (fastest for most diffusion models)
      2. mps   — Apple Silicon Metal (M1/M2/M3/M4/M5, macOS >= 12.3)
      3. cpu   — fallback, always available but slow

    MPS detection is guarded for torch builds that lack ``torch.backends.mps``
    (e.g. older pip wheels or Linux builds).  We check both build-time support
    (``is_built()``) and runtime availability (``is_available()``).
    """
    try:
        import torch as _torch  # noqa: PLC0415
    except ImportError:
        return "cpu"
    if _torch.cuda.is_available():
        return "cuda"
    # Guard: torch.backends.mps may not exist on older/non-macOS builds
    try:
        mps_backend = getattr(_torch, "backends", None)
        mps_backend = getattr(mps_backend, "mps", None) if mps_backend else None
        if mps_backend is not None:
            # Check build-time support first, then runtime availability
            is_built = getattr(mps_backend, "is_built", lambda: True)()
            is_available = getattr(mps_backend, "is_available", lambda: False)()
            if is_built and is_available:
                return "mps"
    except Exception:
        pass
    return "cpu"


def local_generation_enabled() -> bool:
    return os.environ.get("VIDEO_GEN_LOCAL_ENABLED", "").lower() in {"true", "1", "yes"}


def local_generation_status() -> ToolStatus:
    if not local_generation_enabled():
        return ToolStatus.UNAVAILABLE
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return ToolStatus.UNAVAILABLE
    return ToolStatus.AVAILABLE


def local_install_instructions() -> str:
    return (
        "Enable local video generation and install the diffusers stack:\n"
        "  export VIDEO_GEN_LOCAL_ENABLED=true\n"
        "  uv pip install diffusers transformers accelerate torch pillow requests\n"
        "\n"
        "GPU support — pick what matches your hardware:\n"
        "  NVIDIA CUDA    — works out of the box with the above\n"
        "  Apple Silicon (MPS, macOS >= 12.3) — works out of the box; no extra build\n"
        "  CPU fallback   — slow but functional on any machine\n"
        "\n"
        "VRAM profile: see the selected tool's resource_profile for minimum VRAM."
    )


def estimate_quality_cost(quality: str) -> float:
    if quality == "highest":
        return 0.50
    if quality == "high":
        return 0.35
    if quality == "low":
        return 0.15
    return 0.20


def estimate_speed_runtime(speed: str) -> float:
    return {"fastest": 30.0, "fast": 60.0, "medium": 120.0, "slow": 300.0}.get(speed, 120.0)


def estimate_local_runtime(speed: str) -> float:
    return {"fast": 120.0, "medium": 240.0, "slow": 600.0}.get(speed, 240.0)


def load_diffusers_pipeline(pipeline_class: str, model_id: str, enable_offload: bool):
    import diffusers
    import torch

    pipeline_map = {
        "WanPipeline": "WanPipeline",
        "HunyuanVideoPipeline": "HunyuanVideoPipeline",
        "LTXPipeline": "LTXPipeline",
        "CogVideoXPipeline": "CogVideoXPipeline",
    }
    pipeline_name = pipeline_map.get(pipeline_class, pipeline_class)
    pipeline_class_obj = getattr(diffusers, pipeline_name)

    device = get_torch_device()
    # bfloat16 is only reliable on CUDA; MPS uses float16 for inference,
    # CPU must use float32 (float16 is emulated and unreliable on CPU)
    if device == "cuda" and torch.cuda.is_bf16_supported():
        dtype = torch.bfloat16
    elif device == "cpu":
        dtype = torch.float32
    else:
        dtype = torch.float16

    pipeline = pipeline_class_obj.from_pretrained(model_id, torch_dtype=dtype)

    if enable_offload:
        if device == "cuda":
            pipeline.enable_model_cpu_offload()
        else:
            # enable_model_cpu_offload() is CUDA-only; fall back to direct device placement
            pipeline = pipeline.to(device)
    else:
        pipeline = pipeline.to(device)

    if hasattr(pipeline, "enable_attention_slicing"):
        pipeline.enable_attention_slicing()

    if hasattr(pipeline, "vae") and pipeline.vae is not None:
        if hasattr(pipeline.vae, "enable_tiling"):
            pipeline.vae.enable_tiling()
        if hasattr(pipeline.vae, "enable_slicing"):
            pipeline.vae.enable_slicing()
    return pipeline


def load_reference_image(inputs: dict[str, Any], width: int, height: int):
    from io import BytesIO

    import requests
    from PIL import Image

    ref_path = inputs.get("reference_image_path")
    ref_url = inputs.get("reference_image_url")

    if ref_path:
        image = Image.open(ref_path).convert("RGB")
    elif ref_url:
        response = requests.get(ref_url, timeout=60)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content)).convert("RGB")
    else:
        return ToolResult(
            success=False,
            error="image_to_video requires reference_image_url or reference_image_path",
        )

    return image.resize((width, height), Image.LANCZOS)


def generate_local_video(
    *,
    tool_name: str,
    variants: dict[str, dict[str, Any]],
    default_variant: str,
    inputs: dict[str, Any],
) -> ToolResult:
    import torch
    from diffusers.utils import export_to_video

    variant = inputs.get("model_variant", default_variant)
    if variant not in variants:
        return ToolResult(
            success=False,
            error=f"Unknown model_variant: {variant}. Available: {', '.join(sorted(variants))}",
        )

    meta = variants[variant]
    prompt = inputs["prompt"]
    operation = inputs.get("operation", "text_to_video")
    seed = inputs.get("seed")
    enable_offload = inputs.get("enable_model_offload", True)

    if operation == "image_to_video" and not meta.get("i2v"):
        return ToolResult(
            success=False,
            error=f"{meta['name']} does not support image_to_video.",
        )

    width = inputs.get("width", meta["default_width"])
    height = inputs.get("height", meta["default_height"])
    num_frames = inputs.get("num_frames", meta["default_num_frames"])
    fps = meta["fps"]
    model_id = meta.get("hf_i2v_id") if operation == "image_to_video" and meta.get("hf_i2v_id") else meta["hf_id"]
    pipeline = load_diffusers_pipeline(meta["pipeline_class"], model_id, enable_offload)

    generation_args: dict[str, Any] = {
        "prompt": prompt,
        "num_frames": num_frames,
        "width": width,
        "height": height,
        "num_inference_steps": inputs.get("num_inference_steps", 30),
    }
    if seed is not None:
        generation_args["generator"] = torch.Generator(device="cpu").manual_seed(seed)
    if operation == "image_to_video":
        image = load_reference_image(inputs, width, height)
        if isinstance(image, ToolResult):
            return image
        generation_args["image"] = image
    if meta["pipeline_class"] == "CogVideoXPipeline":
        generation_args["negative_prompt"] = "worst quality, low quality, blurry, distorted, watermark"

    output = pipeline(**generation_args)
    frames = output.frames[0] if hasattr(output, "frames") else output.images

    output_path = Path(inputs.get("output_path", f"{tool_name}_{variant}.mp4"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(frames, str(output_path), fps=fps)

    return ToolResult(
        success=True,
        data={
            "provider": tool_name,
            "model_variant": variant,
            "provider_name": meta["name"],
            "mode": "local",
            "prompt": prompt,
            "model_id": model_id,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "fps": fps,
            "duration_seconds": round(num_frames / fps, 2),
            "operation": operation,
            "output": str(output_path),
            "format": "mp4",
            "license": meta["license"],
            **probe_output(output_path),
        },
        artifacts=[str(output_path)],
        seed=seed,
        model=model_id,
    )


def poll_heygen(execution_id: str, api_key: str, timeout: int = 600) -> str:
    import requests

    headers = {"X-Api-Key": api_key}
    url = f"https://api.heygen.com/v1/workflows/executions/{execution_id}"
    deadline = time.time() + timeout
    interval = 5.0

    while time.time() < deadline:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", {})
        status = data.get("status", "")

        if status == "completed":
            video_url = (
                data.get("output", {}).get("video", {}).get("video_url")
                or data.get("output", {}).get("video_url")
            )
            if video_url:
                return video_url
            raise RuntimeError(f"Completed but no video_url in output: {data}")

        if status in {"failed", "error"}:
            raise RuntimeError(f"HeyGen generation failed: {data.get('error', 'Unknown')}")

        time.sleep(min(interval, max(0.0, deadline - time.time())))
        interval = min(interval * 1.2, 30.0)

    raise TimeoutError(f"HeyGen execution {execution_id} timed out after {timeout}s")


def upload_image_fal(image_path: str) -> str:
    """Upload a local image to fal.ai storage and return a public URL."""
    import requests

    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY")
    if not api_key:
        raise RuntimeError("FAL_KEY or FAL_AI_API_KEY required for image upload")

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    suffix = path.suffix.lower()
    content_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(
        suffix.lstrip("."), "image/png"
    )

    # Initiate upload
    init_resp = requests.post(
        "https://rest.alpha.fal.ai/storage/upload/initiate",
        headers={"Authorization": f"Key {api_key}", "Content-Type": "application/json"},
        json={"content_type": content_type, "file_name": path.name},
        timeout=30,
    )
    init_resp.raise_for_status()
    data = init_resp.json()

    # Upload file content
    put_resp = requests.put(
        data["upload_url"],
        headers={"Content-Type": content_type},
        data=path.read_bytes(),
        timeout=60,
    )
    put_resp.raise_for_status()

    return data["file_url"]


def upload_image_heygen(image_path: str, api_key: str) -> str:
    """Upload a local image to HeyGen and return a public URL.

    Tries the v2 presigned-upload endpoint first, falls back to fal.ai storage.
    """
    import requests

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Try HeyGen v2 presigned upload
    try:
        resp = requests.post(
            "https://api.heygen.com/v2/assets/upload",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json={"content_type": "image/png", "file_name": path.name},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            upload_url = data.get("upload_url")
            file_url = data.get("url") or data.get("file_url")
            if upload_url and file_url:
                put_resp = requests.put(
                    upload_url,
                    headers={"Content-Type": "image/png"},
                    data=path.read_bytes(),
                    timeout=60,
                )
                put_resp.raise_for_status()
                return file_url
    except Exception:
        pass

    # Fallback to fal.ai storage upload
    return upload_image_fal(image_path)


def generate_heygen_video(inputs: dict[str, Any]) -> ToolResult:
    import requests

    api_key = os.environ.get("HEYGEN_API_KEY")
    if not api_key:
        return ToolResult(success=False, error="HEYGEN_API_KEY not set.")

    provider = inputs.get("provider_variant", "veo_3_1")
    if provider not in HEYGEN_PROVIDERS:
        return ToolResult(
            success=False,
            error=f"Unknown provider_variant: {provider}. Available: {', '.join(sorted(HEYGEN_PROVIDERS))}",
        )

    prompt = inputs["prompt"]
    aspect_ratio = inputs.get("aspect_ratio", "16:9")
    operation = inputs.get("operation", "text_to_video")
    workflow_input: dict[str, Any] = {
        "prompt": prompt,
        "provider": provider,
        "aspect_ratio": aspect_ratio,
    }
    if operation == "image_to_video":
        ref_url = inputs.get("reference_image_url")
        ref_path = inputs.get("reference_image_path")
        if ref_path and not ref_url:
            ref_url = upload_image_heygen(ref_path, api_key)
        if not ref_url:
            return ToolResult(
                success=False,
                error="image_to_video requires reference_image_url or reference_image_path",
            )
        workflow_input["reference_image_url"] = ref_url

    response = requests.post(
        "https://api.heygen.com/v1/workflows/executions",
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        json={"workflow_type": "GenerateVideoNode", "input": workflow_input},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    execution_id = payload.get("data", {}).get("execution_id")
    if not execution_id:
        return ToolResult(success=False, error=f"No execution_id in response: {payload}")

    video_url = poll_heygen(execution_id, api_key, timeout=600)
    output_path = Path(inputs.get("output_path", f"heygen_video_{execution_id}.mp4"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    download = requests.get(video_url, timeout=120)
    download.raise_for_status()
    output_path.write_bytes(download.content)

    meta = HEYGEN_PROVIDERS[provider]
    return ToolResult(
        success=True,
        data={
            "provider": "heygen",
            "provider_variant": provider,
            "provider_name": meta["name"],
            "mode": "api",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "operation": operation,
            "execution_id": execution_id,
            "output": str(output_path),
            "format": "mp4",
        },
        artifacts=[str(output_path)],
        model=provider,
    )


def generate_ltx_modal_video(inputs: dict[str, Any]) -> ToolResult:
    import base64

    import requests

    endpoint_url = os.environ.get("MODAL_LTX2_ENDPOINT_URL")
    if not endpoint_url:
        return ToolResult(success=False, error="MODAL_LTX2_ENDPOINT_URL not set.")

    prompt = inputs["prompt"]
    operation = inputs.get("operation", "text_to_video")
    aspect = inputs.get("aspect_ratio", "16:9")
    width = inputs.get("width")
    height = inputs.get("height")
    if width is None or height is None:
        if aspect == "16:9":
            width, height = 1024, 576
        elif aspect == "9:16":
            width, height = 576, 1024
        else:
            width, height = 512, 512

    num_frames = inputs.get("num_frames", LTX2_FRAME_COUNTS.get(inputs.get("duration_hint", "5s"), 121))
    if (num_frames - 1) % 8 != 0:
        num_frames = ((num_frames - 1) // 8) * 8 + 1

    payload: dict[str, Any] = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "fps": 24,
        "steps": inputs.get("num_inference_steps", 30),
        "negative_prompt": "worst quality, low quality, blurry, distorted, watermark, text, logo",
    }
    if inputs.get("seed") is not None:
        payload["seed"] = inputs["seed"]

    if operation == "image_to_video":
        ref_path = inputs.get("reference_image_path")
        ref_url = inputs.get("reference_image_url")
        if ref_path:
            payload["input_image"] = base64.b64encode(Path(ref_path).read_bytes()).decode()
        elif ref_url:
            payload["input_image_url"] = ref_url
        else:
            return ToolResult(
                success=False,
                error="image_to_video requires reference_image_url or reference_image_path",
            )

    response = requests.post(endpoint_url, json=payload, timeout=300)
    response.raise_for_status()
    output_path = Path(inputs.get("output_path", "ltx_video_modal.mp4"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content_type = response.headers.get("content-type", "")
    if "video" in content_type or "octet-stream" in content_type:
        output_path.write_bytes(response.content)
    else:
        response_payload = response.json()
        video_url = response_payload.get("video_url") or response_payload.get("url")
        if not video_url:
            return ToolResult(success=False, error=f"No video data in response: {response_payload}")
        download = requests.get(video_url, timeout=120)
        download.raise_for_status()
        output_path.write_bytes(download.content)

    return ToolResult(
        success=True,
        data={
            "provider": "ltx-modal",
            "provider_name": "LTX-2 (Modal)",
            "mode": "modal",
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "fps": 24,
            "duration_seconds": round(num_frames / 24, 2),
            "operation": operation,
            "output": str(output_path),
            "format": "mp4",
        },
        artifacts=[str(output_path)],
        seed=inputs.get("seed"),
        model="ltx-2",
    )


def probe_output(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"file_size_bytes": path.stat().st_size}
    if not shutil.which("ffprobe"):
        return info

    import json

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            probe = json.loads(proc.stdout)
            fmt = probe.get("format", {})
            info["duration_seconds"] = float(fmt.get("duration", 0))
            info["file_size_mb"] = round(path.stat().st_size / (1024 * 1024), 2)
            for stream in probe.get("streams", []):
                if stream.get("codec_type") == "video":
                    info["video_width"] = int(stream.get("width", 0))
                    info["video_height"] = int(stream.get("height", 0))
                    info["video_codec"] = stream.get("codec_name", "")
                    break
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# FAL queue helpers (PLAN §0 idempotency, §9 hardening; fixture: tests/fixtures/
# providers/fal-queue.json). Additive — the legacy inline polling in older
# tools is untouched.
# ---------------------------------------------------------------------------

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_ALLOWED_HOSTS: tuple[str, ...] = ("fal.run", "queue.fal.run", "fal.media", "v3.fal.media")
FAL_NO_RETRY_HEADER = {"X-Fal-No-Retry": "1"}
MAX_TRANSIENT_POLL_FAILURES = 10  # read-only status GETs only; never affects submission
FAL_TERMINAL_FAILURES = ("FAILED", "CANCELLED", "ERROR")


class FalQueueError(RuntimeError):
    """Submit/poll/result failure reported by the FAL queue."""


class FalDeadlineExceeded(FalQueueError):
    """Hard deadline hit while waiting; a cancel was issued."""


class FalDownloadError(RuntimeError):
    """Download rejected: disallowed host, MIME mismatch, or size cap."""


def _fal_headers(api_key: str, *, json_body: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Key {api_key}", **FAL_NO_RETRY_HEADER}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def fal_queue_submit(
    model_id: str, payload: dict[str, Any], *, api_key: str, timeout_s: float = 30.0
) -> dict[str, Any]:
    """POST ``payload`` to ``https://queue.fal.run/{model_id}`` and return the queue response.

    Always sends ``X-Fal-No-Retry: 1`` so the provider never re-runs (and
    re-bills) a request on 503/504 behind our back. The returned dict carries
    ``request_id``; callers must persist it immediately (attach_request_id).
    """
    import requests

    resp = requests.post(
        f"{FAL_QUEUE_BASE}/{model_id}",
        headers=_fal_headers(api_key, json_body=True),
        json=payload,
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("request_id"):
        raise FalQueueError(f"queue submit for {model_id} returned no request_id")
    return data


def fal_app_id(model_id: str) -> str:
    """FAL addresses queue requests by the app id — the first two path segments
    (``owner/app``) — even when the model id has sub-paths
    (``bytedance/seedream/v5/pro/text-to-image`` -> ``bytedance/seedream``).
    Verified against a live 405 on 2026-08-25 and fal.ai/docs/model-endpoints/queue."""
    parts = [p for p in model_id.split("/") if p]
    if len(parts) < 2:
        raise FalQueueError(f"model id {model_id!r} has no owner/app prefix")
    return "/".join(parts[:2])


def fal_request_url(model_id: str, request_id: str, leaf: str) -> str:
    """Status/response/cancel URL built from the fixture pattern (never from the server)."""
    base = f"{FAL_QUEUE_BASE}/{fal_app_id(model_id)}/requests/{request_id}"
    # The result is served at the bare request URL; only status/cancel have a leaf.
    # Verified live 2026-08-25 (GET .../response -> 405).
    if leaf in ("", "response", "result"):
        return base
    return f"{base}/{leaf}"


def fal_queue_cancel(model_id: str, request_id: str, *, api_key: str, timeout_s: float = 15.0) -> None:
    """Best-effort PUT cancel; errors are swallowed (the caller is already failing)."""
    import requests

    try:
        requests.put(
            fal_request_url(model_id, request_id, "cancel"),
            headers=_fal_headers(api_key),
            timeout=timeout_s,
        )
    except Exception:
        pass


def fal_queue_wait(
    model_id: str,
    request_id: str,
    *,
    api_key: str,
    deadline_s: float,
    poll_s: float = 5.0,
    _sleep=time.sleep,
    _clock=time.monotonic,
) -> dict[str, Any]:
    """Poll the status URL until COMPLETED, then fetch and return the result JSON.

    URLs are constructed from ``model_id``/``request_id`` per the fixture, never
    taken from the submit response. On ``deadline_s`` elapsing a cancel PUT is
    issued and ``FalDeadlineExceeded`` is raised. Terminal failure statuses
    raise ``FalQueueError``.
    """
    import requests

    headers = _fal_headers(api_key)
    status_url = fal_request_url(model_id, request_id, "status")
    started = _clock()
    transient_failures = 0
    while True:
        if _clock() - started > deadline_s:
            fal_queue_cancel(model_id, request_id, api_key=api_key)
            raise FalDeadlineExceeded(
                f"{model_id} request {request_id} exceeded {deadline_s}s; cancel issued"
            )
        try:
            resp = requests.get(status_url, headers=headers, timeout=15)
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as exc:
            # A transient network blip on a read-only status GET must not abandon a
            # paid render (seen live 2026-08-25: connect timeout mid-poll). Retry until
            # the overall deadline; the reservation stays pending_billing meanwhile.
            transient_failures += 1
            if transient_failures > MAX_TRANSIENT_POLL_FAILURES:
                raise FalQueueError(f"status polling for {request_id} failed {transient_failures} times: {exc}") from exc
            _sleep(poll_s)
            continue
        transient_failures = 0
        body = resp.json() if resp.content else {}
        status = str(body.get("status", "UNKNOWN")).upper()
        if status == "COMPLETED":
            break
        if status in FAL_TERMINAL_FAILURES:
            raise FalQueueError(
                f"{model_id} request {request_id} {status.lower()}: {body.get('error') or ''}".strip()
            )
        _sleep(poll_s)

    result = requests.get(fal_request_url(model_id, request_id, "response"), headers=headers, timeout=30)
    result.raise_for_status()
    return result.json()


def _host_allowed(host: str, allowed_hosts) -> bool:
    """Exact hostname match, or any subdomain of an allowlisted registered domain
    (FAL serves outputs from rotating CDN hosts such as v3.fal.media / v3b.fal.media;
    verified live 2026-08-25). ``evil-fal.media`` does NOT match ``fal.media``."""
    host = host.lower()
    for allowed in allowed_hosts:
        a = allowed.lower()
        if host == a or host.endswith("." + a):
            return True
    return False


def fal_download(
    url: str,
    dest_staging_path: Path | str,
    *,
    allowed_hosts: tuple[str, ...] | list[str] = FAL_ALLOWED_HOSTS,
    max_bytes: int,
    allowed_mime_prefixes: tuple[str, ...] | list[str],
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Stream ``url`` into ``dest_staging_path`` with host allowlist, MIME and size checks.

    The host must match ``allowed_hosts`` exactly (https only). The response
    Content-Type must start with one of ``allowed_mime_prefixes``; the body is
    streamed and aborted the moment it exceeds ``max_bytes``. Returns
    ``{"bytes": n, "content_type": ct}``.
    """
    from urllib.parse import urlparse

    import requests

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not _host_allowed(host, allowed_hosts):
        raise FalDownloadError(f"download host not allowed: {parsed.scheme}://{host}")

    dest = Path(dest_staging_path)
    written = 0
    with requests.get(url, stream=True, timeout=timeout_s, allow_redirects=False) as resp:
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not any(content_type.startswith(p.lower()) for p in allowed_mime_prefixes):
            raise FalDownloadError(f"unexpected content type {content_type!r} for {url}")
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FalDownloadError(f"download of {declared} bytes exceeds cap {max_bytes}")
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    fh.close()
                    try:
                        dest.unlink()
                    except FileNotFoundError:
                        pass
                    raise FalDownloadError(f"download exceeded cap of {max_bytes} bytes")
                fh.write(chunk)
    return {"bytes": written, "content_type": content_type}


# ---------------------------------------------------------------------------
# Paid-call context: project root + CostTracker for reserve/attach/reconcile.
# ---------------------------------------------------------------------------


class VideoVerificationError(RuntimeError):
    """ffprobe missing/failed or the file has no decodable video stream."""


def verify_video_file(path: Path | str, *, require_audio: bool = False) -> dict[str, Any]:
    """Fail-closed verification of a downloaded video (inspection #16).

    ``ffprobe`` MUST be installed, exit 0, and report at least one video
    stream; otherwise ``VideoVerificationError`` is raised and the caller must
    not move the file into place. When ``require_audio`` is set, at least one
    audio stream is required too. Returns the same info dict as
    ``probe_output`` plus ``has_audio``/``audio_codec``.
    """
    import json

    path = Path(path)
    if not path.is_file():
        raise VideoVerificationError(f"downloaded file missing: {path}")
    if not shutil.which("ffprobe"):
        raise VideoVerificationError("ffprobe is not installed; cannot verify the downloaded video")
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise VideoVerificationError(f"ffprobe could not run: {exc}") from exc
    if proc.returncode != 0:
        raise VideoVerificationError(f"ffprobe rejected the file: {(proc.stderr or '').strip()[:300]}")
    try:
        probe = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise VideoVerificationError("ffprobe produced unparsable output") from exc
    streams = probe.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        raise VideoVerificationError("ffprobe found no video stream in the downloaded file")
    if require_audio and not audio:
        raise VideoVerificationError("generate_audio was requested but the file has no audio stream")
    fmt = probe.get("format") or {}
    size = path.stat().st_size
    info: dict[str, Any] = {
        "file_size_bytes": size,
        "file_size_mb": round(size / (1024 * 1024), 2),
        "duration_seconds": float(fmt.get("duration", 0) or 0),
        "video_width": int(video[0].get("width", 0) or 0),
        "video_height": int(video[0].get("height", 0) or 0),
        "video_codec": video[0].get("codec_name", ""),
        "has_audio": bool(audio),
        "audio_codec": audio[0].get("codec_name", "") if audio else None,
    }
    return info


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class PaidCallContextError(RuntimeError):
    """The paid call cannot be attributed to a registered, approved project."""


def paid_call_context(
    inputs: dict[str, Any], *, check_resume: bool = True, governance: dict[str, Any] | None = None,
    media: str = "image",
) -> tuple[Path, Any, Any]:
    """Resolve ``(project_root, tracker, config)`` for a paid FAL call (inspection #2).
    ``media`` (``"image"`` default / ``"video"``) is forwarded to
    ``verify_look_governance``: image renderings of look_refs need a rebuilt
    prompt_recipe (round 2 #6).

    Look governance (plan D10, Slice A step 5(b)) runs here, before any upload
    or reservation: ``verify_look_governance`` refuses a governed visual call
    that carries no verified ``look_refs`` (unless the named ``shot_id`` is
    entity-free in the approved scene plan) and verifies ``headshot_ref`` for
    sheet roles. Pass a dict as ``governance`` to receive the verified
    ``look_refs`` / ``headshot_ref`` for binding into the generation receipt.

    The project root is derived ONLY from ``lib.events.infer_project_dir`` and
    must be a registered project directory under ``lib.paths.PROJECTS_DIR``;
    arbitrary directories are rejected. Caller-injected trackers/caps are not
    honored: the CAP-mode CostTracker is built over ``<root>/cost_log.json``
    from the human-approved ``project.yaml`` (``load_verified_project_config``),
    which is also returned so callers can ``require_egress(...)`` per content
    class before any upload. ``resume_check`` runs first: an unreconciled
    paid call blocks every new one before any upload or reservation
    (``check_resume=False`` is reserved for scripts/reconcile_paid_calls.py).
    """
    from lib.config_model import BudgetMode
    from lib.events import infer_project_dir
    from lib.paths import PROJECTS_DIR
    from lib.project_config import load_verified_project_config
    from tools.cost_tracker import CostTracker, resume_check

    if not isinstance(inputs, dict) or not inputs.get("project_dir"):
        raise PaidCallContextError("paid generation requires inputs['project_dir'] (a registered project root)")
    project_root = infer_project_dir(inputs)
    if project_root is None:
        raise PaidCallContextError(
            f"paid generation requires a registered project under {PROJECTS_DIR}; "
            f"got project_dir={inputs.get('project_dir')!r}"
        )
    project_root = Path(project_root).resolve()
    try:
        project_root.relative_to(Path(PROJECTS_DIR).resolve())
    except ValueError as exc:
        raise PaidCallContextError(f"{project_root} is not under {PROJECTS_DIR}") from exc
    if project_root.is_symlink() or not project_root.is_dir():
        raise PaidCallContextError(f"registered project directory missing or a symlink: {project_root}")

    # Any nonterminal reservation (submitting / pending_billing) means a prior
    # paid call has no known outcome; refuse to spend again before a human runs
    # scripts/reconcile_paid_calls.py. Checked before any upload or reservation.
    # Only the human-run reconciler (which never resubmits) opts out.
    if check_resume:
        resume_check(project_root)  # raises IndeterminatePaidCallError

    config = load_verified_project_config(project_root)  # raises ProjectConfigError
    verified = verify_look_governance(inputs, project_root, media=media)
    if governance is not None:
        governance.update(verified)
    tracker = CostTracker(
        budget_total_usd=float(config.budget_usd_cap),
        reserve_pct=0.0,
        single_action_approval_usd=float("inf"),
        require_approval_for_new_paid_tool=False,
        mode=BudgetMode.CAP,
        cost_log_path=project_root / "cost_log.json",
    )
    return project_root, tracker, config


# ---- D19: pre-submit hook and the QC (judge) call context ----

import contextvars as _contextvars

_PRE_SUBMIT: _contextvars.ContextVar = _contextvars.ContextVar("openmontage_pre_submit", default=None)


@contextmanager
def pre_submit_hook(fn):
    """Install ``fn(project_root, reservation_id)`` to run inside a paid tool
    after its reservation is persisted and BEFORE provider submission (D19.5:
    sheet_run commits the signed ``attempt_started`` row there, so an attempt
    is recorded for every generated candidate, crash or not)."""
    token = _PRE_SUBMIT.set(fn)
    try:
        yield
    finally:
        _PRE_SUBMIT.reset(token)


def run_pre_submit(project_root: Path, reservation_id: str) -> None:
    fn = _PRE_SUBMIT.get()
    if fn is not None:
        fn(project_root, reservation_id)


class QCCallContextError(RuntimeError):
    """A judge call is not allowed to start."""


def qc_call_context(
    inputs: dict[str, Any], *, check_resume: bool = True,
) -> tuple[Path, Any, Any, Any, dict[str, Any]]:
    """Resolve ``(project_root, tracker, config, qc_config, attempt_started_row)``
    for a sheet-QC judge call (D19.2, Codex R1#3).

    Same root discipline as ``paid_call_context`` (registered project under
    PROJECTS_DIR, no symlink), same resume check and CAP-mode tracker, but NO
    look/prompt-recipe governance (there is no generation prompt). Instead the
    call must name an OPEN signed attempt (``inputs['attempt_id']``: an
    attempt_started row for this project with no verdict_attached yet — Codex
    R4#2) and the project must be pinned to a QC manifest (authored-film 1.3)
    with a 1.1 config whose ``qc`` block names the judge, and egress consent
    for BOTH ``prompts`` and ``generated_sheet_images`` to that provider.
    """
    from lib.canon_enforcement import _is_qc_manifest
    from lib.config_model import BudgetMode
    from lib.events import infer_project_dir
    from lib.paths import PROJECTS_DIR
    from lib.pipeline_pin import _read_marker, pinned_pipeline
    from lib.project_config import load_verified_project_config
    from lib import qc_receipts
    from tools.cost_tracker import CostTracker, resume_check

    if not isinstance(inputs, dict) or not inputs.get("project_dir"):
        raise QCCallContextError("a judge call requires inputs['project_dir'] (a registered project root)")
    project_root = infer_project_dir(inputs)
    if project_root is None:
        raise QCCallContextError(f"judge call requires a registered project under {PROJECTS_DIR}")
    project_root = Path(project_root).resolve()
    try:
        project_root.relative_to(Path(PROJECTS_DIR).resolve())
    except ValueError as exc:
        raise QCCallContextError(f"{project_root} is not under {PROJECTS_DIR}") from exc
    if project_root.is_symlink() or not project_root.is_dir():
        raise QCCallContextError(f"registered project directory missing or a symlink: {project_root}")
    if check_resume:
        resume_check(project_root)

    config = load_verified_project_config(project_root)
    qc = config.require_qc()
    pipeline_type = str(_read_marker(project_root).get("pipeline_type") or "authored-film")
    pin = pinned_pipeline(project_root, pipeline_type)
    if not _is_qc_manifest(pin):
        raise QCCallContextError(
            f"project is pinned to {pin.name}@{pin.version}; QC needs authored-film 1.3 or 1.4 "
            f"(approve a pipeline_migration first)"
        )
    config.require_egress(qc.judge_provider, "prompts", "generated_sheet_images")

    attempt_id = inputs.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise QCCallContextError("a judge call runs only inside an open signed attempt: inputs['attempt_id'] is required")
    rows = qc_receipts.attempt_rows(project_root, attempt_id)
    started = rows["started"]
    if started is None:
        raise QCCallContextError(f"no signed attempt_started row {attempt_id!r} in this project's QC chain")
    if rows["voided"] is not None:
        raise QCCallContextError(f"attempt {attempt_id} is voided (terminal); start a new attempt")
    if rows["verdict"] is not None:
        raise QCCallContextError(f"attempt {attempt_id} already has a verdict attached; start a new attempt")
    key = started.get("series_key") or {}
    if key.get("role") == "hero":
        # D20: hero judging needs the 1.2 config (hero bundle + budget) and pin
        # 1.4 — or pin 1.3 for the one legacy-hero grandfather attempt (D20.7).
        from lib.canon_enforcement import _is_hero_qc_manifest
        from lib.project_config import ProjectConfigError

        try:
            qc = config.require_hero_qc()
        except ProjectConfigError as exc:
            raise QCCallContextError(str(exc)) from exc
        if not _is_hero_qc_manifest(pin) and not (key.get("grandfather") is True and pin.version == "1.3"):
            raise QCCallContextError(
                f"project is pinned to {pin.name}@{pin.version}; hero QC needs authored-film 1.4 "
                f"(pin 1.3 is accepted only for a grandfather attempt)"
            )
        pinned_bundle = qc.hero_policy_sha256
    else:
        pinned_bundle = qc.policy_bundle_sha256
    if key.get("judge_provider") != qc.judge_provider or key.get("judge_model") != qc.judge_model \
            or key.get("policy_bundle_sha256") != pinned_bundle:
        raise QCCallContextError(
            "attempt series names a different judge or policy bundle than the signed config; the config changed "
            "since the attempt started — start a new attempt"
        )

    tracker = CostTracker(
        budget_total_usd=float(config.budget_usd_cap), reserve_pct=0.0,
        single_action_approval_usd=float("inf"), require_approval_for_new_paid_tool=False,
        mode=BudgetMode.CAP, cost_log_path=project_root / "cost_log.json",
    )
    return project_root, tracker, config, qc, started


# ---- governed reference packing (shared by seedance_video 2.5 and kling_reference_video) ----

STORYBOARD_OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"


def storyboard_preflight(inputs: dict[str, Any], project_root: Path, *, model_label: str) -> Path | None:
    """A shot take must be covered by a signed storyboard approval AND the
    approved frame must be the file we upload (Codex R2 #4).

    The asset director ALWAYS passes ``asset_class="shot_visual"``, ``shot_id``
    and ``storyboard_frame_sha256`` for shot takes. Preflight verifies the
    receipt (``lib.receipts.require_storyboard_receipt``, imported lazily —
    its absence fails closed), then locates the frame file (``storyboard_frame_path``
    or ``<project>/canon/visual/objects/<sha>.png``), re-hashes it and
    requires equality with ``storyboard_frame_sha256``. Returns the resolved
    frame path (None for non-shot material).
    """
    if inputs.get("asset_class") != "shot_visual":
        return None
    from lib import pathsafe

    shot_id = inputs.get("shot_id")
    frame_sha = inputs.get("storyboard_frame_sha256")
    if not shot_id or not frame_sha:
        raise ValueError("shot_visual takes require inputs['shot_id'] and inputs['storyboard_frame_sha256']")
    for key in ("reference_image_urls", "start_image_url", "end_image_url"):
        if inputs.get(key):
            raise ValueError(
                f"shot_visual takes accept only hash-verified local references "
                f"(reference_image_paths + reference_manifest); {key} cannot be proven"
            )
    import lib.receipts as receipts_mod

    checker = getattr(receipts_mod, "require_storyboard_receipt", None)
    if checker is None:
        raise RuntimeError("lib.receipts.require_storyboard_receipt is unavailable; cannot verify storyboard approval")
    checker(project_root, str(shot_id), str(frame_sha))

    raw = inputs.get("storyboard_frame_path") or (project_root / STORYBOARD_OBJECTS_SUBDIR / f"{frame_sha}.png")
    try:
        frame = pathsafe.resolve_input(raw, project_root)
    except pathsafe.PathSafetyError as exc:
        raise ValueError(f"approved storyboard frame for shot {shot_id!r} is not a project-local file: {exc}") from exc
    actual = pathsafe.sha256_file(frame)
    if actual != str(frame_sha):
        raise ValueError(
            f"storyboard frame file {frame} hashes to {actual} but storyboard_frame_sha256 is "
            f"{frame_sha} — the approved frame is not the file that would be uploaded ({model_label})"
        )
    return frame


def bind_reference_manifest(
    inputs: dict[str, Any], project_root: Path, local_refs: list[Path]
) -> list[dict[str, Any]]:
    """Bind ``reference_image_paths`` to ``reference_manifest`` objects by hash.

    The manifest must list exactly one object per local reference, in
    order, with a project-local ``path`` resolving to the same file and an
    ``asset_id`` equal to the file's sha256. Returned items are the
    normalized objects recorded as ``references_applied``.
    """
    from lib import pathsafe
    from lib.receipts import normalize_reference

    manifest = inputs.get("reference_manifest")
    if not local_refs:
        if manifest:
            raise ValueError("reference_manifest given without reference_image_paths")
        return []
    if not isinstance(manifest, list) or len(manifest) != len(local_refs):
        raise ValueError(
            f"reference_manifest must list one object per reference_image_paths entry "
            f"({len(local_refs)}); got {len(manifest) if isinstance(manifest, list) else 'none'}"
        )
    applied: list[dict[str, Any]] = []
    for i, (resolved, item) in enumerate(zip(local_refs, manifest)):
        ref = normalize_reference(item)
        if ref.get("role") == "storyboard" or "shot_id" in ref:
            raise ValueError(
                f"reference_manifest[{i}] is a storyboard reference — the tool packs the approved "
                f"frame itself from shot_id/storyboard_frame_sha256"
            )
        if not ref.get("path") or not ref.get("visual_bible_entity_id"):
            raise ValueError(f"reference_manifest[{i}] needs asset_id, path, role, visual_bible_entity_id")
        try:
            manifest_file = pathsafe.resolve_input(ref["path"], project_root)
        except pathsafe.PathSafetyError as exc:
            raise ValueError(f"reference_manifest[{i}].path is not project-local: {exc}") from exc
        if manifest_file != resolved:
            raise ValueError(
                f"reference_manifest[{i}].path {ref['path']!r} is not reference_image_paths[{i}] ({resolved})"
            )
        actual = pathsafe.sha256_file(resolved)
        if actual != ref["asset_id"]:
            raise ValueError(
                f"reference_manifest[{i}] asset_id {ref['asset_id']} does not match the file "
                f"{resolved} (sha256 {actual}) — refusing to upload an unproven reference"
            )
        applied.append(ref)
    return applied


# ---------------------------------------------------------------------------
# Look governance (plan D10 Slice A step 5(b)/7, Slice A′ steps 1–3; D16/D17).
# Shared by every governed visual tool: seedream_image, kling_reference_video,
# seedance_video (2.5), title_card, poster_composite. Everything here runs
# BEFORE any upload or reservation and fails closed when the lib-side
# verifiers (built separately) are missing.
# ---------------------------------------------------------------------------

LOOK_GOVERNED_STAGE = "look_lock"
ENTITY_KINDS = ("character", "location")
SHEET_ASSET_ROLES = frozenset({"turnaround", "front", "three_quarter", "profile", "full_body", "expressions", "wardrobe"})
# Presence of any of these makes a call governed regardless of the project's
# manifest: a caller who names looks, a stage, a sheet role or a headshot is
# making a governed call and gets the full check.
GOVERNED_CALL_KEYS = ("look_refs", "stage", "asset_role", "headshot_ref", "prompt_recipe")
REMOTE_REFERENCE_KEYS = ("reference_image_urls", "start_image_url", "end_image_url")
REAL_PERSON_REFUSAL = (
    "reference refused: it has no receipted pipeline lineage (or is casting inspiration of a real "
    "person). Only pipeline-generated images and attested imported_synthetic images may be uploaded "
    "as references; real-person or unconsented images are never sent to a provider."
)
_HEX64 = frozenset("0123456789abcdef")


class LookGovernanceError(RuntimeError):
    """A governed visual call is missing or misusing its look / headshot / lineage proof."""


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _lazy(module: str, name: str):
    """Import ``module.name`` by name; a missing module or attribute fails closed."""
    import importlib

    try:
        mod = importlib.import_module(module)
    except ImportError as exc:
        raise LookGovernanceError(f"{module}.{name} is unavailable ({exc}); refusing the governed call") from exc
    fn = getattr(mod, name, None)
    if fn is None:
        raise LookGovernanceError(f"{module}.{name} is unavailable; refusing the governed call")
    return fn


def project_look_governed(project_root: Path | str) -> bool:
    """Whether ``project_root`` runs under a manifest that declares ``look_lock`` (D13).

    Answered by ``lib.look_ingest.project_look_governed`` — the signed pipeline
    pin (unique migration-chain tip, 1.1 when unpinned), never the marker file
    alone. Imported by name and fails closed if the lib side is missing.
    """
    return bool(_lazy("lib.look_ingest", "project_look_governed")(Path(project_root)))


def call_is_governed(inputs: dict[str, Any], project_root: Path | str) -> bool:
    return any(inputs.get(k) is not None for k in GOVERNED_CALL_KEYS) or project_look_governed(project_root)


def normalize_look_ref(ref: Any, index: int = 0) -> dict[str, str]:
    if not isinstance(ref, dict):
        raise LookGovernanceError(f"look_refs[{index}] must be an object {{entity_kind, entity_id, look_hash}}")
    kind, eid, look_hash = ref.get("entity_kind"), ref.get("entity_id"), ref.get("look_hash")
    if kind not in ENTITY_KINDS:
        raise LookGovernanceError(f"look_refs[{index}].entity_kind must be one of {ENTITY_KINDS}; got {kind!r}")
    if not isinstance(eid, str) or not eid:
        raise LookGovernanceError(f"look_refs[{index}].entity_id must be a non-empty string")
    if not _is_sha256(look_hash):
        raise LookGovernanceError(f"look_refs[{index}].look_hash must be a 64-hex sha256")
    return {"entity_kind": kind, "entity_id": eid, "look_hash": look_hash}


def _scene_plan_shot_index(project_root: Path) -> dict[str, str]:
    """``shot_id -> scene_id`` from the on-disk ``scene_plan`` checkpoint.

    This is a MAPPING only — never an authorization. Which scenes are
    entity-free is decided exclusively by ``lib.checkpoint.entity_free_scene_ids``
    (inspection round 2 #7); the checkpoint's ``status`` / ``human_approved`` /
    ``entity_free`` fields are not read here.
    """
    import lib.checkpoint as checkpoint_mod

    root = Path(project_root)
    try:
        checkpoint = checkpoint_mod.read_checkpoint(root.parent, root.name, "scene_plan")
    except Exception:  # noqa: BLE001 — an unreadable plan maps nothing
        return {}
    plan = ((checkpoint or {}).get("artifacts") or {}).get("scene_plan") or {}
    index: dict[str, str] = {}
    for scene in plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("scene_id")
        if not isinstance(scene_id, str) or not scene_id:
            continue
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict) and shot.get("shot_id") is not None:
                index.setdefault(str(shot.get("shot_id")), scene_id)
    return index


def entity_free_scene_ids(project_root: Path | str) -> set[str]:
    """Scene ids the project may generate without ``look_refs``.

    Answered ONLY by ``lib.checkpoint.entity_free_scene_ids`` — the scene ids
    of a completed, non-invalidated, pin-matching, receipt-bound ``scene_plan``
    (inspection round 2 #7). Imported by name and fails closed if the lib side
    is missing; any lib-side failure is an empty set (not authorization).
    """
    fn = _lazy("lib.checkpoint", "entity_free_scene_ids")
    try:
        ids = fn(Path(project_root))
    except Exception:  # noqa: BLE001 — unknown scene-plan state is not authorization
        return set()
    return {str(s) for s in (ids or ())}


def shot_is_entity_free(project_root: Path | str, shot_id: str) -> bool:
    """Server-side lookup (R4#2): True only when ``shot_id`` belongs to a scene
    that ``lib.checkpoint.entity_free_scene_ids`` reports as entity-free. The
    tool-call inputs are never consulted, and neither are the checkpoint's
    own ``status`` / ``human_approved`` / ``entity_free`` fields: an edited,
    unsigned checkpoint claiming ``entity_free`` authorizes nothing (round 2 #7).
    """
    root = Path(project_root)
    free = entity_free_scene_ids(root)
    if not free:
        return False
    scene_id = _scene_plan_shot_index(root).get(str(shot_id))
    return scene_id is not None and scene_id in free


def _verify_look_refs(inputs: dict[str, Any], project_root: Path) -> list[dict[str, str]]:
    refs = inputs.get("look_refs")
    stage = inputs.get("stage")
    if "entity_free" in inputs:
        raise LookGovernanceError(
            "entity_free is not a tool-call input; it is read from the approved scene_plan checkpoint for shot_id"
        )
    if not refs:
        if stage == "visual_bible":
            raise LookGovernanceError("visual_bible generation can never be entity-free: look_refs[] is required")
        shot_id = inputs.get("shot_id")
        if not shot_id:
            raise LookGovernanceError(
                "governed visual call requires look_refs[] {entity_kind, entity_id, look_hash}; "
                "only a shot_id whose approved scene_plan record is entity_free may omit them"
            )
        if not shot_is_entity_free(project_root, str(shot_id)):
            raise LookGovernanceError(
                f"shot {shot_id!r} is not entity_free in the approved scene_plan checkpoint; look_refs[] is required"
            )
        return []
    if not isinstance(refs, list):
        raise LookGovernanceError("look_refs must be a list")
    out = [normalize_look_ref(r, i) for i, r in enumerate(refs)]
    keys = [(r["entity_kind"], r["entity_id"]) for r in out]
    if len(set(keys)) != len(keys):
        raise LookGovernanceError("look_refs names the same (entity_kind, entity_id) twice")
    # lib.look_ingest.verify_look_refs: every ref names the ACTIVE look_lock tip
    # for its key and that look is generation-sufficient; raises otherwise.
    verify_look_refs = _lazy("lib.look_ingest", "verify_look_refs")
    verify_look_refs(project_root, out)
    return out


def _verify_headshot_ref(
    inputs: dict[str, Any], project_root: Path, look_refs: list[dict[str, str]]
) -> dict[str, str] | None:
    role = inputs.get("asset_role")
    ref = inputs.get("headshot_ref")
    if role is not None and role in SHEET_ASSET_ROLES and ref is None:
        raise LookGovernanceError(
            f"asset_role {role!r} is a character-sheet role: headshot_ref {{entity_id, asset_id, approval_receipt_id}} "
            f"is required — sheets derive only from the approved hero (Slice A′ step 3)"
        )
    if ref is None:
        return None
    if not isinstance(ref, dict):
        raise LookGovernanceError("headshot_ref must be an object {entity_id, asset_id, approval_receipt_id}")
    eid, asset_id, receipt_id = ref.get("entity_id"), ref.get("asset_id"), ref.get("approval_receipt_id")
    if not isinstance(eid, str) or not eid:
        raise LookGovernanceError("headshot_ref.entity_id must be a non-empty string")
    if not _is_sha256(asset_id):
        raise LookGovernanceError("headshot_ref.asset_id must be the hero's 64-hex sha256")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise LookGovernanceError("headshot_ref.approval_receipt_id must be a non-empty string")
    if not any(r["entity_kind"] == "character" and r["entity_id"] == eid for r in look_refs):
        raise LookGovernanceError(f"headshot_ref.entity_id {eid!r} is not a character named in look_refs")
    normalized = {"entity_id": eid, "asset_id": asset_id, "approval_receipt_id": receipt_id}
    # lib.headshots.verify_headshot_ref: the ref must be the ACTIVE headshot tip
    # (same asset, same receipt) approved against the active look; raises otherwise.
    verify_headshot_ref = _lazy("lib.headshots", "verify_headshot_ref")
    verify_headshot_ref(project_root, normalized)
    return normalized


IMPORTED_SYNTHETIC_ORIGIN = "imported_synthetic"


def _recipe_required(inputs: dict[str, Any], look_refs: list[dict[str, str]], media: str) -> bool:
    """Whether this call must carry a rebuilt ``prompt_recipe`` (round 2 #6).

    Every model/local IMAGE rendering of a look — a call that names
    ``look_refs`` and renders them (it carries a ``prompt`` or an
    ``asset_role``, or runs at ``stage: visual_bible``) — must be a builder
    rendering, not only ``visual_bible``. Exempt: video calls (the builder
    has no motion roles), calls with no look_refs (entity-free shots), and
    receipt-bound scene renders that name a ``shot_id`` of the approved
    scene plan (their prompt is the shot, not an appearance).
    """
    if media != "image" or not look_refs:
        return False
    if inputs.get("stage") == "visual_bible":
        return True
    if inputs.get("shot_id"):
        return False
    return isinstance(inputs.get("prompt"), str) or inputs.get("asset_role") is not None


def _verify_imported_synthetic(inputs: dict[str, Any]) -> bool:
    """An ``origin: imported_synthetic`` candidate is never generated here; it
    may omit the recipe only with a non-empty ``import_receipt_id`` and no
    prompt (the import gate, not this boundary, attests the file)."""
    origin = inputs.get("origin")
    if origin is None:
        return False
    if origin != IMPORTED_SYNTHETIC_ORIGIN:
        raise LookGovernanceError(f"origin {origin!r} is not a generation origin this boundary accepts")
    receipt_id = inputs.get("import_receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise LookGovernanceError("origin imported_synthetic requires a non-empty import_receipt_id")
    if inputs.get("prompt") is not None or inputs.get("prompt_recipe") is not None:
        raise LookGovernanceError("an imported_synthetic candidate is not generated: prompt / prompt_recipe are refused")
    return True


def _verify_prompt_recipe(
    inputs: dict[str, Any], look_refs: list[dict[str, str]], project_root: Path, *, media: str = "image"
) -> dict[str, Any] | None:
    """Every image rendering of a look must be a builder rendering (round 2
    #6, generalising the ``visual_bible`` rule): the ``prompt_recipe`` names a
    look in ``look_refs``, ``rendered_sha256`` equals the hash of the prompt
    actually sent (no verbatim text), AND the prompt rebuilds from the signed
    ACTIVE look payload (inspection #2): the boundary calls
    ``tools.prompt_builder.build_prompt`` on the active look's payload with
    the same builder version, ``asset_role`` and ``palette`` as the call, and
    requires ``rendered_sha256`` equality. A rendering of appearance B
    submitted under look A's hash is refused before any upload. Omission is
    allowed only for an attested ``origin: imported_synthetic`` candidate."""
    recipe = inputs.get("prompt_recipe")
    if _verify_imported_synthetic(inputs):
        return None
    if recipe is None and not _recipe_required(inputs, look_refs, media):
        shot_id = inputs.get("shot_id")
        if media == "image" and look_refs and shot_id and str(shot_id) not in _scene_plan_shot_index(project_root):
            raise LookGovernanceError(
                f"shot {shot_id!r} is not a shot of the scene_plan checkpoint; a rendering of look_refs that is "
                "not a planned shot requires prompt_recipe from tools.prompt_builder"
            )
        return None
    if not isinstance(recipe, dict):
        raise LookGovernanceError(
            "a generated rendering of look_refs requires prompt_recipe {look_hash, builder_version, fields_used[], "
            "rendered_sha256} from tools.prompt_builder (only origin: imported_synthetic with an import_receipt_id "
            "may omit it)"
        )
    from tools.prompt_builder import BUILDER_VERSION, ROLES, PromptBuildError, build_prompt, rendered_prompt_sha256

    look_hash = recipe.get("look_hash")
    ref = next((r for r in look_refs if r["look_hash"] == look_hash), None)
    if ref is None:
        raise LookGovernanceError("prompt_recipe.look_hash is not one of the verified look_refs")
    prompt = inputs.get("prompt")
    if not isinstance(prompt, str) or rendered_prompt_sha256(prompt) != recipe.get("rendered_sha256"):
        raise LookGovernanceError(
            "prompt does not match prompt_recipe.rendered_sha256 — rebuild it with tools.prompt_builder"
        )
    if recipe.get("builder_version") != BUILDER_VERSION:
        raise LookGovernanceError(
            f"prompt_recipe.builder_version {recipe.get('builder_version')!r} is not the boundary builder "
            f"{BUILDER_VERSION!r} — rebuild the prompt"
        )
    role = inputs.get("asset_role")
    if role not in ROLES:
        raise LookGovernanceError(
            f"a prompt_recipe call must name asset_role (one of {ROLES}) so the boundary can rebuild the prompt"
        )
    # lib.look_ingest.active_look_for: the signed ACTIVE look_lock tip for the key
    # (payload + look_hash); imported by name and fails closed if absent.
    active = _lazy("lib.look_ingest", "active_look_for")(project_root, ref["entity_kind"], ref["entity_id"])
    if active is None or getattr(active, "look_hash", None) != look_hash:
        raise LookGovernanceError(
            f"prompt_recipe.look_hash {look_hash} is not the active look for ({ref['entity_kind']}, {ref['entity_id']})"
        )
    try:
        rebuilt = build_prompt(dict(active.payload), role=role, palette=inputs.get("palette"))
    except PromptBuildError as exc:
        raise LookGovernanceError(f"active look cannot be rendered by the boundary builder: {exc}") from exc
    if rebuilt["prompt_recipe"]["look_hash"] != look_hash:
        raise LookGovernanceError("active look payload does not hash to prompt_recipe.look_hash")
    if rebuilt["prompt_recipe"]["rendered_sha256"] != recipe.get("rendered_sha256"):
        raise LookGovernanceError(
            "prompt_recipe.rendered_sha256 does not rebuild from the active look payload "
            f"(role {role!r}) — the prompt was not rendered from the look it names"
        )
    out = {
        "look_hash": look_hash,
        "builder_version": recipe.get("builder_version"),
        "fields_used": list(recipe.get("fields_used") or []),
        "rendered_sha256": recipe.get("rendered_sha256"),
    }
    # D19.6: the builder policy hash rides in the sealed recipe; it must be the
    # boundary builder's own (a recipe claiming another policy is refused).
    claimed = recipe.get("builder_policy_sha256")
    if claimed is not None:
        if claimed != rebuilt["prompt_recipe"].get("builder_policy_sha256"):
            raise LookGovernanceError("prompt_recipe.builder_policy_sha256 is not the boundary builder's policy hash")
        out["builder_policy_sha256"] = claimed
    return out


def verify_look_governance(
    inputs: dict[str, Any], project_root: Path | str, *, media: str = "image"
) -> dict[str, Any]:
    """Run the generation-boundary look checks for one visual call.

    Returns ``{"governed": bool, "look_refs": [...] | None, "headshot_ref": {...} | None,
    "prompt_recipe": {...} | None}``. Non-governed (legacy manifest, no governed
    keys) calls get ``governed: False`` and ``None`` fields. Raises
    ``LookGovernanceError`` on any refusal. ``media`` is ``"image"`` (default;
    every rendering of look_refs needs a rebuilt prompt_recipe) or ``"video"``.
    """
    if media not in ("image", "video"):
        raise LookGovernanceError(f"media must be 'image' or 'video'; got {media!r}")
    root = Path(project_root)
    if not call_is_governed(inputs, root):
        return {"governed": False, "look_refs": None, "headshot_ref": None, "prompt_recipe": None}
    look_refs = _verify_look_refs(inputs, root)
    headshot_ref = _verify_headshot_ref(inputs, root, look_refs)
    prompt_recipe = _verify_prompt_recipe(inputs, look_refs, root, media=media)
    return {"governed": True, "look_refs": look_refs, "headshot_ref": headshot_ref, "prompt_recipe": prompt_recipe}


def verify_reference_lineage(
    inputs: dict[str, Any], project_root: Path | str, local_refs: list[Path], *, governed: bool
) -> None:
    """Reference provenance at the upload boundary (Slice A step 7, A′ step 1).

    On a governed call remote reference URLs are refused outright, and every
    local reference must (a) not be in the project's casting-inspiration taint
    set and (b) pass ``lib.receipts.verify_lineage`` (recursive receipted
    lineage). Both verifiers are imported by name and fail closed if absent.
    """
    if not governed:
        return
    for key in REMOTE_REFERENCE_KEYS:
        if inputs.get(key):
            raise LookGovernanceError(
                f"{key} refused: governed projects accept only project-local references with receipted lineage"
            )
    if not local_refs:
        return
    from lib.pathsafe import sha256_file

    root = Path(project_root)
    # lib.reference_import.tainted_hashes: every casting_inspiration pixel hash;
    # lib.reference_import.verify_lineage: recursive receipted lineage to
    # pipeline / attested-import roots (raises on any violation).
    taint = _lazy("lib.reference_import", "tainted_hashes")(root)
    verify_lineage = _lazy("lib.reference_import", "verify_lineage")
    for path in local_refs:
        sha = sha256_file(path)
        if sha in taint:
            raise LookGovernanceError(f"{path}: {REAL_PERSON_REFUSAL}")
        try:
            verify_lineage(root, sha, label=str(path))
        except Exception as exc:  # noqa: BLE001 — any lineage failure is a refusal
            raise LookGovernanceError(f"{path}: {REAL_PERSON_REFUSAL} ({exc})") from exc


def receipt_governance_fields(governance: dict[str, Any] | None) -> dict[str, Any]:
    """The receipt-bound subset of a ``verify_look_governance`` result — only
    keys that are set, so legacy calls produce receipts identical to before.
    The receipt binds ``look_refs`` / ``headshot_ref`` and, when the call was a
    builder rendering, the verified ``prompt_recipe`` (enforcement re-checks
    ``rendered_sha256`` against the sealed prompt for sheet images)."""
    out: dict[str, Any] = {}
    if not governance:
        return out
    for key in ("look_refs", "headshot_ref", "prompt_recipe"):
        if governance.get(key) is not None:
            out[key] = governance[key]
    return out


# ---- selector boundary (inspection #9) --------------------------------------

SELECTOR_LOCAL_REFERENCE_KEYS = ("reference_image_path", "reference_image_paths", "image_path", "image_paths")


def selector_governance(inputs: dict[str, Any], *, media: str = "image") -> dict[str, Any] | None:
    """Governance for the generic selectors, run BEFORE any upload or delegation.

    The project is inferred FIRST (round 2 #9) with ``lib.events.infer_project_dir``,
    which resolves any path-like input — ``project_dir`` or ``output_path`` /
    ``input_path`` hints — under the registered ``PROJECTS_DIR``. When a
    registered project resolves, governance is decided from its signed pin
    (``lib.look_ingest.project_look_governed``) regardless of which keys the
    caller supplied; governed keys force governance either way. Only a call
    that resolves to no project AND carries no governed key is legacy
    (``None``; the selector then behaves as before).

    A governed call runs ``paid_call_context`` (registered project, resume
    check, verified ``project.yaml``, ``verify_look_governance``) and
    ``verify_reference_lineage`` over every local reference, and the verified
    governance dict is returned so the selector restricts delegation to
    ``governance_bound`` providers. Raises on any refusal.
    """
    from lib import pathsafe
    from lib.events import infer_project_dir

    root = infer_project_dir(inputs)
    has_keys = any(inputs.get(k) is not None for k in GOVERNED_CALL_KEYS)
    if root is None and not has_keys:
        return None
    if root is not None and not has_keys and not project_look_governed(root):
        return None
    governance: dict[str, Any] = {}
    # The inferred root IS the project: a call reached through a path hint is
    # bound to it explicitly so the boundary (and the delegated provider) can
    # never resolve a different project than the one that governed it.
    bound_inputs = inputs if inputs.get("project_dir") else {**inputs, "project_dir": str(root)}
    project_root, _tracker, _config = paid_call_context(bound_inputs, governance=governance, media=media)
    local_refs: list[Path] = []
    for key in SELECTOR_LOCAL_REFERENCE_KEYS:
        value = inputs.get(key)
        if not value:
            continue
        for item in value if isinstance(value, list) else [value]:
            local_refs.append(pathsafe.resolve_input(str(item), project_root))
    verify_reference_lineage(inputs, project_root, local_refs, governed=True)
    governance["project_root"] = project_root
    return governance

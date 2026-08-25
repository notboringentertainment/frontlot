"""Shared helpers for provider-specific video generation tools."""

from __future__ import annotations

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


def fal_request_url(model_id: str, request_id: str, leaf: str) -> str:
    """Status/response/cancel URL built from the fixture pattern (never from the server)."""
    return f"{FAL_QUEUE_BASE}/{model_id}/requests/{request_id}/{leaf}"


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
    while True:
        if _clock() - started > deadline_s:
            fal_queue_cancel(model_id, request_id, api_key=api_key)
            raise FalDeadlineExceeded(
                f"{model_id} request {request_id} exceeded {deadline_s}s; cancel issued"
            )
        resp = requests.get(status_url, headers=headers, timeout=15)
        resp.raise_for_status()
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
    if parsed.scheme != "https" or host not in tuple(h.lower() for h in allowed_hosts):
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


def paid_call_context(inputs: dict[str, Any], *, check_resume: bool = True) -> tuple[Path, Any, Any]:
    """Resolve ``(project_root, tracker, config)`` for a paid FAL call (inspection #2).

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
    tracker = CostTracker(
        budget_total_usd=float(config.budget_usd_cap),
        reserve_pct=0.0,
        single_action_approval_usd=float("inf"),
        require_approval_for_new_paid_tool=False,
        mode=BudgetMode.CAP,
        cost_log_path=project_root / "cost_log.json",
    )
    return project_root, tracker, config

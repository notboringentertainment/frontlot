"""Base tool class implementing the expanded ToolContract.

Every tool in OpenMontage inherits from BaseTool. This enforces a uniform
interface for discovery, execution, cost estimation, and health reporting.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import platform
import subprocess
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


def _load_dotenv() -> None:
    """Load .env into os.environ once at import time.

    This ensures API keys are available before any tool is instantiated,
    even when tools are imported directly without going through the registry.
    Only sets variables that are not already in the environment.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    import re
    with open(env_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Quoted value: take the content inside the quotes verbatim.
            if value[:1] in ("'", '"'):
                quote = value[0]
                end = value.find(quote, 1)
                value = value[1:end] if end != -1 else value[1:]
            else:
                # Strip an inline comment ('#' at line start or after
                # whitespace) so "VAR=   # note" yields "" not "# note".
                match = re.search(r"(^|\s)#", value)
                if match:
                    value = value[: match.start()]
                value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


class ToolTier(str, Enum):
    CORE = "core"
    VOICE = "voice"
    ENHANCE = "enhance"
    GENERATE = "generate"
    SOURCE = "source"
    ANALYZE = "analyze"
    PUBLISH = "publish"


class ToolStability(str, Enum):
    EXPERIMENTAL = "experimental"
    BETA = "beta"
    PRODUCTION = "production"


class ToolStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class ToolRuntime(str, Enum):
    """Where and how a tool executes."""
    LOCAL = "local"            # Runs entirely on-device, free, no network
    LOCAL_GPU = "local_gpu"    # Runs on-device but needs GPU (VRAM)
    API = "api"                # Calls an external API, requires API key, costs money
    HYBRID = "hybrid"          # Can run locally OR via API (e.g., image_selector)


class ExecutionMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"


class Determinism(str, Enum):
    DETERMINISTIC = "deterministic"
    SEEDED = "seeded"
    STOCHASTIC = "stochastic"


class ResumeSupport(str, Enum):
    NONE = "none"
    FROM_START = "from_start"
    FROM_CHECKPOINT = "from_checkpoint"


@dataclass
class ResourceProfile:
    """Hardware resource envelope for a tool."""
    cpu_cores: int = 1
    ram_mb: int = 512
    vram_mb: int = 0
    disk_mb: int = 100
    network_required: bool = False


@dataclass
class RetryPolicy:
    """Safe retry behavior for a tool."""
    max_retries: int = 0
    backoff_seconds: float = 1.0
    retryable_errors: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    """Standard result returned by tool execution."""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: Optional[str] = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    seed: Optional[int] = None
    model: Optional[str] = None
    # Provenance hints consumed by the generation-receipt layer (PLAN §6):
    # model_endpoint, provider_request_id, generator_kind ("model"|"local"),
    # local_tool, local_tool_version, parameters_hash, input_asset_ids.
    metadata: dict[str, Any] = field(default_factory=dict)


import threading as _threading
import uuid as _uuid
from datetime import datetime as _datetime, timezone as _timezone

# Shared nesting counter for instrumented execute() calls (thread-local so
# parallel tool threads don't see each other's depth). Also carries the
# per-call ``execution_id`` of the innermost running execute().
_EXECUTE_DEPTH = _threading.local()

# Keys stripped from ``inputs`` before hashing for a generation receipt /
# paid-call reservation. Path-like keys are machine-specific (they encode the
# checkout location) and volatile keys change per run without changing what
# was generated. Anything whose value cannot be canonicalized (objects such
# as an injected cost tracker) is dropped too.
_VOLATILE_INPUT_KEYS = frozenset({
    "project_dir", "project_path", "output_path", "output_dir", "output_file",
    "objects_dir", "scene_id", "execution_id", "cost_tracker", "api_key",
    "budget_usd_cap", "poll_s", "deadline_s", "timeout_s",
})
_PATH_KEY_SUFFIXES = ("_path", "_paths", "_dir", "_file")


def current_execution_id() -> Optional[str]:
    """Execution id of the innermost instrumented execute() on this thread."""
    return getattr(_EXECUTE_DEPTH, "execution_id", None)


def normalized_inputs_hash(inputs: Any) -> str:
    """Canonical (RFC 8785) sha256 of ``inputs`` minus path-like and volatile keys.

    Dropped: keys in ``_VOLATILE_INPUT_KEYS``, keys ending in ``_path``,
    ``_paths``, ``_dir`` or ``_file``, and any key whose value is not
    JSON-canonicalizable. The same function is used for reservations and
    receipts so the two hashes always agree.
    """
    from lib.canonical_json import canonical_bytes, record_sha256

    if not isinstance(inputs, dict):
        return record_sha256(inputs)
    kept: dict[str, Any] = {}
    for key, value in inputs.items():
        if not isinstance(key, str) or key in _VOLATILE_INPUT_KEYS:
            continue
        if key.endswith(_PATH_KEY_SUFFIXES):
            continue
        try:
            canonical_bytes(value)
        except (TypeError, ValueError):
            continue
        kept[key] = value
    return record_sha256(kept)


def _utc_iso() -> str:
    return _datetime.now(_timezone.utc).isoformat()


def _write_generation_receipts(
    self: Any,
    inputs: Any,
    result: Any,
    *,
    execution_id: str,
    started_at: str,
    finished_at: str,
) -> None:
    """Record one generation receipt per output of a successful, flagged execute().

    Unlike events this is FATAL: any failure propagates to the caller, because
    a generated file without a receipt can never become canon (PLAN §6).
    """
    from lib.events import infer_project_dir
    from lib.pathsafe import sha256_file
    from lib.receipts import record_generation

    if not getattr(result, "success", False):
        return
    data = getattr(result, "data", None) or {}
    outputs = list(data.get("output_paths") or [])
    single = data.get("output_path") or (inputs.get("output_path") if isinstance(inputs, dict) else None)
    if single and str(single) not in [str(o) for o in outputs]:
        outputs.insert(0, single)
    if not outputs:
        return
    meta = getattr(result, "metadata", None) or {}
    tool_name = getattr(self, "name", "") or self.__class__.__name__
    inputs_hash = normalized_inputs_hash(inputs)
    for output_path in outputs:
        project_dir = infer_project_dir({"output_path": str(output_path)})
        if project_dir is None:
            continue
        record_generation(
            project_dir,
            execution_id=execution_id,
            tool=tool_name,
            model_endpoint=meta.get("model_endpoint"),
            provider_request_id=meta.get("provider_request_id"),
            normalized_inputs_hash=inputs_hash,
            output_sha256=sha256_file(output_path),
            cost_usd=float(getattr(result, "cost_usd", 0.0) or 0.0),
            started_at=started_at,
            finished_at=finished_at,
            generator_kind=meta.get("generator_kind", "model"),
            local_tool=meta.get("local_tool"),
            local_tool_version=meta.get("local_tool_version"),
            parameters_hash=meta.get("parameters_hash"),
            input_asset_ids=meta.get("input_asset_ids"),
        )


def _instrument_execute(fn: Callable) -> Callable:
    """Wrap a tool's execute() with Backlot event emission.

    Appends start/finish/error entries to the owning project's events.jsonl
    when the call can be attributed to a project (explicit project_dir input
    or any path input under projects/). Powers the board's live activity
    ticker and per-scene generating states with zero agent involvement.

    Instrumentation is strictly non-fatal: any failure inside the event layer
    is swallowed and the tool call proceeds untouched.

    Every call gets a uuid4 ``execution_id`` (thread-local, see
    ``current_execution_id``) included in the start/error/finish events. Tools
    that set ``emits_generation_receipt = True`` additionally get a generation
    receipt written for each output path under a project dir after a
    successful execute — that write is fatal on failure.
    """
    if getattr(fn, "_backlot_instrumented", False):
        return fn

    depth_state = _EXECUTE_DEPTH  # shared across all tools (selector → provider)

    @functools.wraps(fn)
    def wrapper(self, inputs: Any, *args: Any, **kwargs: Any):
        # Event layer is fully optional: if it can't import, run untouched.
        try:
            from lib.events import emit_event, infer_project_dir
        except Exception:
            return fn(self, inputs, *args, **kwargs)

        tool_name = getattr(self, "name", "") or self.__class__.__name__
        scene_id = inputs.get("scene_id") if isinstance(inputs, dict) else None
        output_path = inputs.get("output_path") if isinstance(inputs, dict) else None
        # Nesting depth: selector tools delegate to provider tools' execute().
        # Both emit (the ticker wants the provider name too), but depth lets
        # consumers dedupe — e.g. sum cost_usd only at depth 0.
        depth = getattr(depth_state, "value", 0)
        depth_state.value = depth + 1
        parent_execution_id = getattr(depth_state, "execution_id", None)
        execution_id = str(_uuid.uuid4())
        depth_state.execution_id = execution_id
        project_dir = infer_project_dir(inputs)

        base = {
            "tool": tool_name,
            "scene_id": scene_id,
            "depth": depth if depth else None,
            "execution_id": execution_id,
        }
        if project_dir is not None:
            emit_event(project_dir, {
                **base, "event": "start",
                "output_path": str(output_path) if output_path else None,
            })

        started = time.monotonic()
        started_at = _utc_iso()
        try:
            result = fn(self, inputs, *args, **kwargs)
        except Exception as exc:
            if project_dir is not None:
                emit_event(project_dir, {
                    **base, "event": "error",
                    "error": str(exc)[:300],
                    "duration_s": round(time.monotonic() - started, 2),
                })
            raise
        finally:
            depth_state.value = depth
            depth_state.execution_id = parent_execution_id
        finished_at = _utc_iso()

        if project_dir is None:
            # The tool may have created its own project dir during execute
            # (first call of a run) — attribute the finish if possible.
            project_dir = infer_project_dir(inputs)
        if project_dir is not None:
            cost = getattr(result, "cost_usd", None)
            emit_event(project_dir, {
                **base, "event": "finish",
                "output_path": str(output_path) if output_path else None,
                "success": getattr(result, "success", None),
                # NOTE: 0.0 is meaningful (ran for free) — only None is dropped.
                "cost_usd": cost if isinstance(cost, (int, float)) else None,
                "duration_s": round(time.monotonic() - started, 2),
            })
        if getattr(self, "emits_generation_receipt", False):
            # Deliberately outside any try/except: receipt failures are fatal.
            _write_generation_receipts(
                self, inputs, result,
                execution_id=execution_id,
                started_at=started_at,
                finished_at=finished_at,
            )
        return result

    wrapper._backlot_instrumented = True  # type: ignore[attr-defined]
    return wrapper


class BaseTool(ABC):
    """Abstract base class for all OpenMontage tools."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Auto-instrument every concrete execute() with Backlot events."""
        super().__init_subclass__(**kwargs)
        impl = cls.__dict__.get("execute")
        if impl is not None and not getattr(impl, "__isabstractmethod__", False):
            cls.execute = _instrument_execute(impl)

    # --- Identity (override in subclasses) ---
    name: str = ""
    version: str = "0.1.0"
    tier: ToolTier = ToolTier.CORE
    stability: ToolStability = ToolStability.EXPERIMENTAL
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    determinism: Determinism = Determinism.DETERMINISTIC
    runtime: ToolRuntime = ToolRuntime.LOCAL

    # --- Dependencies ---
    # For API tools, add "env:ENVVAR_NAME" to signal required API keys
    dependencies: list[str] = []
    install_instructions: str = ""

    # --- Capabilities ---
    capability: str = "generic"
    provider: str = "openmontage"
    capabilities: list[str] = []
    input_schema: dict = {}
    output_schema: dict = {}
    artifact_schema: dict = {}
    progress_schema: Optional[dict] = None
    supports: dict[str, Any] = {}
    best_for: list[str] = []
    not_good_for: list[str] = []
    provider_matrix: dict[str, Any] = {}

    # --- Resource & retry ---
    resource_profile: ResourceProfile = ResourceProfile()
    retry_policy: RetryPolicy = RetryPolicy()

    # --- Resume & idempotency ---
    resume_support: ResumeSupport = ResumeSupport.NONE
    idempotency_key_fields: list[str] = []

    # --- Side effects & fallback ---
    side_effects: list[str] = []
    fallback: Optional[str] = None
    fallback_tools: list[str] = []

    # --- Agent skills (Layer 3 references) ---
    # Names of installed agent skills in .agents/skills/ that teach the
    # underlying technology. The orchestrator uses these to load relevant
    # API knowledge when planning tool usage.
    agent_skills: list[str] = []

    # --- Verification ---
    user_visible_verification: list[str] = []

    # --- Provenance (PLAN §6) ---
    # When True, the execute() wrapper appends a generation receipt to the
    # owning project's generation-receipts.jsonl for every output path of a
    # successful call. Receipt failures raise. Tools must fill
    # ToolResult.metadata (model_endpoint / provider_request_id, or
    # generator_kind="local" + local_tool details) for the receipt to validate.
    emits_generation_receipt: bool = False

    # --- Optional telemetry / quality hints for the scoring engine ---
    # If set (0.0-1.0), lib/scoring.py uses these directly instead of falling
    # back to stability-based heuristics. Leave unset unless the tool has a
    # real measured or well-calibrated value.
    quality_score: Optional[float] = None
    historical_success_rate: Optional[float] = None
    latency_p50_seconds: Optional[float] = None

    # ---- Status reporting ----

    def get_status(self) -> ToolStatus:
        """Check if this tool's dependencies are satisfied."""
        try:
            self.check_dependencies()
            return ToolStatus.AVAILABLE
        except DependencyError:
            return ToolStatus.UNAVAILABLE

    def check_dependencies(self) -> None:
        """Verify all dependencies are installed. Raises DependencyError if not."""
        for dep in self.dependencies:
            if dep.startswith(("cmd:", "binary:")):
                prefix = "cmd:" if dep.startswith("cmd:") else "binary:"
                cmd_name = dep[len(prefix):]
                if shutil.which(cmd_name) is None:
                    raise DependencyError(
                        f"Command {cmd_name!r} not found. {self.install_instructions}"
                    )
            elif dep.startswith("env:"):
                env_name = dep[4:]
                if not os.environ.get(env_name):
                    raise DependencyError(
                        f"Environment variable {env_name!r} not set. {self.install_instructions}"
                    )
            elif dep.startswith("python:"):
                module_name = dep[7:]
                try:
                    __import__(module_name)
                except ImportError:
                    raise DependencyError(
                        f"Python module {module_name!r} not installed. {self.install_instructions}"
                    )

    def get_info(self) -> dict[str, Any]:
        """Return full tool contract info for registry/discovery."""
        usage_location = inspect.getfile(self.__class__)
        return {
            "name": self.name,
            "version": self.version,
            "tier": self.tier.value,
            "capability": self.capability,
            "provider": self.provider,
            "stability": self.stability.value,
            "status": self.get_status().value,
            "execution_mode": self.execution_mode.value,
            "determinism": self.determinism.value,
            "runtime": self.runtime.value,
            "module_path": self.__class__.__module__,
            "usage_location": usage_location,
            "dependencies": self.dependencies,
            "install_instructions": self.install_instructions,
            "capabilities": self.capabilities,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "artifact_schema": self.artifact_schema,
            "supports": self.supports,
            "best_for": self.best_for,
            "not_good_for": self.not_good_for,
            "provider_matrix": self.provider_matrix,
            "resource_profile": {
                "cpu_cores": self.resource_profile.cpu_cores,
                "ram_mb": self.resource_profile.ram_mb,
                "vram_mb": self.resource_profile.vram_mb,
                "disk_mb": self.resource_profile.disk_mb,
                "network_required": self.resource_profile.network_required,
            },
            "resume_support": self.resume_support.value,
            "side_effects": self.side_effects,
            "fallback": self.fallback,
            "fallback_tools": self.fallback_tools or ([self.fallback] if self.fallback else []),
            "agent_skills": self.agent_skills,
            "related_skills": self.agent_skills,
            "user_visible_verification": self.user_visible_verification,
            "quality_score": self.quality_score,
            "historical_success_rate": self.historical_success_rate,
            "latency_p50_seconds": self.latency_p50_seconds,
        }

    # ---- Cost estimation ----

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """Estimate cost in USD for the given inputs. Override for paid tools."""
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        """Estimate runtime in seconds. Override for long-running tools."""
        return 0.0

    # ---- Idempotency ----

    def idempotency_key(self, inputs: dict[str, Any]) -> str:
        """Compute a cache key from idempotency fields."""
        key_data = {k: inputs.get(k) for k in self.idempotency_key_fields}
        raw = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ---- Execution ----

    @abstractmethod
    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        """Run the tool. Subclasses must implement this."""
        ...

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Preflight check without side effects. Override for paid/publishing tools."""
        return {
            "tool": self.name,
            "estimated_cost_usd": self.estimate_cost(inputs),
            "estimated_runtime_seconds": self.estimate_runtime(inputs),
            "status": self.get_status().value,
            "would_execute": True,
        }

    # ---- CLI helper ----

    def run_command(
        self,
        cmd: list[str],
        *,
        timeout: Optional[int] = None,
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess command with standard error handling.

        On Windows, resolves .cmd/.bat wrappers (e.g. npx, npm) via
        shutil.which() so subprocess.run() can find them without shell=True.
        """
        resolved_cmd = list(cmd)
        if platform.system() == "Windows" and resolved_cmd:
            exe = shutil.which(resolved_cmd[0])
            if exe:
                resolved_cmd[0] = exe
        try:
            return subprocess.run(
                resolved_cmd,
                capture_output=True,
                text=True,
                # Force UTF-8 decoding. The default uses the OS locale (cp1252 on
                # Windows), which raises UnicodeDecodeError on a subprocess that
                # emits Unicode/emoji (e.g. Remotion's progress output), killing the
                # reader thread and potentially swallowing the real error text.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or str(exc)
            raise ToolCommandError(
                exc.returncode,
                exc.cmd,
                output=exc.output,
                stderr=exc.stderr,
                detail=detail,
            ) from exc


class ToolCommandError(subprocess.CalledProcessError):
    """CalledProcessError with stderr/stdout surfaced in str(error)."""

    def __init__(
        self,
        returncode: int,
        cmd: list[str],
        *,
        output: Optional[str] = None,
        stderr: Optional[str] = None,
        detail: str = "",
    ) -> None:
        super().__init__(returncode, cmd, output=output, stderr=stderr)
        self.detail = detail

    def __str__(self) -> str:
        base = super().__str__()
        if self.detail:
            return f"{base}\n{self.detail}"
        return base


class DependencyError(Exception):
    """Raised when a tool's dependency is not satisfied."""
    pass

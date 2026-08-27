"""Cost tracker core: estimate, reserve, reconcile, and persist to cost_log.json.

Implements the budget governance rules from the spec:
- Every paid operation produces a preflight estimate
- The orchestrator reserves estimated budget before execution
- Budget overruns trigger pauses (in warn/cap mode)
- Actual spend is reconciled when the tool finishes or fails
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from lib.config_model import BudgetMode


class EntryStatus(str, Enum):
    ESTIMATED = "estimated"
    RESERVED = "reserved"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class BudgetExceededError(Exception):
    """Raised when an operation would exceed the budget in cap mode."""
    pass


class ApprovalRequiredError(Exception):
    """Raised when an operation needs user approval before proceeding."""
    pass


class CostTracker:
    """Tracks estimated, reserved, and actual costs for a pipeline project."""

    def __init__(
        self,
        budget_total_usd: float = 10.0,
        reserve_pct: float = 0.10,
        single_action_approval_usd: float = 0.50,
        require_approval_for_new_paid_tool: bool = True,
        mode: BudgetMode = BudgetMode.WARN,
        cost_log_path: Optional[Path] = None,
    ) -> None:
        self.budget_total_usd = budget_total_usd
        self.reserve_pct = reserve_pct
        self.single_action_approval_usd = single_action_approval_usd
        self.require_approval_for_new_paid_tool = require_approval_for_new_paid_tool
        self.mode = mode
        self.cost_log_path = cost_log_path
        self.entries: list[dict[str, Any]] = []
        self._approved_tools: set[str] = set()

        if cost_log_path and cost_log_path.exists():
            self._load()

    # ---- Budget calculations ----

    @property
    def budget_reserved_usd(self) -> float:
        return sum(
            e.get("reserved_usd", 0.0)
            for e in self.entries
            if e["status"] == EntryStatus.RESERVED.value
        )

    @property
    def budget_spent_usd(self) -> float:
        return sum(
            e.get("actual_usd", 0.0)
            for e in self.entries
            if e["status"] in (EntryStatus.COMPLETED.value, EntryStatus.FAILED.value)
        )

    @property
    def budget_remaining_usd(self) -> float:
        return self.budget_total_usd - self.budget_spent_usd - self.budget_reserved_usd

    @property
    def usable_budget_usd(self) -> float:
        """Budget minus the reserve holdback."""
        holdback = self.budget_total_usd * self.reserve_pct
        return max(0.0, self.budget_remaining_usd - holdback)

    def cost_snapshot(self) -> dict[str, float]:
        return {
            "total_spent_usd": round(self.budget_spent_usd, 4),
            "total_reserved_usd": round(self.budget_reserved_usd, 4),
            "budget_remaining_usd": round(self.budget_remaining_usd, 4),
        }

    # ---- Core operations ----

    @contextlib.contextmanager
    def transaction(self):
        """D19 R2#15: every mutation is lock → reload → modify → atomic write.

        Two processes sharing one cost_log.json (a generation run and a judge
        call, or two runners) can no longer overwrite each other's entries
        or both pass the budget check on the same remaining amount. Without a
        cost_log_path (in-memory tracker) this is a no-op. Re-entrant."""
        if self.cost_log_path is None or getattr(self, "_in_txn", False):
            yield
            return
        self.cost_log_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.cost_log_path.with_name(self.cost_log_path.name + ".lock")
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._in_txn = True
            try:
                if self.cost_log_path.exists():
                    self._load()
                yield
                self._save()
            finally:
                self._in_txn = False
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def estimate(self, tool: str, operation: str, estimated_usd: float) -> str:
        """Record an estimate. Returns entry ID."""
        with self.transaction():
            return self._estimate(tool, operation, estimated_usd)

    def _estimate(self, tool: str, operation: str, estimated_usd: float) -> str:
        entry_id = self._new_id()
        self.entries.append({
            "id": entry_id,
            "tool": tool,
            "operation": operation,
            "status": EntryStatus.ESTIMATED.value,
            "estimated_usd": round(estimated_usd, 4),
            "reserved_usd": 0.0,
            "actual_usd": 0.0,
            "timestamp": self._now(),
        })
        self._save()
        return entry_id

    def reserve(self, entry_id: str) -> None:
        """Reserve budget for an estimated entry.

        Raises BudgetExceededError in cap mode, or ApprovalRequiredError
        when the action exceeds the single-action approval threshold.
        """
        with self.transaction():
            self._reserve(entry_id)

    def _reserve(self, entry_id: str) -> None:
        entry = self._find(entry_id)
        estimated = entry["estimated_usd"]

        # Check single-action approval threshold
        if estimated > self.single_action_approval_usd:
            if self.mode != BudgetMode.OBSERVE:
                raise ApprovalRequiredError(
                    f"Action costs ${estimated:.2f}, exceeds "
                    f"single-action threshold ${self.single_action_approval_usd:.2f}"
                )

        # Check new paid tool approval
        if self.require_approval_for_new_paid_tool and estimated > 0:
            if entry["tool"] not in self._approved_tools:
                if self.mode != BudgetMode.OBSERVE:
                    raise ApprovalRequiredError(
                        f"First paid use of tool {entry['tool']!r} requires approval"
                    )

        # Check budget
        if estimated > self.usable_budget_usd:
            message = (
                f"Reservation of ${estimated:.2f} exceeds usable budget "
                f"${self.usable_budget_usd:.2f}"
            )
            if self.mode == BudgetMode.CAP:
                raise BudgetExceededError(message)
            if self.mode == BudgetMode.WARN:
                entry["budget_warning"] = True
                entry["budget_warning_message"] = message

        entry["status"] = EntryStatus.RESERVED.value
        entry["reserved_usd"] = estimated
        entry["timestamp"] = self._now()
        self._save()

    def approve_tool(self, tool: str) -> None:
        """Mark a tool as approved for paid operations."""
        with self.transaction():
            self._approved_tools.add(tool)
            self._save()

    def reconcile(self, entry_id: str, actual_usd: float, success: bool = True) -> None:
        """Reconcile actual spend after tool execution."""
        with self.transaction():
            entry = self._find(entry_id)
            entry["status"] = EntryStatus.COMPLETED.value if success else EntryStatus.FAILED.value
            entry["actual_usd"] = round(actual_usd, 4)
            entry["reserved_usd"] = 0.0
            entry["timestamp"] = self._now()
            self._save()

    def refund(self, entry_id: str) -> None:
        """Cancel a reservation without executing."""
        with self.transaction():
            entry = self._find(entry_id)
            entry["status"] = EntryStatus.REFUNDED.value
            entry["reserved_usd"] = 0.0
            entry["timestamp"] = self._now()
            self._save()

    # ---- Reference-driven estimation ----

    def estimate_from_reference(
        self,
        video_analysis_brief: dict,
        target_duration_seconds: int,
        tool_plan: dict,
    ) -> dict:
        """Estimate production cost based on reference analysis + target duration.

        Args:
            video_analysis_brief: The VideoAnalysisBrief artifact from video analysis
            target_duration_seconds: How long the output video should be
            tool_plan: Which tools will be used for each asset type, e.g.:
                {
                    "image_generation": {"tool": "flux_fal", "cost_per_unit": 0.05},
                    "video_generation": {"tool": "kling_fal", "cost_per_unit": 0.30,
                                         "clip_duration_seconds": 5},
                    "tts": {"tool": "elevenlabs_tts", "cost_per_word": 0.00003},
                    "music": {"tool": "music_gen", "cost_per_track": 0.10},
                }

        Returns:
            Itemized cost breakdown with line items, total, sample cost, and assumptions.
        """
        structure = video_analysis_brief.get("structure_analysis", {})
        pacing = structure.get("pacing_profile", {})
        narration = video_analysis_brief.get("narration_transcript", {})
        ref_duration = video_analysis_brief.get("source", {}).get("duration_seconds", 60)
        pacing_style = pacing.get("pacing_style", "steady_educational")

        # ── Scene count estimation ──
        # Don't just scale linearly — use the PACING DENSITY from the reference.
        # A music video with 8 scenes in 162s has ~3 cuts/min.
        # Scaling to 60s should PRESERVE that cut rate, not reduce scene count.
        ref_scenes = structure.get("total_scenes", 8)
        if ref_duration > 0:
            cuts_per_minute = ref_scenes / (ref_duration / 60)
        else:
            cuts_per_minute = 4.0  # default: moderate pacing

        # Apply pacing-aware minimums (a fast-cut video doesn't become a slideshow)
        min_scenes_by_pacing = {
            "rapid_fire": 10,
            "dynamic_social": 8,
            "steady_educational": 5,
            "slow_contemplative": 3,
            "variable": 6,
        }
        min_scenes = min_scenes_by_pacing.get(pacing_style, 5)

        # Scene count = max(pacing-density-based, minimum for style)
        density_based_scenes = round(cuts_per_minute * (target_duration_seconds / 60))
        estimated_scenes = max(min_scenes, density_based_scenes)

        # ── Narration word count ──
        ref_word_count = narration.get("word_count", 0)
        if ref_duration > 0 and ref_word_count > 0:
            actual_wpm = (ref_word_count / ref_duration) * 60
        else:
            actual_wpm = 150  # default conversational pace
        estimated_words = round(actual_wpm * (target_duration_seconds / 60))

        # ── Motion ratio from reference ──
        scenes_list = structure.get("scenes", [])
        motion_ratio, motion_basis = self._estimate_motion_ratio(
            video_analysis_brief=video_analysis_brief,
            scenes_list=scenes_list,
            pacing_style=pacing_style,
        )

        estimated_motion_scenes = (
            max(1, round(estimated_scenes * motion_ratio))
            if motion_ratio > 0
            else 0
        )
        estimated_still_scenes = estimated_scenes - estimated_motion_scenes

        # ── Video clip coverage ──
        # Video gen tools produce clips of limited duration (typically 5-10s).
        # A 60s video with motion needs enough clips to COVER the duration,
        # not just 1 per scene.
        vid_plan = tool_plan.get("video_generation", {})
        clip_duration = vid_plan.get("clip_duration_seconds", 5) if vid_plan else 5
        motion_seconds = target_duration_seconds * motion_ratio
        clips_needed_for_coverage = max(
            estimated_motion_scenes,
            round(motion_seconds / clip_duration)
        ) if vid_plan else 0

        # ── Retry/waste buffer ──
        # Not every generation succeeds or looks good. Add a buffer.
        retry_multiplier = 1.3  # ~30% extra for retries and rejected outputs

        # ── Image count ──
        # Images per scene depends on visual variety needs:
        # - Explainer: 1-2 images per scene
        # - Music video / cinematic: 2-3 images per scene (mood shifts, variety)
        images_per_scene = 2.0 if pacing_style in ("dynamic_social", "rapid_fire") else 1.5
        estimated_images = max(
            estimated_scenes,
            round(estimated_scenes * images_per_scene)
        )

        # Build line items
        line_items = []
        assumptions = []

        assumptions.append(
            f"{estimated_scenes} scenes (reference has {cuts_per_minute:.1f} cuts/min, "
            f"pacing: {pacing_style})"
        )
        assumptions.append(motion_basis)

        # Image generation
        img_plan = tool_plan.get("image_generation", {})
        if img_plan:
            img_count = round(estimated_images * retry_multiplier)
            unit_cost = img_plan.get("cost_per_unit", 0.05)
            line_items.append({
                "category": "image_generation",
                "provider": img_plan.get("tool", "unknown"),
                "quantity": img_count,
                "unit_cost_usd": unit_cost,
                "total_usd": round(img_count * unit_cost, 4),
                "basis": (
                    f"~{images_per_scene:.0f} images/scene x {estimated_scenes} scenes "
                    f"+ {round((retry_multiplier - 1) * 100)}% retry buffer"
                ),
            })

        # Video generation
        if vid_plan and clips_needed_for_coverage > 0:
            clip_count = round(clips_needed_for_coverage * retry_multiplier)
            unit_cost = vid_plan.get("cost_per_unit", 0.30)
            line_items.append({
                "category": "video_generation",
                "provider": vid_plan.get("tool", "unknown"),
                "quantity": clip_count,
                "unit_cost_usd": unit_cost,
                "total_usd": round(clip_count * unit_cost, 4),
                "basis": (
                    f"{motion_seconds:.0f}s of motion / {clip_duration}s clips = "
                    f"{clips_needed_for_coverage} clips + retry buffer"
                ),
            })
            assumptions.append(
                f"{round(motion_ratio * 100)}% motion ratio → "
                f"{motion_seconds:.0f}s needs {clips_needed_for_coverage} clips "
                f"({clip_duration}s each)"
            )

        # TTS narration
        tts_plan = tool_plan.get("tts", {})
        if tts_plan and estimated_words > 10:
            cost_per_word = tts_plan.get("cost_per_word", 0.00003)
            tts_cost = round(estimated_words * cost_per_word, 4)
            line_items.append({
                "category": "tts_narration",
                "provider": tts_plan.get("tool", "unknown"),
                "quantity": estimated_words,
                "unit_cost_usd": cost_per_word,
                "total_usd": tts_cost,
                "basis": f"Narration at {round(actual_wpm)} WPM = ~{estimated_words} words",
            })
            assumptions.append(
                f"Narration at {round(actual_wpm)} WPM = ~{estimated_words} words "
                f"for {target_duration_seconds} seconds"
            )

        # Music
        music_plan = tool_plan.get("music", {})
        if music_plan:
            music_cost = music_plan.get("cost_per_track", 0.0)
            line_items.append({
                "category": "music",
                "provider": music_plan.get("tool", "unknown"),
                "quantity": 1,
                "unit_cost_usd": music_cost,
                "total_usd": music_cost,
                "basis": "1 background music track",
            })

        subtotal = round(sum(item["total_usd"] for item in line_items), 4)

        # ── Cost range instead of single number ──
        # Low: everything works first try. High: retry buffer fully consumed.
        low_total = round(subtotal / retry_multiplier, 4)
        high_total = round(subtotal * 1.15, 4)  # 15% above retry-buffered estimate

        # Sample cost: 2 scenes worth of assets (hook + 1 middle)
        sample_scenes = 2
        sample_fraction = sample_scenes / max(estimated_scenes, 1)
        sample_cost = round(subtotal * sample_fraction, 4)

        # Confidence based on how much data we have
        if scenes_list and narration.get("word_count", 0) > 0:
            confidence = "high"
        elif scenes_list or narration.get("word_count", 0) > 0:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "line_items": line_items,
            "total_usd": subtotal,
            "total_range_usd": {"low": low_total, "high": high_total},
            "sample_cost_usd": sample_cost,
            "confidence": confidence,
            "assumptions": assumptions,
            "estimated_scenes": estimated_scenes,
            "estimated_images": estimated_images,
            "estimated_clips": clips_needed_for_coverage,
            "estimated_words": estimated_words,
            "motion_ratio": round(motion_ratio, 2),
            "cuts_per_minute": round(cuts_per_minute, 1),
            "target_duration_seconds": target_duration_seconds,
        }

    def _estimate_motion_ratio(
        self,
        *,
        video_analysis_brief: dict,
        scenes_list: list[dict[str, Any]],
        pacing_style: str,
    ) -> tuple[float, str]:
        """Estimate how much of the target treatment truly needs motion."""
        motion_weights = {
            "animation": 1.0,
            "b_roll": 1.0,
            "stock_footage": 1.0,
            "product_shot": 0.9,
            "transition": 0.6,
            "screen_recording": 0.45,
            "talking_head": 0.35,
            "diagram": 0.25,
            "chart": 0.25,
            "text_card": 0.2,
        }
        classified_weights = [
            motion_weights[visual_type]
            for scene in scenes_list
            if (visual_type := scene.get("visual_type")) in motion_weights
        ]
        if classified_weights:
            ratio = sum(classified_weights) / len(classified_weights)
            unknown_count = max(0, len(scenes_list) - len(classified_weights))
            if unknown_count:
                fallback_ratio, _ = self._fallback_motion_ratio(
                    video_analysis_brief=video_analysis_brief,
                    pacing_style=pacing_style,
                )
                ratio = (
                    (sum(classified_weights) + fallback_ratio * unknown_count)
                    / len(scenes_list)
                )
                basis = (
                    "motion ratio blended from classified scene types and "
                    "reference-style fallback for unclassified scenes"
                )
            else:
                basis = "motion ratio derived from classified scene types"
            return round(min(max(ratio, 0.0), 0.95), 2), basis

        return self._fallback_motion_ratio(
            video_analysis_brief=video_analysis_brief,
            pacing_style=pacing_style,
        )

    def _fallback_motion_ratio(
        self,
        *,
        video_analysis_brief: dict,
        pacing_style: str,
    ) -> tuple[float, str]:
        """Fallback heuristic for motion ratio before scene vision enrichment."""
        source_type = video_analysis_brief.get("source", {}).get("type", "")
        replication = video_analysis_brief.get("replication_guidance", {})
        motion_required = bool(replication.get("motion_required"))
        suggested_pipeline = replication.get("suggested_pipeline", "")

        base_by_pacing = {
            "rapid_fire": 0.8,
            "dynamic_social": 0.65,
            "steady_educational": 0.35,
            "slow_contemplative": 0.2,
            "variable": 0.5,
        }
        ratio = base_by_pacing.get(pacing_style, 0.5)

        if source_type in ("shorts", "instagram", "tiktok"):
            ratio = max(ratio, 0.7)
        if motion_required:
            ratio = max(ratio, 0.6)
        if suggested_pipeline == "cinematic":
            ratio = max(ratio, 0.55)

        ratio = round(min(max(ratio, 0.1), 0.95), 2)
        basis = (
            "motion ratio inferred from pacing/style because scene visual types "
            "have not been enriched yet"
        )
        return ratio, basis

    # ---- Persistence ----

    def _save(self) -> None:
        if self.cost_log_path is None:
            return
        data = {
            "version": "1.0",
            "budget_total_usd": self.budget_total_usd,
            "budget_reserved_usd": round(self.budget_reserved_usd, 4),
            "budget_spent_usd": round(self.budget_spent_usd, 4),
            "approved_tools": sorted(self._approved_tools),
            "entries": self.entries,
        }
        self.cost_log_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cost_log_path.with_name(self.cost_log_path.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.cost_log_path)

    def _load(self) -> None:
        with open(self.cost_log_path) as f:  # type: ignore[arg-type]
            data = json.load(f)
        # Only entries/spend state are loaded. The cap is NOT: the constructor's
        # value comes from the receipt-bound project.yaml (or the caller), and a
        # historical cap persisted in cost_log.json must never override a
        # lowered, re-approved one (inspection #3).
        self.entries = data.get("entries", [])
        self._approved_tools = set(data.get("approved_tools", []))

    # ---- Helpers ----

    def _find(self, entry_id: str) -> dict[str, Any]:
        for entry in self.entries:
            if entry["id"] == entry_id:
                return entry
        raise KeyError(f"Cost entry {entry_id!r} not found")

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


# ---- Paid-call idempotency (PLAN §0) ----
#
# A reservation is persisted to <project_root>/cost-reservations.jsonl in state
# "submitting" BEFORE any network call. The provider request id is attached when
# it returns; reconciliation appends the terminal state. States:
#
#   submitting      — reserved; the request may or may not have reached the
#                     provider. With no request id it is indeterminate; with a
#                     request id the outcome is unknown (crash after attach).
#   pending_billing — the provider ACCEPTED the request (id persisted) but the
#                     tool failed afterwards (deadline, download, verification).
#                     The provider may still have completed and billed the job,
#                     so the reserved amount is retained, never refunded here.
#   completed / failed — terminal.
#
# ``resume_check`` halts on ANY nonterminal reservation; only the human-run
# scripts/reconcile_paid_calls.py (which polls the provider by request id and
# never resubmits) moves them to a terminal state.

RESERVATIONS_FILENAME = "cost-reservations.jsonl"
NONTERMINAL_STATES = ("submitting", "pending_billing")
TERMINAL_STATES = ("completed", "failed")
RESERVATION_STATES = NONTERMINAL_STATES + TERMINAL_STATES


class IndeterminatePaidCallError(Exception):
    """Raised on resume when paid calls have no recorded terminal outcome."""

    def __init__(self, reservations: list[dict[str, Any]]) -> None:
        self.reservations = reservations
        described = ", ".join(
            f"{r['reservation_id']} ({r.get('state')}, request id "
            f"{r.get('provider_request_id') or 'none'})"
            for r in reservations
        )
        super().__init__(
            f"{len(reservations)} paid call(s) have no terminal outcome: {described}. "
            f"Halt; run scripts/reconcile_paid_calls.py --project <slug> to reconcile "
            f"with the provider. Never resubmit."
        )


def reservations_path(project_root: Path) -> Path:
    return Path(project_root) / RESERVATIONS_FILENAME


def _append_reservation_event(project_root: Path, event: dict[str, Any]) -> None:
    from lib.state_io import append_jsonl

    event = dict(event)
    event.setdefault("at", datetime.now(timezone.utc).isoformat())
    append_jsonl(reservations_path(project_root), event)


def load_reservations(project_root: Path) -> dict[str, dict[str, Any]]:
    """Fold the reservation event log into the current state per reservation_id."""
    from lib.state_io import read_jsonl

    folded: dict[str, dict[str, Any]] = {}
    for event in read_jsonl(reservations_path(project_root)):
        rid = event.get("reservation_id")
        if not rid:
            continue
        current = folded.setdefault(rid, {})
        current.update({k: v for k, v in event.items() if k != "at"})
        current["updated_at"] = event.get("at")
    return folded


def reserve_paid_call(
    tracker: CostTracker,
    project_root: Path,
    *,
    tool: str,
    endpoint: str,
    normalized_inputs_hash: str,
    reserved_usd: float,
    output_hint: Optional[dict[str, Any]] = None,
) -> str:
    """Reserve budget through ``tracker`` and persist a ``submitting`` reservation.

    Raises the tracker's BudgetExceededError / ApprovalRequiredError before
    anything is written to the reservation log. Returns the reservation id,
    which doubles as the client idempotency key sent with the request.
    ``output_hint`` (e.g. ``{"kind": "video", "output_path": ...}`` or
    ``{"kind": "image", "objects_dir": ...}``) lets the offline reconciler
    recover a completed output without resubmitting.
    """
    entry_id = tracker.estimate(tool, endpoint, reserved_usd)
    try:
        tracker.reserve(entry_id)
    except Exception:
        tracker.refund(entry_id)
        raise
    reservation_id = str(uuid.uuid4())
    _append_reservation_event(
        project_root,
        {
            "reservation_id": reservation_id,
            "idempotency_key": reservation_id,
            "cost_entry_id": entry_id,
            "tool": tool,
            "endpoint": endpoint,
            "normalized_inputs_hash": normalized_inputs_hash,
            "reserved_usd": round(reserved_usd, 4),
            "state": "submitting",
            "provider_request_id": None,
            "output_hint": dict(output_hint) if output_hint else None,
        },
    )
    return reservation_id


def attach_request_id(project_root: Path, reservation_id: str, provider_request_id: str) -> None:
    if not provider_request_id:
        raise ValueError("provider_request_id must be non-empty")
    if reservation_id not in load_reservations(project_root):
        raise KeyError(f"unknown reservation {reservation_id}")
    _append_reservation_event(
        project_root,
        {"reservation_id": reservation_id, "provider_request_id": provider_request_id},
    )


def reconcile_paid_call(
    project_root: Path,
    reservation_id: str,
    actual_usd: float,
    state: str = "completed",
    tracker: Optional[CostTracker] = None,
) -> None:
    """Record the state of a paid call; also reconciles the tracker entry when given.

    ``completed`` / ``failed`` are terminal. ``pending_billing`` keeps the
    reserved amount charged against the budget (the tracker entry is settled
    at ``actual_usd``, which callers pass as the reserved amount) until the
    reconciler learns the provider's real outcome. A terminal reservation
    cannot be reconciled again.
    """
    if state not in ("completed", "failed", "pending_billing"):
        raise ValueError("state must be 'completed', 'failed' or 'pending_billing'")
    reservation = load_reservations(project_root).get(reservation_id)
    if reservation is None:
        raise KeyError(f"unknown reservation {reservation_id}")
    if reservation.get("state") in TERMINAL_STATES:
        raise ValueError(
            f"reservation {reservation_id} is already {reservation['state']}; refusing to re-reconcile"
        )
    if tracker is not None and reservation.get("cost_entry_id"):
        tracker.reconcile(reservation["cost_entry_id"], actual_usd, success=(state != "failed"))
    _append_reservation_event(
        project_root,
        {"reservation_id": reservation_id, "state": state, "actual_usd": round(actual_usd, 4)},
    )


def nonterminal_reservations(project_root: Path) -> list[dict[str, Any]]:
    """Every reservation not yet ``completed`` or ``failed`` (any ``submitting`` —
    with or without a request id — and every ``pending_billing``)."""
    return [
        r for r in load_reservations(project_root).values() if r.get("state") in NONTERMINAL_STATES
    ]


def indeterminate_reservations(project_root: Path) -> list[dict[str, Any]]:
    """Reservations still ``submitting`` with no provider request id: the only
    ones the reconciler cannot resolve by polling. Kept for callers that need
    to single these out; ``resume_check`` blocks on all nonterminal ones."""
    return [
        r
        for r in load_reservations(project_root).values()
        if r.get("state") == "submitting" and not r.get("provider_request_id")
    ]


def resume_check(project_root: Path) -> None:
    """Raise if any paid call lacks a terminal outcome.

    Incomplete generation WAL entries (a crash between staging an output and
    its receipt/ledger/terminal state) are replayed first; one whose output
    is missing raises ``lib.receipts.GenerationWalError`` and blocks new
    spend until it is recovered. Then any nonterminal reservation raises
    IndeterminatePaidCallError.
    """
    from lib.receipts import recover_generation_wal

    recover_generation_wal(project_root)
    pending = nonterminal_reservations(project_root)
    if pending:
        raise IndeterminatePaidCallError(pending)

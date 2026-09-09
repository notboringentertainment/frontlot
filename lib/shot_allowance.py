"""Supervised shot limits; ledger arithmetic and locking reused from 561d87f.

Allowance authority is the saved conversational brief, independent of pipeline
pins. No 1.6 migration or take approval is introduced.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
import math

VIDEO_KIND = "video"
RELEASED_STATE = "failed"


class ShotAllowanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShotGuard:
    shot_id: str
    kind: str
    inputs: Mapping

    @property
    def scope(self):
        return f"shot:{self.shot_id}"

    def check(self, project_root, estimated_usd, *, tool=None):
        from lib.supervised_production import read_brief, history
        from tools.cost_tracker import load_reservations
        brief = read_brief(project_root, self.shot_id)
        if brief is None or brief["stopped"]:
            raise ShotAllowanceError("Supervised shot is missing or stopped")
        if self.inputs.get('output_path'):
            output = Path(self.inputs['output_path'])
            output = output if output.is_absolute() else Path(project_root) / output
            if output.exists():
                raise ShotAllowanceError('Choose a new output path; preserve the existing take')
        if tool is not None and tool not in brief["allowed_tools"]:
            raise ShotAllowanceError("Tool exceeds the shot's agreed scope")
        for key in ("look_refs", "reference_manifest"):
            if self.inputs.get(key) != brief[key]:
                raise ShotAllowanceError("Selected reference scope changed; prepare a revised brief")
        expected = [(Path(project_root) / ref["path"]).resolve() for ref in brief["reference_manifest"]]
        actual = []
        for path in self.inputs.get("reference_image_paths", []):
            path = Path(path)
            actual.append((path if path.is_absolute() else Path(project_root) / path).resolve())
        if actual != expected or any(self.inputs.get(k) for k in
                ("reference_image_urls", "start_image_url", "end_image_url", "reference_video_url")):
            raise ShotAllowanceError("Paid call references differ from the saved reference selection")
        for key in ("spend_allowance_usd", "max_video_takes"):
            value = brief.get(key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ShotAllowanceError(f"Invalid {key}")
        if isinstance(estimated_usd, bool) or not isinstance(estimated_usd, (int, float)) or not math.isfinite(estimated_usd) or estimated_usd < 0:
            raise ShotAllowanceError("A finite nonnegative cost estimate is required")
        held, attempts = scope_usage(load_reservations(Path(project_root)), self.scope)
        # External imports carry reported costs, not invented generation receipts.
        # Deduplicate the same external asset when it is attached more than once.
        external = {}
        for row in history(project_root, self.shot_id):
            if row["kind"] == "take" and not row.get("reservation_id"):
                external[row["sha256"]] = row
        for row in external.values():
            if row["cost_usd"] is None:
                raise ShotAllowanceError("External take cost is unknown; resolve it before spending")
            held += row["cost_usd"]
            attempts += int(row["duration_seconds"] is not None)
        if self.kind == VIDEO_KIND and attempts + 1 > brief["max_video_takes"]:
            raise ShotAllowanceError("Shot video take allowance is exhausted")
        if round(held + estimated_usd, 4) > round(brief["spend_allowance_usd"], 4):
            raise ShotAllowanceError("Shot spending allowance is exhausted")


def resolve(project_root, inputs, *, kind):
    from lib.supervised_production import read_brief, shot_dir
    if not isinstance(inputs, Mapping):
        return None
    shot_id = inputs.get("shot_id")
    explicit = inputs.get("asset_class") == "supervised_shot"
    if not shot_id:
        if explicit:
            raise ShotAllowanceError("A supervised shot requires shot_id and a saved brief")
        return None
    try:
        shot_dir(project_root, shot_id)
    except ValueError as exc:
        if explicit:
            raise ShotAllowanceError(str(exc)) from exc
        return None
    # A malformed saved journal is not permission to revert to project-only funds.
    brief = read_brief(project_root, shot_id)
    if brief is None:
        if explicit:
            raise ShotAllowanceError("Prepare the supervised shot brief first")
        return None
    if kind not in ("image", "video"):
        raise ShotAllowanceError("A supervised paid call must declare image or video")
    return ShotGuard(shot_id, kind, inputs)


def check_call(project_root, inputs, *, kind, estimated_usd=None):
    guard = resolve(project_root, inputs, kind=kind)
    if guard is not None:
        guard.check(project_root, estimated_usd)
    return guard


def reservation_holds(reservation: Mapping[str, Any]) -> bool:
    """Whether a reservation still holds its dollars and (video) its attempt.

    Everything except a terminal ``failed`` holds — an unknown state included,
    because only a CONFIRMED non-acceptance may release (constraint 15).
    """
    return reservation.get("state") != RELEASED_STATE


def reservation_usd(reservation: Mapping[str, Any]) -> float:
    """What a held reservation costs the shot: the larger of what it reserved
    and what it actually settled at (a provider never bills less than nothing,
    and an estimate is never allowed to shrink after the fact)."""
    return max(_usd(reservation.get("reserved_usd")), _usd(reservation.get("actual_usd")))


def _usd(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(float(value), 0.0)


def scope_usage(
    reservations: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]], scope: str
) -> tuple[float, int]:
    """``(dollars held, video attempts held)`` for one scope, from the ledger."""
    rows = reservations.values() if isinstance(reservations, Mapping) else reservations
    held, attempts = 0.0, 0
    for reservation in rows:
        if reservation.get("scope") != scope or not reservation_holds(reservation):
            continue
        held += reservation_usd(reservation)
        if reservation.get("kind") == VIDEO_KIND:
            attempts += 1
    return round(held, 4), attempts

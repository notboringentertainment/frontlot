"""Per-shot dollar allowance and video take limit (plan 2026-09-08, C2).

A shot's record in the ``scene_plan`` checkpoint carries two limits:

``spend_allowance_usd``
    ONE dollar allowance shared by every paid call named for that shot —
    storyboard frames, video takes, any ancillary call.
``max_video_takes``
    Video generation attempts only. A storyboard call consumes dollars,
    never a take.

Authority (constraint 14). Both numbers are read from the shot's record in
the on-disk, PIN-BOUND ``scene_plan`` checkpoint — the same read the
generation boundary already performs (``_scene_plan_shot_index``) — and
never from the tool call's inputs. On the shot path under a shot-ready pin
(``lib.canon_enforcement.project_is_shot_ready``) a missing shot id, an
unresolvable shot record or missing/malformed limits REFUSE the call; there
is no fallback to project-only accounting. A project pinned below 1.6 keeps
today's project-cap-only behaviour: nothing here applies to it.

Counting (constraint 15, Codex revision-2). Cumulative spend and the attempt
count are rebuilt from the reservation ledger every time, never from memory,
so they survive a restart. A video attempt is consumed when a reservation
for a NEW video request is created; polling, resuming or reconciling an
existing job creates no reservation and consumes nothing. Dollars and the
attempt are released only when NON-ACCEPTANCE IS CONFIRMED, which is
precisely the terminal ``failed`` state:

    submitting        outstanding — the request may or may not have reached
                      the provider. A missing provider request id is NOT
                      proof that it did not: the reservation keeps its
                      dollars and its attempt, blocks the next call through
                      ``resume_check`` and routes to reconciliation.
    pending_billing   the provider accepted (id persisted) and the tool then
                      failed. Consumed: releasing nothing is the whole point
                      of the state.
    completed         consumed. A take the writer later rejects refunds
                      nothing — the ledger is the only counter, and the
                      asset manifest is never consulted here.
    failed            RELEASED. Only ``scripts/reconcile_paid_calls.py``
                      (the provider returned a definite rejection) or a
                      human recording a submission that demonstrably never
                      left the machine writes it. No tool writes ``failed``
                      after a request id is attached.

Changing the approved package does not reset money or attempts already
attributed to a shot id: the ledger rows stay, and a larger allowance or
take limit is a new D-A decision recorded on the shot record.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SCOPE_PREFIX = "shot:"
#: An ``asset_class`` that makes a call a shot call even before ``shot_id`` is read.
SHOT_ASSET_CLASSES = frozenset({"shot_visual", "storyboard_frame"})
VIDEO_KIND = "video"
IMAGE_KIND = "image"
CALL_KINDS = (VIDEO_KIND, IMAGE_KIND)
#: The one reservation state that proves non-acceptance and therefore releases.
RELEASED_STATE = "failed"


class ShotAllowanceError(RuntimeError):
    """A paid call named for a shot is outside that shot's approved allowance."""


def shot_scope(shot_id: str) -> str:
    """The reservation-ledger scope for one shot (``shot:<shot_id>``)."""
    return f"{SCOPE_PREFIX}{shot_id}"


def call_shot_id(inputs: Mapping[str, Any] | None) -> str | None:
    """The shot this paid call is named for, or None for non-shot material.

    A call whose ``asset_class`` is a shot class MUST name a shot id; so must
    any call that supplies ``shot_id`` at all. Anything else is non-shot and
    keeps today's project-cap-only behaviour.
    """
    if not isinstance(inputs, Mapping):
        return None
    asset_class = inputs.get("asset_class")
    raw = inputs.get("shot_id")
    named = isinstance(raw, str) and raw.strip() != ""
    if named:
        return raw.strip()
    if asset_class in SHOT_ASSET_CLASSES:
        raise ShotAllowanceError(
            f"a {asset_class!r} call must name inputs['shot_id']: the shot's approved "
            f"spend_allowance_usd and max_video_takes are read from its scene_plan record, "
            f"and there is no project-only fallback"
        )
    if raw is not None:
        raise ShotAllowanceError(
            f"inputs['shot_id'] must be a non-empty string naming a shot in the approved "
            f"scene_plan; got {raw!r}"
        )
    return None


def shot_record(project_root: Path | str, shot_id: str) -> dict[str, Any]:
    """The shot's record in the on-disk, pin-bound ``scene_plan`` checkpoint.

    Fails closed: an absent, unreadable, pin-mismatched or duplicate-shot plan
    is a refusal, never an unlimited allowance.

    KEEP THIS TRAVERSAL IN STEP WITH ``tools.video._shared._scene_plan_shot_index``,
    which walks the same ``artifacts.scene_plan.scenes[].shots[]`` path to
    decide whether a shot is a planned shot at all: a shot this function funds
    must be a shot that index knows, or the boundary funds a call it then
    refuses (or refuses a call it has already funded).
    """
    import lib.checkpoint as checkpoint_mod

    root = Path(project_root)
    try:
        checkpoint = checkpoint_mod.read_checkpoint(root.parent, root.name, "scene_plan")
    except Exception as exc:  # noqa: BLE001 — an unreadable plan authorizes no spend
        raise ShotAllowanceError(
            f"the scene_plan checkpoint of {root.name} cannot be read, so shot {shot_id!r} has no "
            f"approved allowance: {exc}"
        ) from exc
    if not checkpoint:
        raise ShotAllowanceError(
            f"{root.name} has no scene_plan checkpoint, so shot {shot_id!r} has no approved "
            f"spend_allowance_usd or max_video_takes — record the shot package (D-A) first"
        )
    plan = ((checkpoint.get("artifacts") or {}).get("scene_plan")) or {}
    found: dict[str, Any] | None = None
    for scene in plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict) or str(shot.get("shot_id")) != str(shot_id):
                continue
            if found is not None:
                raise ShotAllowanceError(
                    f"shot {shot_id!r} appears more than once in the scene_plan checkpoint; "
                    f"its allowance is ambiguous"
                )
            found = shot
    if found is None:
        raise ShotAllowanceError(
            f"shot {shot_id!r} has no record in the scene_plan checkpoint of {root.name}; "
            f"a paid call cannot be attributed to a shot the approved plan does not contain"
        )
    return found


def _limit(record: Mapping[str, Any], key: str, *, integral: bool) -> float:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int if integral else (int, float)):
        raise ShotAllowanceError(
            f"shot {record.get('shot_id')!r} has no usable {key} on its scene_plan record "
            f"(got {value!r}); the shot package (D-A) must record one before any paid call"
        )
    if value < 0:
        raise ShotAllowanceError(
            f"shot {record.get('shot_id')!r} has a negative {key} ({value!r}) on its scene_plan record"
        )
    return float(value)


@dataclass(frozen=True)
class ShotGuard:
    """The limits one shot's record grants, resolved once per paid call.

    Resolution (pin, checkpoint, receipts) happens outside the reservation
    lock; :meth:`check` is pure ledger arithmetic and is what runs under it.
    """

    shot_id: str
    kind: str
    spend_allowance_usd: float
    max_video_takes: int

    @property
    def scope(self) -> str:
        return shot_scope(self.shot_id)

    def check(self, project_root: Path | str, estimated_usd: float | None) -> None:
        """Refuse this call if it would exceed the shot's dollars or takes."""
        from tools.cost_tracker import load_reservations

        held, attempts = scope_usage(load_reservations(Path(project_root)), self.scope)
        if self.kind == VIDEO_KIND and attempts + 1 > self.max_video_takes:
            raise ShotAllowanceError(
                f"shot {self.shot_id!r} allows max_video_takes={self.max_video_takes} and "
                f"{attempts} video attempt(s) are already held by the reservation ledger; "
                f"a further take needs a new shot package (D-A), not a retry"
            )
        wanted = round(held + max(float(estimated_usd or 0.0), 0.0), 4)
        if wanted > round(self.spend_allowance_usd, 4):
            raise ShotAllowanceError(
                f"shot {self.shot_id!r} has spend_allowance_usd=${self.spend_allowance_usd:.4f} and "
                f"${held:.4f} is already spent or outstanding; this call would take it to "
                f"${wanted:.4f}. Raise the allowance with a new shot package (D-A)"
            )


def resolve(
    project_root: Path | str, inputs: Mapping[str, Any] | None, *, kind: str | None
) -> ShotGuard | None:
    """The guard governing this paid call, or None when C2 does not apply.

    None means one of two things, both of which keep today's behaviour: the
    project's signed pin is not shot-ready, or the call is not named for a
    shot. Everything else raises.
    """
    from lib.canon_enforcement import project_is_shot_ready

    root = Path(project_root)
    if not project_is_shot_ready(root):
        return None
    shot_id = call_shot_id(inputs)
    if shot_id is None:
        return None
    if kind not in CALL_KINDS:
        raise ShotAllowanceError(
            f"a paid call for shot {shot_id!r} must declare kind {CALL_KINDS}; got {kind!r}"
        )
    record = shot_record(root, shot_id)
    return ShotGuard(
        shot_id=shot_id,
        kind=kind,
        spend_allowance_usd=_limit(record, "spend_allowance_usd", integral=False),
        max_video_takes=int(_limit(record, "max_video_takes", integral=True)),
    )


def check_call(
    project_root: Path | str,
    inputs: Mapping[str, Any] | None,
    *,
    kind: str | None,
    estimated_usd: float | None = None,
) -> ShotGuard | None:
    """Resolve and check in one step (the pre-upload check in ``paid_call_context``)."""
    guard = resolve(project_root, inputs, kind=kind)
    if guard is not None:
        guard.check(project_root, estimated_usd)
    return guard


# ---- reading the ledger -----------------------------------------------------


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

"""D20.5 — neutral utilities shared by the per-character run commands
(``scripts/look_run.py``, ``scripts/headshot_run.py``, later ``sheet_run``).

ONLY what has no precondition profile lives here (Codex R1#17): resolving a
registered project root, holding the run lease, naming the approval command,
reading a gate request's state, appending a decision. Each command keeps its
own explicit validator (config version, pin, QC block); nothing here decides
whether a run may start.
"""
from __future__ import annotations

import json
import re
import shlex
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

REPO = Path(__file__).resolve().parent.parent
REQUEST_DIRNAME = ".gate-requests"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ENTITY_ID_RE = re.compile(r"^[a-z0-9-]+$")
REQUEST_STATES = ("missing", "pending", "done", "declined")


class RunError(RuntimeError):
    """A run command cannot proceed; the message names why."""


def resolve_project_root(slug: str, projects_dir: Path | str | None = None) -> Path:
    """``<PROJECTS_DIR>/<slug>``: registered (under the canonical projects
    root), a real directory (no symlink), carrying ``project.yaml``."""
    from lib.paths import PROJECTS_DIR

    if not isinstance(slug, str) or not ENTITY_ID_RE.match(slug):
        raise RunError(f"project slug {slug!r} must match [a-z0-9-]+")
    base = Path(projects_dir or PROJECTS_DIR)
    root = base / slug
    if root.is_symlink() or not root.is_dir():
        raise RunError(f"no registered project directory at {root} (or it is a symlink)")
    try:
        root.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise RunError(f"{root} is not under the projects root {base}") from exc
    if not (root / "project.yaml").is_file():
        raise RunError(f"{root} has no project.yaml")
    return root.resolve()


def require_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not ENTITY_ID_RE.match(entity_id):
        raise RunError(f"entity id {entity_id!r} must match [a-z0-9-]+")
    return entity_id


@contextmanager
def hold_lease(root: Path, config: Any) -> Iterator[Any]:
    """The project-wide run lease for the config's wall time (one runner per
    project at a time; a stale lease from a dead process is taken over)."""
    from lib import run_lease

    minutes = float((getattr(config, "data", None) or {}).get("wall_time_minutes") or 60)
    with run_lease.acquire(root, minutes) as lease:
        yield lease


def gate_command(root: Path, request_id: str) -> str:
    """The exact Terminal command that signs ``request_id``."""
    invocation = gate_invocation(root, request_id)
    return f"cd {shlex.quote(str(invocation['cwd']))} && {shlex.join(invocation['argv'])}"


def gate_invocation(root: Path, request_id: str) -> dict[str, Any]:
    """Return the one shared, argv-safe invocation for a gate signer.

    ``gate_sign.py`` is intentionally the public wrapper: it owns the lease
    around the interactive signer.  Callers must not substitute a run command
    or invoke ``gate_approve.py`` directly.
    """
    rid = validate_request_id(request_id)
    slug = root.name
    # Validate the slug as well as the request id before exposing it in a
    # shell-renderable command.  This mirrors resolve_project_root's grammar
    # without requiring a filesystem lookup from this neutral helper.
    if not ENTITY_ID_RE.match(slug):
        raise RunError(f"project slug {slug!r} must match [a-z0-9-]+")
    return {
        "cwd": REPO,
        "argv": [str(REPO / ".venv" / "bin" / "python"), "scripts/gate_sign.py", "--project", slug, "--request", rid],
    }


def request_id_for(prefix: str, entity_id: str, revision: int) -> str:
    """``<prefix>-<entity>-<rev>``, never truncated into a collision (inspection
    #3): a long entity id is shortened to a readable head plus a hash of the
    full id, and the revision is always intact."""
    import hashlib

    plain = f"{prefix}-{entity_id}-{revision}"
    if len(plain) <= 64:
        return plain
    digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:12]
    head = entity_id[: max(1, 64 - len(f"{prefix}--{digest}-{revision}"))]
    return f"{prefix}-{head}-{digest}-{revision}"


def validate_request_id(request_id: str) -> str:
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        raise RunError(f"invalid request_id {request_id!r}")
    return request_id


def request_paths(root: Path, request_id: str) -> dict[str, Path]:
    rid = validate_request_id(request_id)
    base = root / REQUEST_DIRNAME
    return {
        "pending": base / f"{rid}.json",
        "done": base / "done" / f"{rid}.json",
        "declined": base / "declined" / f"{rid}.json",
        "abandoned": base / "abandoned" / f"{rid}.json",
    }


def request_state(root: Path, request_id: str) -> str:
    """``missing | pending | done | declined | abandoned`` — where the gate
    left the request. ``abandoned`` is terminal: a pre-commit check failed
    after the one-use token was consumed; the id is never reused. A ``missing``
    request (the checkpoint-before-request crash window) is revalidated and
    republished under the SAME id. More than one location is a corrupted
    request directory and fails closed."""
    paths = request_paths(root, request_id)
    found = [state for state, p in paths.items() if p.is_file() and not p.is_symlink()]
    if not found:
        return "missing"
    if len(found) > 1:
        raise RunError(f"request {request_id!r} exists in more than one state {found}; refusing to guess")
    return found[0]


def read_request(root: Path, request_id: str) -> tuple[str, Optional[dict[str, Any]]]:
    """``(state, request)``; the request is None when missing."""
    state = request_state(root, request_id)
    if state == "missing":
        return state, None
    path = request_paths(root, request_id)[state]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunError(f"request {request_id!r} ({state}) is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("request_id") != request_id:
        raise RunError(f"request file for {request_id!r} does not carry that request_id")
    return state, data


def write_decision(
    root: Path, *, stage: str, category: str, subject: str, reason: str,
    selected: str = "recorded", label: Optional[str] = None, user_approved: bool = False,
) -> dict[str, Any]:
    """Append one decision to the project's cumulative ``decision_log.json``
    through the same validated merge the checkpoint writer uses. ``reason``
    is stored verbatim (a writer's gate note is quoted, never paraphrased)."""
    from lib.checkpoint import _merge_decision_log

    decision = {
        "decision_id": f"d-{stage}-{uuid.uuid4().hex[:8]}",
        "stage": stage,
        "category": category,
        "subject": subject,
        "options_considered": [{"option_id": selected, "label": label or selected, "score": 1.0, "reason": reason}],
        "selected": selected,
        "reason": reason,
        "user_visible": True,
        "user_approved": bool(user_approved),
    }
    _merge_decision_log(root.parent, root.name, {"decisions": [decision]})
    return decision

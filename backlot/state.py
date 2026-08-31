"""BoardState derivation — turn a project directory into renderable state.

Everything here is read-only and defensive: a malformed JSON file, a missing
artifact, or a half-written checkpoint must degrade the board, never crash it
(design principle: "never block, never break").
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from lib.events import read_events
from lib.paths import PROJECTS_DIR, REPO_ROOT  # single source of truth (env-overridable)

MEDIA_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
MEDIA_VIDEO_EXT = {".mp4", ".webm", ".mov"}
MEDIA_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg"}

# Directories inside a project we never scan for media (build noise).
SCAN_EXCLUDE = {"node_modules", ".git", "__pycache__", "history", ".cache"}

# Stages every pipeline shares (fallback rail when the manifest is unknown).
FALLBACK_STAGES = [
    "research", "proposal", "idea", "script", "scene_plan",
    "assets", "edit", "compose", "publish",
]

# How long (seconds) without filesystem activity before a board reads "idle".
LIVE_WINDOW_SECONDS = 5 * 60

# An in_progress stage with no filesystem activity for this long is flagged
# as possibly stalled (F-05: a wedged agent must be visible, not silent —
# heartbeat checkpoints and tool events both reset the clock).
STALL_WINDOW_SECONDS = 10 * 60


def _read_json(path: Path) -> Optional[dict]:
    """Read a JSON file, returning None on any failure."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _rel(project_dir: Path, path: Path) -> str:
    """Project-relative POSIX path for media URLs."""
    try:
        return path.resolve().relative_to(Path(project_dir).resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


# ---------------------------------------------------------------------------
# Pipeline / stages
# ---------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _load_pipeline_meta(pipeline_type: Optional[str]) -> dict[str, Any]:
    """Stage order + gate flags from the manifest; graceful fallback."""
    if pipeline_type and pipeline_type != "unknown":
        try:
            from lib.pipeline_loader import load_pipeline
            manifest = load_pipeline(pipeline_type)
            stages = [
                {
                    "name": s["name"],
                    "gated": bool(s.get("human_approval_default", False)),
                    "produces": [
                        str(name) for name in (s.get("produces") or [])
                        if isinstance(name, str) and name
                    ],
                }
                for s in manifest.get("stages", [])
                if isinstance(s, dict) and s.get("name")
            ]
            if stages:
                return {
                    "pipeline_type": pipeline_type,
                    "stages": stages,
                    "known": True,
                }
        except Exception:
            pass
    return {
        "pipeline_type": pipeline_type or "unknown",
        "stages": [{"name": s, "gated": False, "produces": []} for s in FALLBACK_STAGES],
        "known": False,
    }


def _resolve_artifact(project_dir: Path, value: Any) -> Optional[dict]:
    """Checkpoint artifacts may be inline dicts or path strings — resolve both.

    Path references are only followed INSIDE the project directory: a
    checkpoint must not be able to pull arbitrary JSON from elsewhere on
    disk onto the board (F-04).
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        p = Path(value)
        if not p.is_absolute():
            p = project_dir / value
        try:
            p.resolve().relative_to(Path(project_dir).resolve())
        except (ValueError, OSError):
            return None
        return _read_json(p)
    return None


def _collect_checkpoints(project_dir: Path) -> dict[str, dict]:
    """Current checkpoint per stage (raw dicts, unvalidated by design)."""
    out: dict[str, dict] = {}
    for path in sorted(project_dir.glob("checkpoint_*.json")):
        stage = path.stem[len("checkpoint_"):]
        data = _read_json(path)
        if data is not None:
            data["_mtime"] = path.stat().st_mtime
            out[stage] = data
    return out


def _collect_history(project_dir: Path) -> dict[str, list[dict]]:
    """Archived checkpoint versions per stage (oldest first)."""
    history_dir = project_dir / "history"
    out: dict[str, list[dict]] = {}
    if not history_dir.is_dir():
        return out
    for path in sorted(history_dir.glob("checkpoint_*.json")):
        m = re.match(r"checkpoint_(.+?)_\d", path.stem)
        stage = m.group(1) if m else path.stem[len("checkpoint_"):]
        data = _read_json(path)
        if data is not None:
            out.setdefault(stage, []).append(data)
    return out


def _build_stage_rail(
    pipeline_meta: dict,
    checkpoints: dict[str, dict],
    history: dict[str, list[dict]],
) -> list[dict]:
    """One entry per manifest stage with derived status + gate audit."""
    rail = []
    manifest_stage_names = {s["name"] for s in pipeline_meta["stages"]}
    for stage_def in pipeline_meta["stages"]:
        name = stage_def["name"]
        cp = checkpoints.get(name)
        versions = history.get(name, [])
        status = cp.get("status") if cp else "pending"
        entry: dict[str, Any] = {
            "name": name,
            "gated": stage_def["gated"],
            "produces": list(stage_def.get("produces") or []),
            "status": status or "pending",
            "timestamp": cp.get("timestamp") if cp else None,
            "review": cp.get("review") if cp else None,
            "cost_snapshot": cp.get("cost_snapshot") if cp else None,
            "error": cp.get("error") if cp else None,
            "human_approved": cp.get("human_approved") if cp else None,
            "partial_progress": (cp.get("metadata") or {}).get("partial_progress") if cp else None,
            "versions": len(versions) + (1 if cp else 0),
            # Chronological status trail (history + current) — powers replay.
            "history_entries": (
                [{"status": v.get("status"), "timestamp": v.get("timestamp")} for v in versions]
                + ([{"status": cp.get("status"), "timestamp": cp.get("timestamp")}] if cp else [])
            ),
        }
        # Gate audit: a gated stage that completed without ever passing
        # through awaiting_human (current or archived) was gate-skipped.
        if (
            stage_def["gated"]
            and cp is not None
            and cp.get("status") == "completed"
        ):
            saw_wait = any(v.get("status") == "awaiting_human" for v in versions)
            approved = bool(cp.get("human_approved"))
            entry["gate_skipped"] = not (saw_wait or approved)
        rail.append(entry)

    # Checkpoints for stages the manifest doesn't declare (legacy runs,
    # pipeline mismatch) still deserve a slot — at their canonical position
    # in the pipeline, not dangling after publish ("idea" belongs up front).
    canon = {name: i for i, name in enumerate(FALLBACK_STAGES)}
    for name, cp in checkpoints.items():
        if name in manifest_stage_names:
            continue
        entry = {
            "name": name,
            "gated": False,
            "produces": [
                str(artifact_name)
                for artifact_name in (cp.get("artifacts") or {})
                if isinstance(artifact_name, str) and artifact_name
            ],
            "status": cp.get("status") or "unknown",
            "timestamp": cp.get("timestamp"),
            "review": cp.get("review"),
            "cost_snapshot": cp.get("cost_snapshot"),
            "error": cp.get("error"),
            "human_approved": cp.get("human_approved"),
            "partial_progress": None,
            "versions": 1 + len(history.get(name, [])),
            "undeclared": True,
        }
        pos = canon.get(name)
        if pos is None:
            rail.append(entry)  # truly unknown name — end of rail
            continue
        insert_at = len(rail)
        for i, existing in enumerate(rail):
            existing_pos = canon.get(existing["name"])
            if existing_pos is not None and existing_pos > pos:
                insert_at = i
                break
        rail.insert(insert_at, entry)
    return rail


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

ARTIFACT_FILES = {
    "research_brief": "research_brief.json",
    "brief": "brief.json",
    "proposal_packet": "proposal_packet.json",
    "script": "script.json",
    "scene_plan": "scene_plan.json",
    "asset_manifest": "asset_manifest.json",
    "edit_decisions": "edit_decisions.json",
    "render_report": "render_report.json",
    "final_review": "final_review.json",
    "publish_log": "publish_log.json",
    "decision_log": "decision_log.json",
}


def _collect_artifacts(project_dir: Path, checkpoints: dict[str, dict]) -> dict[str, dict]:
    """Artifacts from artifacts/*.json, backfilled from checkpoint payloads."""
    artifacts: dict[str, dict] = {}
    art_dir = project_dir / "artifacts"
    for name, filename in ARTIFACT_FILES.items():
        data = _read_json(art_dir / filename)
        if data is not None:
            artifacts[name] = data
    # decision_log historically also lives at project root
    if "decision_log" not in artifacts:
        data = _read_json(project_dir / "decision_log.json")
        if data is not None:
            artifacts["decision_log"] = data
    # Backfill from checkpoint-embedded artifacts.
    for cp in checkpoints.values():
        embedded = cp.get("artifacts")
        if not isinstance(embedded, dict):
            continue
        for name, value in embedded.items():
            if name not in artifacts:
                resolved = _resolve_artifact(project_dir, value)
                if resolved is not None:
                    artifacts[name] = resolved
    return artifacts


# ---------------------------------------------------------------------------
# Storyboard join
# ---------------------------------------------------------------------------

def _resolve_asset_path(project_dir: Path, raw_path: str) -> Optional[Path]:
    """Manifest paths appear in several real-world flavors — try them all.

    Observed on disk: project-relative ("assets/images/x.png"),
    repo-relative ("projects/<id>/assets/images/x.png"), and absolute.
    """
    if not raw_path:
        return None
    p = Path(raw_path)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(project_dir / raw_path)
        candidates.append(REPO_ROOT / raw_path)
        # repo-relative with the project prefix repeated
        parts = p.parts
        if len(parts) > 2 and parts[0] == "projects":
            candidates.append(project_dir.parent / Path(*parts[1:]))
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _asset_entry(project_dir: Path, asset: dict) -> dict:
    """Normalize a manifest asset entry + resolve file existence.

    A file that resolves OUTSIDE the project directory is treated as
    not-servable (exists=False): /media only serves within the project, and
    a bare-filename fallback path would 404 or hit the wrong file.
    """
    raw_path = asset.get("path") or ""
    resolved = _resolve_asset_path(project_dir, raw_path)
    if resolved is not None:
        try:
            resolved.resolve().relative_to(Path(project_dir).resolve())
        except (ValueError, OSError):
            resolved = None
    file_path = resolved if resolved is not None else (project_dir / raw_path)
    exists = resolved is not None
    kind = asset.get("type") or ""
    if not kind and file_path.suffix:
        ext = file_path.suffix.lower()
        if ext in MEDIA_IMAGE_EXT:
            kind = "image"
        elif ext in MEDIA_VIDEO_EXT:
            kind = "video"
        elif ext in MEDIA_AUDIO_EXT:
            kind = "audio"
    # A visual is only *renderable* on the board if the file it points at is
    # actually a raster image or a video. Bespoke/atelier assets (type
    # "animation" pointing at a .tsx composition) exist on disk but can't be
    # thumbnailed — routing them to <img> yields a broken image. The board
    # falls back to a per-scene snapshot or the shot-spec placeholder instead.
    ext = file_path.suffix.lower()
    renderable = exists and ext in (MEDIA_IMAGE_EXT | MEDIA_VIDEO_EXT)
    return {
        "id": asset.get("id"),
        "type": kind,
        "scene_id": asset.get("scene_id"),
        "path": _rel(project_dir, file_path) if exists else raw_path,
        "exists": exists,
        "renderable": renderable,
        "prompt": asset.get("prompt"),
        "model": asset.get("model"),
        "source_tool": asset.get("source_tool"),
        "provider": asset.get("provider"),
        "cost_usd": asset.get("cost_usd"),
        "quality_score": asset.get("quality_score"),
        "duration_seconds": asset.get("duration_seconds"),
        "resolution": asset.get("resolution"),
    }


def _find_scene_snapshot(project_dir: Path, scene_id: str) -> Optional[dict]:
    """A per-scene review still, if the run wrote one.

    Atelier/animation scenes have no thumbnailable asset file, so the
    assets-stage snapshot (`snapshots/<scene_id>.png`) is what the filmstrip
    shows. Accept exact `<scene_id>.<ext>` and `<scene_id>_*.<ext>` forms.
    """
    snap_dir = project_dir / "snapshots"
    if not scene_id or not snap_dir.is_dir():
        return None
    try:
        for f in sorted(snap_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in MEDIA_IMAGE_EXT:
                continue
            stem = f.stem
            if stem == scene_id or stem.startswith(f"{scene_id}_"):
                return {
                    "id": f"snap_{scene_id}",
                    "type": "image",
                    "scene_id": scene_id,
                    "path": _rel(project_dir, f),
                    "exists": True,
                    "renderable": True,
                    "snapshot": True,
                }
    except OSError:
        return None
    return None


def _find_script_section(scene: dict, sections: list[dict]) -> Optional[dict]:
    """Join scene → script section by id, falling back to timing overlap."""
    sid = scene.get("script_section_id")
    if sid:
        for s in sections:
            if s.get("id") == sid:
                return s
    start = scene.get("start_seconds")
    end = scene.get("end_seconds")
    if start is None or end is None:
        return None
    best, best_overlap = None, 0.0
    for s in sections:
        s0, s1 = s.get("start_seconds"), s.get("end_seconds")
        if s0 is None or s1 is None:
            continue
        overlap = min(end, s1) - max(start, s0)
        if overlap > best_overlap:
            best, best_overlap = s, overlap
    return best


def _build_storyboard(
    project_dir: Path,
    artifacts: dict[str, dict],
    events: list[dict],
) -> Optional[dict]:
    """Scene cards: scene_plan × script × asset_manifest (+ live events)."""
    scene_plan = artifacts.get("scene_plan")
    if not scene_plan or not isinstance(scene_plan.get("scenes"), list):
        return None
    sections = (artifacts.get("script") or {}).get("sections") or []
    manifest_assets = (artifacts.get("asset_manifest") or {}).get("assets") or []

    def scene_key(value: Any) -> str:
        # 0 is a legitimate scene id — only None/absent collapses to "".
        return str(value) if value is not None else ""

    assets_by_scene: dict[str, list[dict]] = {}
    for asset in manifest_assets:
        if not isinstance(asset, dict):
            continue
        entry = _asset_entry(project_dir, asset)
        assets_by_scene.setdefault(scene_key(entry.get("scene_id")), []).append(entry)

    # A scene is "generating" if its most recent top-level event is an
    # unfinished start. Nested (depth>0) provider events inside a selector
    # call are skipped — the outer call's finish is the real completion.
    generating: dict[str, dict] = {}
    for ev in events:
        sid = ev.get("scene_id")
        if sid is None or ev.get("depth"):
            continue
        sid = scene_key(sid)
        if ev.get("event") == "start":
            generating[sid] = ev
        elif ev.get("event") in ("finish", "error"):
            generating.pop(sid, None)

    cards = []
    for scene in scene_plan["scenes"]:
        if not isinstance(scene, dict):
            continue
        sid = scene_key(scene.get("id"))
        section = _find_script_section(scene, sections)
        scene_assets = assets_by_scene.get(sid, [])
        visuals = [a for a in scene_assets if a["type"] in ("image", "video", "diagram", "animation")]
        audio = [a for a in scene_assets if a["type"] in ("audio", "narration", "music", "sfx")]
        # Only files that can actually be shown (raster/video) are takes; a
        # bespoke composition asset (.tsx animation) is real but not showable.
        renderable = [a for a in visuals if a.get("renderable")]
        # A raster/video asset whose FILE is missing stays as a "file missing"
        # indicator. But an asset that EXISTS yet can't be shown (a .tsx atelier
        # composition) is dropped — it falls back to a per-scene snapshot.
        missing = [a for a in visuals if not a.get("exists") and a["type"] in ("image", "video", "diagram")]
        active_visual = (
            renderable[-1] if renderable
            else missing[-1] if missing
            else _find_scene_snapshot(project_dir, sid)
        )
        cards.append({
            "id": sid,
            "type": scene.get("type"),
            "description": scene.get("description"),
            "start_seconds": scene.get("start_seconds"),
            "end_seconds": scene.get("end_seconds"),
            "duration_seconds": (
                max(0, (scene.get("end_seconds") or 0) - (scene.get("start_seconds") or 0))
                if scene.get("end_seconds") is not None and scene.get("start_seconds") is not None
                else None
            ),
            "hero_moment": bool(scene.get("hero_moment")),
            "shot_language": scene.get("shot_language"),
            "shot_intent": scene.get("shot_intent"),
            "framing": scene.get("framing"),
            "movement": scene.get("movement"),
            "narration": (section or {}).get("text"),
            "section_label": (section or {}).get("label"),
            "required_assets": scene.get("required_assets") or [],
            "visual": active_visual,
            "takes": renderable,
            "audio": audio,
            "generating": generating.get(sid) is not None,
            "generating_tool": (generating.get(sid) or {}).get("tool"),
        })

    total = scene_plan.get("metadata", {}).get("total_duration_seconds")
    if total is None and cards:
        ends = [c["end_seconds"] for c in cards if c["end_seconds"] is not None]
        total = max(ends) if ends else None
    return {
        "scenes": cards,
        "total_duration_seconds": total,
        "style_playbook": scene_plan.get("style_playbook"),
    }


# ---------------------------------------------------------------------------
# Media discovery
# ---------------------------------------------------------------------------

def _scan_media(project_dir: Path) -> dict[str, list[dict]]:
    """Discovered media files (renders, loose assets, snapshots)."""
    renders: list[dict] = []
    snapshots: list[dict] = []
    music: list[dict] = []

    renders_dir = project_dir / "renders"
    if renders_dir.is_dir():
        for f in sorted(renders_dir.iterdir()):
            if f.suffix.lower() in MEDIA_VIDEO_EXT and f.is_file():
                renders.append({"path": _rel(project_dir, f), "size": f.stat().st_size,
                                "mtime": f.stat().st_mtime})
    # Atelier heuristic: deliverables at project root.
    for f in sorted(project_dir.glob("*.mp4")):
        renders.append({"path": _rel(project_dir, f), "size": f.stat().st_size,
                        "mtime": f.stat().st_mtime, "at_root": True})
    for f in sorted(project_dir.glob("*.mp3")):
        music.append({"path": _rel(project_dir, f), "at_root": True})
    music_dir = project_dir / "assets" / "music"
    if music_dir.is_dir():
        for f in sorted(music_dir.iterdir()):
            if f.suffix.lower() in MEDIA_AUDIO_EXT:
                music.append({"path": _rel(project_dir, f)})

    for dirname in ("snapshots", "verify"):
        d = project_dir / dirname
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in MEDIA_IMAGE_EXT and f.is_file():
                    snapshots.append({"path": _rel(project_dir, f)})

    renders.sort(key=lambda r: r.get("mtime", 0), reverse=True)
    return {"renders": renders, "snapshots": snapshots, "music": music}


def _find_poster(project_dir: Path, state: dict) -> Optional[str]:
    """Best poster for the library card (image path, or a video path —
    the /thumb endpoint extracts a frame from videos)."""
    board = state.get("storyboard") or {}
    for card in board.get("scenes", []):
        visual = card.get("visual")
        if visual and visual.get("exists") and visual.get("type") == "image":
            return visual["path"]
    for snap in (state.get("media") or {}).get("snapshots", []):
        return snap["path"]
    # Common image homes, in order of how representative they usually are.
    for rel_dir in ("assets/images", "assets/frames", "exports", "assets", "."):
        d = (project_dir / rel_dir) if rel_dir != "." else project_dir
        if not d.is_dir():
            continue
        try:
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in MEDIA_IMAGE_EXT:
                    return _rel(project_dir, f)
        except OSError:
            continue
    # Last resort: the newest render — /thumb extracts a poster frame.
    renders = (state.get("media") or {}).get("renders", [])
    if renders:
        return renders[0]["path"]
    return None


def _last_activity(project_dir: Path) -> float:
    """Most recent mtime among state-bearing files (bounded scan)."""
    latest = 0.0
    try:
        candidates = list(project_dir.glob("checkpoint_*.json"))
        candidates.append(project_dir / "events.jsonl")
        art = project_dir / "artifacts"
        if art.is_dir():
            candidates.extend(art.glob("*.json"))
        for p in candidates:
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return latest


# ---------------------------------------------------------------------------
# Governed gate state (read-only; deliberately does not use receipt readers)
# ---------------------------------------------------------------------------

_GATE_STATES = ("pending", "done", "declined", "abandoned")
_GATE_DIRS = {"pending": Path("."), "done": Path("done"), "declined": Path("declined"), "abandoned": Path("abandoned")}
_MAX_GATE_ROWS = 200
_MAX_LEDGER_LINE_BYTES = 128 * 1024
_MAX_LEDGER_LOOKUP_BYTES = 8 * 1024 * 1024
_MAX_LEDGER_LOOKUP_ROWS = 20_000
_MAX_COST_LEDGER_BYTES = 4 * 1024 * 1024
_MAX_GATE_TREE_ENTRIES = 4_096
_MAX_GATE_TREE_DEPTH = 12
_MAX_GATE_TREE_WORK = 8_192
_MAX_GATE_REQUEST_BYTES = 128 * 1024
_MAX_GATE_REQUEST_TOTAL_BYTES = 4 * 1024 * 1024
_MAX_GATE_PUBLIC_STRING_CHARS = 4_096
_MAX_GATE_PUBLIC_LIST_ITEMS = 100
_MAX_GATE_DETAIL_JSON_BYTES = 2 * 1024 * 1024
_MAX_GATE_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_GATE_PROJECT_YAML_BYTES = 256 * 1024


def _lstat(path: Path) -> Optional[os.stat_result]:
    try:
        return path.lstat()
    except OSError:
        return None


def _gate_tree_error(root: Path) -> Optional[str]:
    """Return a fail-closed error for an unsafe or over-budget gate tree.

    We intentionally use lstat rather than resolve: gate request data is
    untrusted board input and following a symlink could turn a read-only board
    refresh into an arbitrary-file disclosure.
    """
    base = root / ".gate-requests"
    info = _lstat(base)
    if info is None:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "symlink in gate directory"
    if not stat.S_ISDIR(info.st_mode):
        return None
    # An output bound must never hide a corrupt request tree.  Conversely a
    # board refresh must not recursively consume unbounded attacker-controlled
    # input: exhaustion is itself a fail-closed gate error, never truncation.
    pending = [(base, 0)]
    entries_seen = work = 0
    while pending:
        directory, depth = pending.pop()
        work += 1
        if work > _MAX_GATE_TREE_WORK:
            return "gate directory scan budget exceeded"
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                entries_seen += 1
                work += 1
                if entries_seen > _MAX_GATE_TREE_ENTRIES or work > _MAX_GATE_TREE_WORK:
                    return "gate directory scan budget exceeded"
                try:
                    entry_info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(entry_info.st_mode):
                    return "symlink in gate directory"
                if stat.S_ISDIR(entry_info.st_mode):
                    if depth >= _MAX_GATE_TREE_DEPTH:
                        return "gate directory scan budget exceeded"
                    pending.append((Path(entry.path), depth + 1))
    return None


def _regular_json_files(directory: Path) -> list[Path]:
    """A small deterministic listing of regular JSON files, never following links."""
    info = _lstat(directory)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    out: list[Path] = []
    for entry in entries:
        info = _lstat(entry)
        if info is not None and stat.S_ISREG(info.st_mode) and entry.suffix == ".json":
            out.append(entry)
    return out


def _read_gate_request(path: Path, parsed_bytes: list[int]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read one bounded request object; over-budget input invalidates gates."""
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return None, None
    if info.st_size > _MAX_GATE_REQUEST_BYTES:
        return None, "gate request exceeds byte budget"
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_GATE_REQUEST_BYTES + 1)
        if len(raw) > _MAX_GATE_REQUEST_BYTES:
            return None, "gate request exceeds byte budget"
        if parsed_bytes[0] + len(raw) > _MAX_GATE_REQUEST_TOTAL_BYTES:
            return None, "gate request aggregate byte budget exceeded"
        parsed_bytes[0] += len(raw)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, None
    return (data if isinstance(data, dict) else None), None


def _gate_public_field_error(request: dict[str, Any]) -> Optional[str]:
    """Reject oversized values before they can become board JSON output."""
    for name in (
        "request_id", "kind", "stage", "scope", "entity_id", "summary",
        "approval_receipt_id", "declined_note",
    ):
        value = request.get(name)
        if isinstance(value, str) and len(value) > _MAX_GATE_PUBLIC_STRING_CHARS:
            return "gate request public string exceeds budget"
        if isinstance(value, list):
            if len(value) > _MAX_GATE_PUBLIC_LIST_ITEMS:
                return "gate request public list exceeds budget"
            if any(not isinstance(item, str) or len(item) > _MAX_GATE_PUBLIC_STRING_CHARS for item in value):
                return "gate request public list is invalid"
        elif value is not None and not isinstance(value, str):
            return "gate request public field is invalid"
    return None


def _gate_rows(root: Path, slug: str, *, limit: Optional[int] = _MAX_GATE_ROWS) -> dict[str, Any]:
    """Read bounded request summaries; malformed files are simply not rows."""
    tree_error = _gate_tree_error(root)
    if tree_error:
        return {"error": tree_error, "requests": []}
    from lib.run_common import RunError, gate_command, validate_request_id

    base = root / ".gate-requests"
    found: dict[str, list[tuple[str, Path, dict[str, Any]]]] = {}
    parsed_bytes = [0]
    for state_name, relative in _GATE_DIRS.items():
        for path in _regular_json_files(base / relative):
            request, request_error = _read_gate_request(path, parsed_bytes)
            if request_error:
                return {"error": request_error, "requests": []}
            try:
                request_id = validate_request_id(path.stem)
            except RunError:
                continue
            if request is None or request.get("request_id") != request_id or request.get("project_id") != slug:
                continue
            public_error = _gate_public_field_error(request)
            if public_error:
                return {"error": public_error, "requests": []}
            found.setdefault(request_id, []).append((state_name, path, request))

    rows: list[dict[str, Any]] = []
    for request_id, copies in sorted(found.items()):
        state_name, path, request = copies[0]
        row: dict[str, Any] = {
            "request_id": request_id,
            "kind": request.get("kind"),
            "stage": request.get("stage"),
            "scope": request.get("scope"),
            "entity_id": request.get("entity_id"),
            "summary": request.get("summary"),
            "state": "conflict" if len(copies) > 1 else state_name,
            "mtime": _lstat(path).st_mtime if _lstat(path) else None,
        }
        if request.get("approval_receipt_id") is not None:
            row["approval_receipt_id"] = request.get("approval_receipt_id")
        if request.get("declined_note") is not None:
            row["declined_note"] = request.get("declined_note")
        if row["state"] == "pending":
            # gate_command is itself rendered solely from gate_invocation.
            row["next_command"] = gate_command(root, request_id)
        rows.append(row)
    priority = {"pending": 0, "conflict": 1, "done": 2, "declined": 3, "abandoned": 4}
    rows.sort(key=lambda row: (priority.get(str(row.get("state")), 99), str(row.get("request_id"))))
    visible = rows if limit is None else rows[:limit]
    return {
        "requests": visible,
        "truncated": limit is not None and len(rows) > limit,
        "total_requests": len(rows),
    }


def _canon_summary(root: Path) -> list[dict[str, str]]:
    """Approved hero and character-sheet objects from canon_view's pure plan."""
    try:
        from lib.canon_view import CHARACTER_ROLES, plan_view
        plan = plan_view(root)
    except Exception:
        return []
    allowed = {"hero", *CHARACTER_ROLES}
    rows: list[dict[str, str]] = []
    for link_rel, object_rel in plan:
        parts = Path(link_rel).parts
        if len(parts) < 2:
            continue
        entity, role = parts[-2], Path(parts[-1]).stem
        if role in allowed:
            rows.append({"entity": entity, "role": role, "object_rel": object_rel})
    return rows


def _looks_summary(root: Path) -> list[dict[str, Any]]:
    """Look packet text only: no ticket or look images are served by state."""
    checkpoint = _read_json(root / "checkpoint_look_lock.json") or {}
    packet = ((checkpoint.get("artifacts") or {}).get("look_packet") or {})
    looks = packet.get("looks") if isinstance(packet, dict) else []
    out = []
    for entry in looks or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("entity_id") and entry.get("look_hash"):
            out.append({
                "entity": entry.get("entity_id"),
                "entity_kind": entry.get("entity_kind"),
                "look_hash": entry.get("look_hash"),
                "source_ticket_ref": entry.get("source_ticket_ref"),
            })
    return out


def _cost_summary(root: Path, checkpoint_cost: Any) -> Any:
    """Authored-film reads its persisted cost ledger; old projects keep snapshots."""
    project_yaml = root / "project.yaml"
    yaml_info = _lstat(project_yaml)
    if yaml_info is not None and stat.S_ISREG(yaml_info.st_mode) and yaml_info.st_size > _MAX_GATE_PROJECT_YAML_BYTES:
        return {"error": "project.yaml exceeds byte budget"}
    text = ""
    try:
        with project_yaml.open("rb") as fh:
            raw = fh.read(_MAX_GATE_PROJECT_YAML_BYTES + 1)
        if len(raw) > _MAX_GATE_PROJECT_YAML_BYTES:
            return {"error": "project.yaml exceeds byte budget"}
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        pass
    authored = "authored-film" in text
    if not authored:
        for checkpoint in root.glob("checkpoint_*.json"):
            data = _read_json(checkpoint) or {}
            authored = data.get("pipeline_type") == "authored-film" or (data.get("pipeline") or {}).get("name") == "authored-film"
            if authored:
                break
    if not authored:
        if not isinstance(checkpoint_cost, dict):
            return checkpoint_cost
        return dict(checkpoint_cost, label="snapshot", source="snapshot")
    ledger_path = root / "cost_log.json"
    ledger_info = _lstat(ledger_path)
    if ledger_info is not None and ledger_info.st_size > _MAX_COST_LEDGER_BYTES:
        return {"label": "ledger", "source": "ledger", "error": "cost ledger too large"}
    ledger = _read_json(ledger_path)
    if ledger is None:
        return {"label": "ledger", "source": "ledger", "total_spent_usd": 0.0, "total_reserved_usd": 0.0}
    entries = ledger.get("entries") or []
    if not isinstance(entries, list):
        entries = []

    def amount(value: Any) -> float:
        try:
            parsed = float(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return parsed if math.isfinite(parsed) else 0.0

    spent = sum(amount(e.get("actual_usd")) for e in entries if isinstance(e, dict) and e.get("status") in ("completed", "failed"))
    reserved = sum(amount(e.get("reserved_usd")) for e in entries if isinstance(e, dict) and e.get("status") == "reserved")
    return {"label": "ledger", "source": "ledger", "total_spent_usd": round(spent, 4), "total_reserved_usd": round(reserved, 4)}


def _lease_summary(root: Path) -> dict[str, Any]:
    """Read a lease without acquiring, touching, or reclaiming it."""
    from lib import run_lease

    path = run_lease.lease_path(root)
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return {"held": False, "pid": None, "started": None}
    record = _read_json(path) or {}
    held = bool(record) and not run_lease.owner_is_dead(record)
    return {"held": held, "pid": record.get("pid"), "started": record.get("acquired_at")}


def _gates_state(project_dir: Path, checkpoint_cost: Any) -> Any:
    """The optional governed-project extension to BoardState."""
    if not (project_dir / "project.yaml").is_file():
        return None
    try:
        from lib.run_common import resolve_project_root
        root = resolve_project_root(project_dir.name, projects_dir=project_dir.parent)
    except Exception:
        # Fixed literal: RunError text can carry absolute filesystem paths,
        # which must not reach board JSON.
        return {"error": "invalid governed project", "requests": []}
    gates = _gate_rows(root, root.name)
    if gates.get("error"):
        return gates
    gates.update({
        "canon": _canon_summary(root),
        "looks": _looks_summary(root),
        "cost": _cost_summary(root, checkpoint_cost),
        "run_lease": _lease_summary(root),
    })
    return gates


def _raw_jsonl_targets(path: Path, receipt_ids: set[str]) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """Stream target receipt rows without ledger replay or materialization.

    The scan is complete (a requested id may be anywhere in a mature ledger),
    memory is O(the requested id set), and malformed or overlong physical rows
    are discarded one-at-a-time before scanning continues at the next newline.
    """
    wanted = {receipt_id for receipt_id in receipt_ids if isinstance(receipt_id, str) and receipt_id}
    if not wanted:
        return {}, None
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return {}, None
    if info.st_size > _MAX_LEDGER_LOOKUP_BYTES:
        return {}, "ledger lookup exceeds byte budget"
    found: dict[str, dict[str, Any]] = {}
    try:
        with path.open("rb") as fh:
            rows_seen = bytes_read = 0
            while wanted - found.keys():
                line = fh.readline(_MAX_LEDGER_LINE_BYTES + 1)
                if not line:
                    break
                rows_seen += 1
                bytes_read += len(line)
                if rows_seen > _MAX_LEDGER_LOOKUP_ROWS or bytes_read > _MAX_LEDGER_LOOKUP_BYTES:
                    return {}, "ledger lookup exceeds row or byte budget"
                if len(line) > _MAX_LEDGER_LINE_BYTES:
                    # A physical row above the cap is untrustworthy for any
                    # requested receipt.  Drain bounded chunks only to close
                    # the descriptor; do not silently skip potential evidence.
                    while line and not line.endswith(b"\n"):
                        line = fh.readline(_MAX_LEDGER_LINE_BYTES + 1)
                    return {}, "ledger row exceeds byte budget"
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, TypeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("receipt_id") in wanted:
                    found[row["receipt_id"]] = row
    except OSError:
        return {}, "ledger lookup unreadable"
    return found, None


def _read_gate_json(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read one lazy evidence JSON object within the detail byte budget."""
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return None, "evidence JSON unavailable"
    if info.st_size > _MAX_GATE_DETAIL_JSON_BYTES:
        return None, "evidence JSON exceeds byte budget"
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_GATE_DETAIL_JSON_BYTES + 1)
        if len(raw) > _MAX_GATE_DETAIL_JSON_BYTES:
            return None, "evidence JSON exceeds byte budget"
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, "evidence JSON unreadable or malformed"
    if not isinstance(data, dict):
        return None, "evidence JSON root is malformed"
    return data, None


def _safe_project_file(root: Path, path: Path) -> Optional[Path]:
    """Return a project-confined regular path with no symlink component."""
    try:
        root_real = root.resolve()
        candidate = path if path.is_absolute() else root_real / path
        # Check lexical components before resolve: resolving first erases an
        # in-project symlink and would diverge from pathsafe.resolve_input(),
        # which the signer uses for candidate ImageRefs.
        relative = candidate.relative_to(root_real)
    except ValueError:
        return None
    current = root_real
    for component in relative.parts:
        if component in ("", ".", ".."):
            return None
        current /= component
        info = _lstat(current)
        if info is None or stat.S_ISLNK(info.st_mode):
            return None
    try:
        candidate.resolve().relative_to(root_real)
    except (OSError, ValueError):
        return None
    return candidate


def _file_packet(root: Path, asset_id: Any, *, path: Optional[Path] = None, label: Optional[str] = None) -> dict[str, Any]:
    """One visual descriptor; vault IDs are only accepted when bytes match."""
    expected = asset_id if isinstance(asset_id, str) else None
    requested = path or (root / "canon" / "visual" / "objects" / f"{expected}.png")
    file = _safe_project_file(root, requested)
    result: dict[str, Any] = {"asset_id": expected, "path": _rel(root, file or requested), "label": label}
    if file is None:
        result["error"] = "hash mismatch"
        return result
    info = _lstat(file)
    if info is None or not stat.S_ISREG(info.st_mode):
        result["error"] = "hash mismatch"
        return result
    if info.st_size > _MAX_GATE_IMAGE_BYTES:
        result["error"] = "image exceeds byte budget"
        return result
    try:
        digest = hashlib.sha256()
        total = 0
        with file.open("rb") as fh:
            while True:
                chunk = fh.read(min(1024 * 1024, _MAX_GATE_IMAGE_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_GATE_IMAGE_BYTES:
                    result["error"] = "image exceeds byte budget"
                    return result
                digest.update(chunk)
        actual = digest.hexdigest()
    except OSError:
        result["error"] = "hash mismatch"
        return result
    result["sha256"] = actual
    if expected is None or actual != expected:
        result["error"] = "hash mismatch"
    return result


def _request_for_detail(root: Path, request_id: str) -> tuple[str, Optional[dict[str, Any]], Optional[Path]]:
    """A validated request state for lazy detail, retaining archival data."""
    gate = _gate_rows(root, root.name, limit=None)
    if gate.get("error"):
        return "error", None, None
    for row in gate["requests"]:
        if row["request_id"] != request_id:
            continue
        state_name = row["state"]
        if state_name == "conflict":
            return state_name, None, None
        path = root / ".gate-requests" / _GATE_DIRS[state_name] / f"{request_id}.json"
        request, request_error = _read_gate_request(path, [0])
        return ("error", None, path) if request_error or request is None else (state_name, request, path)
    existing = []
    for state_name, relative in _GATE_DIRS.items():
        path = root / ".gate-requests" / relative / f"{request_id}.json"
        info = _lstat(path)
        if info is not None and stat.S_ISREG(info.st_mode):
            existing.append(path)
    if existing:
        return "error", None, existing[0]
    return "missing", None, None


def _visuals_from_refs(root: Path, refs: list[tuple[str, Any]]) -> dict[str, Any]:
    visuals = []
    error = False
    for role, ref in refs:
        if not isinstance(ref, dict):
            continue
        visual = _file_packet(root, ref.get("asset_id"), label=role)
        visuals.append(visual)
        error |= bool(visual.get("error"))
    return {"visuals": visuals, "packet_error": error}


def _bible(root: Path) -> dict[str, Any]:
    checkpoint, _error = _read_gate_json(root / "checkpoint_visual_bible.json")
    checkpoint = checkpoint or {}
    data = (checkpoint.get("artifacts") or {}).get("visual_bible") or {}
    return data if isinstance(data, dict) else {}


def _render_headshot(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: checkpoint_headshots.json headshot_packet.characters[].candidates."""
    envelope = req.get("envelope") if isinstance(req.get("envelope"), dict) else {}
    if envelope.get("action") == "retire":
        receipt_id = envelope.get("supersedes_receipt_id")
        rows, lookup_error = _raw_jsonl_targets(
            root / "approvals.jsonl", {receipt_id},
        ) if isinstance(receipt_id, str) else ({}, None)
        row = rows.get(receipt_id) if isinstance(receipt_id, str) else None
        record = row.get("record") if isinstance(row, dict) and isinstance(row.get("record"), dict) else {}
        valid = bool(
            isinstance(row, dict)
            and row.get("kind") == "headshot"
            and row.get("action") == "activate"
            and row.get("project_id") == root.name
            and row.get("entity_id") == req.get("entity_id")
            and record.get("entity_id") == req.get("entity_id")
            and record.get("look_hash") == envelope.get("look_hash")
        )
        visual = _file_packet(root, record.get("asset_id"), label="current approved hero")
        error = lookup_error or (None if valid else "active headshot receipt unavailable or malformed")
        packet_error = bool(error) or bool(visual.get("error"))
        result = {
            "action": "retire",
            "supersedes_receipt_id": receipt_id,
            "look_hash": envelope.get("look_hash"),
            "visual": visual,
            "packet_error": packet_error,
        }
        if error:
            result["error"] = error
        return result
    checkpoint, checkpoint_error = _read_gate_json(root / "checkpoint_headshots.json")
    if checkpoint is None:
        return {"packet_error": True, "error": checkpoint_error or "headshot checkpoint unreadable", "candidates": []}
    artifacts = checkpoint.get("artifacts")
    if not isinstance(artifacts, dict):
        return {"packet_error": True, "error": "headshot checkpoint malformed", "candidates": []}
    packet = artifacts.get("headshot_packet")
    if not isinstance(packet, dict):
        return {"packet_error": True, "error": "headshot packet incomplete", "candidates": []}
    entry = next((x for x in packet.get("characters") or [] if isinstance(x, dict) and x.get("entity_id") == req.get("entity_id")), None)
    if not isinstance(entry, dict) or not isinstance(entry.get("candidates"), list):
        return {"packet_error": True, "error": "headshot packet incomplete", "candidates": []}
    candidates = []
    bad = False
    for number, candidate in enumerate(entry["candidates"], 1):
        if not isinstance(candidate, dict):
            bad = True
            continue
        asset_id = candidate.get("asset_id") or candidate.get("normalized_pixel_hash")
        stored_path = candidate.get("path")
        path = Path(stored_path) if isinstance(stored_path, str) and stored_path else None
        visual = _file_packet(root, asset_id, path=path, label=f"candidate {number}")
        bad |= bool(visual.get("error"))
        candidates.append({"number": number, "generator_kind": (candidate.get("provenance") or {}).get("generator_kind"),
                           "sha_prefix": str(asset_id or "")[:12], "visual": visual})
    rejected = next(
        (entry.get(name) for name in ("rejection_notes", "candidates_rejected", "rejected_candidates")
         if isinstance(entry.get(name), list)),
        [],
    )
    return {"candidates": candidates, "rejected_count": len(rejected), "packet_error": bad}


def _render_sheet(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: draft visual_bible character sheet + raw QC JSONL."""
    revision = req.get("revision") or (req.get("approval_record") or {}).get("sheet_revision")
    entry = next((x for x in _bible(root).get("characters") or [] if isinstance(x, dict) and x.get("id") == req.get("entity_id") and x.get("sheet_revision") == revision), None)
    if not isinstance(entry, dict):
        return {"packet_error": True, "error": "matching sheet revision unavailable", "visuals": []}
    sheet = entry.get("sheet")
    qc_refs = entry.get("qc_receipts")
    if not isinstance(sheet, dict) or not isinstance(qc_refs, dict):
        return {"packet_error": True, "error": "sheet or cited QC evidence is malformed", "visuals": []}
    refs = [(role, ref) for role, ref in sheet.items()]
    result = _visuals_from_refs(root, refs)
    wanted_qc = {str(receipt_id) for receipt_id in qc_refs.values() if isinstance(receipt_id, str)}
    rows, lookup_error = _raw_jsonl_targets(root / "qc-receipts.jsonl", wanted_qc)
    qc_rows = []
    cited_error = False
    for role, receipt_id in qc_refs.items():
        row = rows.get(str(receipt_id)) if isinstance(receipt_id, str) else None
        ref = sheet.get(role)
        expected_asset_id = ref.get("asset_id") if isinstance(ref, dict) else None
        valid = bool(
            isinstance(row, dict)
            and row.get("receipt_id") == receipt_id
            and row.get("project_id") == root.name
            and row.get("entity_id") == req.get("entity_id")
            and row.get("role") == role
            and row.get("asset_id") == expected_asset_id
            and row.get("verdict") in {"pass", "fail"}
        )
        cited_error |= not valid
        qc_rows.append({"role": role, "label": "unverified QC row", "row": row})
    result["qc_rows"] = qc_rows
    if lookup_error or cited_error:
        result.update({
            "packet_error": True,
            "error": lookup_error or "cited QC receipt unavailable or malformed",
        })
    return result


def _render_qc_override(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: raw qc-receipts.jsonl verdict selected by request qc_receipt_id."""
    rid = req.get("qc_receipt_id")
    rows, lookup_error = _raw_jsonl_targets(root / "qc-receipts.jsonl", {rid}) if isinstance(rid, str) else ({}, None)
    row = rows.get(rid) if isinstance(rid, str) else None
    visual = _file_packet(root, (row or {}).get("asset_id"), label="judged asset")
    return {"qc_row": row, "visual": visual, "item_ids": req.get("item_ids") or [],
            "reason_reminder": "A typed reason of at least 10 characters is required in the terminal.",
            "packet_error": row is None or bool(visual.get("error")) or bool(lookup_error), "error": lookup_error}


def _render_reference_import(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: signer envelope/record fields and STAGING_DIR PNG."""
    from lib.reference_import import STAGING_DIR

    hint = req.get("envelope") if isinstance(req.get("envelope"), dict) else {}
    record = req.get("approval_record") if isinstance(req.get("approval_record"), dict) else {}
    asset_id = hint.get("normalized_pixel_hash", record.get("normalized_pixel_hash"))
    staged = root / STAGING_DIR / f"reference-{asset_id}.png"
    visual = _file_packet(root, asset_id, path=staged, label="normalized import")
    return {"origin_class": hint.get("origin_class", record.get("origin_class")),
            "visual": visual, "packet_error": bool(visual.get("error"))}


def _render_look_lock(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: confined, parsed wayfinder ticket (text only)."""
    action = (req.get("envelope") or {}).get("action", "activate")
    scope_kind, _, scope_entity = str(req.get("scope") or "").partition(":")
    entity_kind = scope_kind or str(req.get("entity_kind") or "")
    entity_id = str(req.get("entity_id") or scope_entity)
    current = next(
        (entry for entry in _looks_summary(root)
         if entry.get("entity") == entity_id and entry.get("entity_kind") == entity_kind),
        None,
    )
    # A retire approval has no ticket: the signer constructs it from the
    # active current look.  Render that same current packet state, not a hint.
    if action == "retire":
        if current is None:
            return {"packet_error": True, "error": "active look unavailable"}
        return {"action": "retire", "source_ticket_ref": current.get("source_ticket_ref"),
                "look_hash": current.get("look_hash"), "supersedes": current.get("look_hash")}
    ticket = req.get("source_ticket_path")
    if not isinstance(ticket, str) or not ticket:
        return {"packet_error": True, "error": "look ticket unavailable"}
    try:
        from lib.look_ingest import LookIngestError, confine_ticket_path, parse_look_ticket, wayfinder_root_for

        wayfinder_root = wayfinder_root_for(root)
        raw_ticket = Path(ticket)
        candidate = wayfinder_root / raw_ticket
        # Both lexical and resolved (including symlink) escapes have the
        # required exact presentation.  Other ticket defects remain distinct
        # packet errors rather than being mislabeled as an escape.
        if ".." in raw_ticket.parts:
            return {"packet_error": True, "source_ticket_ref": "ticket outside wayfinder root"}
        try:
            candidate.resolve().relative_to(wayfinder_root.resolve())
        except (OSError, ValueError):
            return {"packet_error": True, "source_ticket_ref": "ticket outside wayfinder root"}
        confined = confine_ticket_path(candidate, wayfinder_root)
        look = parse_look_ticket(confined, wayfinder_root=wayfinder_root)
    except LookIngestError:
        return {"packet_error": True, "error": "look ticket unavailable"}
    except Exception:
        return {"packet_error": True, "error": "look ticket unavailable"}
    if (look.entity_kind, look.entity_id) != (entity_kind, entity_id):
        return {"packet_error": True, "error": "look ticket names another entity"}
    old = current
    return {"source_ticket_ref": look.source_ticket_ref, "look_hash": look.look_hash,
            "supersedes": (old or {}).get("look_hash")}


def _render_config(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: project.yaml text, shown with the request summary."""
    path = root / "project.yaml"
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return {"packet_error": True, "error": "project.yaml unavailable", "summary": req.get("summary")}
    if info.st_size > _MAX_GATE_PROJECT_YAML_BYTES:
        return {"packet_error": True, "error": "project.yaml exceeds byte budget", "summary": req.get("summary")}
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_GATE_PROJECT_YAML_BYTES + 1)
        if len(raw) > _MAX_GATE_PROJECT_YAML_BYTES:
            return {"packet_error": True, "error": "project.yaml exceeds byte budget", "summary": req.get("summary")}
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return {"packet_error": True, "error": "project.yaml unreadable", "summary": req.get("summary")}
    return {"project_yaml": text, "summary": req.get("summary")}


def _render_pipeline_migration(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: request migration fields (no visual evidence)."""
    hint = req.get("approval_record") or {}
    prior_id = (req.get("envelope") or {}).get("supersedes_receipt_id")
    rows, lookup_error = _raw_jsonl_targets(root / "approvals.jsonl", {prior_id}) if isinstance(prior_id, str) else ({}, None)
    prior = rows.get(prior_id, {}) if isinstance(prior_id, str) else {}
    prior_record = prior.get("record") if isinstance(prior, dict) else {}
    result = {"from_version": req.get("from_version") or (req.get("envelope") or {}).get("from_version")
            or (prior_record or {}).get("version") or (prior_record or {}).get("pipeline_version"),
            "to_version": req.get("pipeline_version") or req.get("to_version") or hint.get("version")}
    if lookup_error:
        result.update({"packet_error": True, "error": lookup_error})
    return result


def _render_character(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: visual_bible character entry hero + sheet refs, as gate constructor uses."""
    entry = next((x for x in _bible(root).get("characters") or [] if isinstance(x, dict) and x.get("id") == req.get("entity_id")), None)
    refs = [] if not isinstance(entry, dict) else [("hero", entry.get("hero"))] + list((entry.get("sheet") or {}).items())
    result = _visuals_from_refs(root, refs)
    result["packet_error"] |= not isinstance(entry, dict)
    return result


def _render_location(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: visual_bible locations[].establishing and angles refs."""
    entry = next((x for x in _bible(root).get("locations") or [] if isinstance(x, dict) and x.get("id") == req.get("entity_id")), None)
    refs = [] if not isinstance(entry, dict) else [("establishing", entry.get("establishing"))] + [(f"angle_{i}", x) for i, x in enumerate(entry.get("angles") or [])]
    result = _visuals_from_refs(root, refs)
    result["packet_error"] |= not isinstance(entry, dict)
    return result


def _render_poster(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: visual_bible.poster key_art/title_card/poster_final refs."""
    poster = _bible(root).get("poster") or {}
    result = _visuals_from_refs(root, [(role, poster.get(role)) for role in ("key_art", "title_card", "poster_final")])
    if not isinstance(poster, dict) or not result["visuals"]:
        result.update({"packet_error": True, "error": "poster evidence unavailable"})
    return result


def _render_storyboard(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Authoritative source: pending checkpoint asset_manifest storyboard_frame assets."""
    checkpoint, checkpoint_error = _read_gate_json(root / f"checkpoint_{req.get('stage')}.json")
    if checkpoint is None:
        return {
            "visuals": [],
            "packet_error": True,
            "error": checkpoint_error or "storyboard checkpoint unreadable",
        }
    manifest = (checkpoint.get("artifacts") or {}).get("asset_manifest") or {}
    if not isinstance(manifest, dict):
        return {"visuals": [], "packet_error": True, "error": "storyboard manifest unavailable"}
    refs = [(str(x.get("shot_id") or x.get("id")), {"asset_id": x.get("asset_id") or x.get("sha256")})
            for x in manifest.get("assets") or [] if isinstance(x, dict) and x.get("asset_class") == "storyboard_frame"]
    result = _visuals_from_refs(root, refs)
    if not refs:
        result.update({"packet_error": True, "error": "storyboard frames unavailable"})
    return result


def _render_text_only(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    """Text-only kinds: no constructor source cites a required visual asset id."""
    excluded = {"approval_record", "envelope"}
    request = {k: v for k, v in req.items() if k not in excluded and k != "preview_paths"}
    return {"summary": req.get("summary"), "request": request,
            "preview_filenames": [Path(str(p)).name for p in req.get("preview_paths") or []]}


from lib.receipts import APPROVAL_KINDS


class _GateRendererMap(dict[str, Any]):
    """Frozen raw-authority dispatch contract, inspected from gate_approve.

    ``headshot``: ``checkpoint_headshots.json`` →
    ``artifacts.headshot_packet.characters[].candidates[].path``.
    ``sheet``/``hero``: ``checkpoint_visual_bible.json`` →
    ``artifacts.visual_bible.characters[]`` (sheet refs / hero ref).
    ``location``: the same visual-bible ``locations[].establishing|angles``.
    ``poster``: the same visual-bible ``poster.key_art|title_card|poster_final``.
    ``storyboard_batch``: ``checkpoint_<request.stage>.json`` →
    ``artifacts.asset_manifest.assets[asset_class=storyboard_frame]``.
    ``qc_override``: project ``qc-receipts.jsonl`` → verdict asset ID.
    ``reference_import``: ``reference_import.STAGING_DIR/reference-<pixel-hash>.png``.
    ``look_lock``: confined ``wayfinder/resolved`` ticket named by request.
    ``config``: ``project.yaml``; ``pipeline_migration``: request plus raw
    ``approvals.jsonl`` superseded row; grandfather/artifact review: text-only.
    """


# Keep this map exhaustive: adding a gate kind without a board packet is an
# intentional test failure.
GATE_RENDERERS = _GateRendererMap({
    "headshot": _render_headshot,
    "sheet": _render_sheet,
    "qc_override": _render_qc_override,
    "reference_import": _render_reference_import,
    "look_lock": _render_look_lock,
    "config": _render_config,
    "pipeline_migration": _render_pipeline_migration,
    "hero": _render_character,
    "location": _render_location,
    "poster": _render_poster,
    "storyboard_batch": _render_storyboard,
    "headshot_grandfather": _render_text_only,
    "artifact_review": _render_text_only,
})
assert set(GATE_RENDERERS) == set(APPROVAL_KINDS)


def build_gate_detail(project_dir: Path, request_id: str) -> dict[str, Any]:
    """Build one lazy, unverified gate packet without reconstructing records.

    Every response deliberately has ``record_sha256: None``: the signer is
    the only process that constructs an approval record.  Archived requests
    show source data and a raw approvals ledger row, never current state.
    """
    project_dir = Path(project_dir)
    try:
        from lib.run_common import RunError, resolve_project_root, validate_request_id
        validate_request_id(request_id)
        root = resolve_project_root(project_dir.name, projects_dir=project_dir.parent)
    except Exception:
        return {"snapshot_at": time.time(), "record_sha256": None, "error": "invalid governed project or request"}
    state_name, request, _ = _request_for_detail(root, request_id)
    result: dict[str, Any] = {"snapshot_at": time.time(), "record_sha256": None, "request_id": request_id, "state": state_name}
    if request is None:
        result["error"] = "request unavailable" if state_name != "missing" else "request not found"
        if state_name == "error":
            result["packet_error"] = True
        return result
    if state_name != "pending":
        receipt_id = request.get("approval_receipt_id")
        rows, lookup_error = _raw_jsonl_targets(root / "approvals.jsonl", {receipt_id}) if isinstance(receipt_id, str) else ({}, None)
        ledger = rows.get(receipt_id) if isinstance(receipt_id, str) else None
        result.update({"request": request, "approval_receipt_id": receipt_id, "declined_note": request.get("declined_note"),
                       "unverified_ledger_row": ledger, "ledger_label": "unverified ledger row"})
        if lookup_error:
            result.update({"packet_error": True, "error": lookup_error})
        return result
    renderer = GATE_RENDERERS.get(str(request.get("kind")))
    try:
        packet = renderer(root, request) if renderer else {"packet_error": True, "error": "unknown gate kind"}
    except Exception:
        packet = {"packet_error": True, "error": "gate evidence is malformed"}
    result.update({"request": request, "packet": packet})
    return result


def load_gate_detail(project_dir: Path, request_id: str) -> dict[str, Any]:
    """Compatibility-friendly public name for the lazy gate detail builder."""
    return build_gate_detail(project_dir, request_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_board_state(project_dir: Path) -> dict[str, Any]:
    """Full BoardState for one project. Never raises."""
    project_dir = Path(project_dir)
    project_id = project_dir.name

    marker = _read_json(project_dir / "project.json") or {}
    meta_json = _read_json(project_dir / "meta.json") or {}

    checkpoints = _collect_checkpoints(project_dir)
    history = _collect_history(project_dir)

    pipeline_type = marker.get("pipeline_type")
    if not pipeline_type:
        for cp in checkpoints.values():
            pt = cp.get("pipeline_type")
            if pt and pt != "unknown":
                pipeline_type = pt
                break
    pipeline_meta = _load_pipeline_meta(pipeline_type)

    artifacts = _collect_artifacts(project_dir, checkpoints)
    events = read_events(project_dir, limit=250)
    storyboard = _build_storyboard(project_dir, artifacts, events)
    media = _scan_media(project_dir)

    stages = _build_stage_rail(pipeline_meta, checkpoints, history)

    # Cost: latest checkpoint snapshot wins; fall back to manifest total.
    cost = None
    for cp in sorted(checkpoints.values(), key=lambda c: c.get("_mtime", 0), reverse=True):
        if cp.get("cost_snapshot"):
            cost = cp["cost_snapshot"]
            break
    if cost is None:
        total = (artifacts.get("asset_manifest") or {}).get("total_cost_usd")
        if total is not None:
            cost = {"total_spent_usd": total}

    import time
    last_activity = _last_activity(project_dir)
    now = time.time()

    # Stall detection: an in_progress stage that stopped writing anything.
    for stage_entry in stages:
        if (
            stage_entry["status"] == "in_progress"
            and last_activity
            and (now - last_activity) > STALL_WINDOW_SECONDS
        ):
            stage_entry["stalled"] = True
            stage_entry["stalled_minutes"] = int((now - last_activity) / 60)

    state: dict[str, Any] = {
        "project_id": project_id,
        "title": marker.get("title") or meta_json.get("name") or project_id.replace("-", " ").title(),
        "pipeline": pipeline_meta,
        "style_playbook": marker.get("style_playbook"),
        "created_at": marker.get("created_at"),
        "has_marker": bool(marker),
        "has_pipeline_state": bool(checkpoints),
        "stages": stages,
        "artifacts": artifacts,
        "storyboard": storyboard,
        "media": media,
        "events": events,
        "cost": cost,
        "last_activity": last_activity,
        "live": bool(last_activity and (now - last_activity) < LIVE_WINDOW_SECONDS),
    }
    state["gates"] = _gates_state(project_dir, cost)
    state["poster"] = _find_poster(project_dir, state)
    return state


def summarize_project(project_dir: Path) -> dict[str, Any]:
    """Cheap library-card summary (no full artifact parse of big files)."""
    state = load_board_state(project_dir)
    active = next((s for s in state["stages"] if s["status"] in ("in_progress", "awaiting_human")), None)
    done = [s for s in state["stages"] if s["status"] == "completed"]
    return {
        "project_id": state["project_id"],
        "title": state["title"],
        "pipeline_type": state["pipeline"]["pipeline_type"],
        "has_pipeline_state": state["has_pipeline_state"],
        "poster": state["poster"],
        "live": state["live"],
        "last_activity": state["last_activity"],
        "active_stage": active["name"] if active else None,
        "awaiting_human": bool(active and active["status"] == "awaiting_human"),
        "stage_states": [
            {"name": s["name"], "status": s["status"]}
            for s in state["stages"] if not s.get("undeclared")
        ],
        "completed_count": len(done),
        "render_count": len(state["media"]["renders"]),
        "scene_count": len((state["storyboard"] or {}).get("scenes", [])),
    }


def list_projects(projects_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """Library view: every project directory, live-first then recency."""
    root = Path(projects_dir) if projects_dir else PROJECTS_DIR
    if not root.is_dir():
        return []
    summaries = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        try:
            summaries.append(summarize_project(entry))
        except Exception:
            summaries.append({
                "project_id": entry.name,
                "title": entry.name.replace("-", " ").title(),
                "pipeline_type": "unknown",
                "has_pipeline_state": False,
                "poster": None,
                "live": False,
                "last_activity": 0,
                "active_stage": None,
                "awaiting_human": False,
                "stage_states": [],
                "completed_count": 0,
                "render_count": 0,
                "scene_count": 0,
                "error": "unreadable",
            })
    summaries.sort(key=lambda s: (not s["live"], -(s["last_activity"] or 0)))
    return summaries

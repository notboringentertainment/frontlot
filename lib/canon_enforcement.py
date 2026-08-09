"""Runtime enforcement for the authored-canon validation profile.

Pipelines that declare ``validation_profile: authored-canon`` in their manifest
(currently authored-film) get these checks at checkpoint write time, on top of
the generic manifest artifact contracts in lib/checkpoint.py.

The split of responsibilities follows the framework contract: semantic story
judgment (does a scene *feel* off-tone?) stays with skills, reviewers, and the
human gates. Structural invariants the runtime can determine exactly — an
unresolved blocking question, a script section with no provenance, a protected
line that lost a character or its audibility verdict, a canon pass that is
missing or failing, a render path that does not exist or has no video stream —
are enforced here, because prose alone cannot promise "canon never forks."

Status semantics: ``completed`` gets the full strict battery. ``awaiting_human``
is the escalation surface — structured evidence is still required (so the
writer sees a real review, not a shrug), but failing verdicts, unresolved
tensions, and open blocking questions are exactly what that status exists to
present, so they do not reject the write.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

VISUAL_ASSET_TYPES = {"image", "video", "animation"}


def _fail(message: str) -> None:
    from lib.checkpoint import CheckpointValidationError

    raise CheckpointValidationError(f"CANON CONTRACT VIOLATION: {message}")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _load_canon(
    pipeline_dir: Path, project_id: str, artifacts: dict[str, Any]
) -> dict[str, Any] | None:
    """The effective canon packet: the ingest checkpoint's packet is
    authoritative; a packet carried in the current checkpoint is only used
    when writing canon_ingest itself."""
    checkpoint = _read_json(
        pipeline_dir / project_id / "checkpoint_canon_ingest.json"
    )
    if checkpoint and isinstance(checkpoint.get("artifacts"), dict):
        packet = checkpoint["artifacts"].get("canon_packet")
        if isinstance(packet, dict):
            return packet
    packet = artifacts.get("canon_packet")
    return packet if isinstance(packet, dict) else None


def _all_decisions(
    pipeline_dir: Path, project_id: str, artifacts: dict[str, Any]
) -> list[dict[str, Any]]:
    """Cumulative decision log plus any decision_log carried in the checkpoint
    being written (its merge has not happened yet at enforcement time)."""
    decisions: list[dict[str, Any]] = []
    cumulative = _read_json(pipeline_dir / project_id / "decision_log.json")
    if cumulative:
        decisions.extend(cumulative.get("decisions", []))
    carried = artifacts.get("decision_log")
    if isinstance(carried, dict):
        decisions.extend(carried.get("decisions", []))
    return [d for d in decisions if isinstance(d, dict)]


def _valid_ruling_question_ids(decisions: list[dict[str, Any]]) -> set[str]:
    """question_ids released by VALID canon rulings.

    A ruling only counts when the writer actually ruled: user_approved must be
    true, and the selected answer must be one of the options that were put in
    front of them. An unapproved or malformed 'ruling' releases nothing.
    """
    released: set[str] = set()
    for d in decisions:
        if d.get("category") != "canon_ruling":
            continue
        if not d.get("question_id"):
            continue
        if d.get("user_approved") is not True:
            continue
        option_ids = {
            opt.get("option_id")
            for opt in d.get("options_considered", [])
            if isinstance(opt, dict)
        }
        if not d.get("selected") or d.get("selected") not in option_ids:
            continue
        released.add(d["question_id"])
    return released


def _resolved_question_ids(
    canon: dict[str, Any], decisions: list[dict[str, Any]]
) -> set[str]:
    resolved = {
        q.get("id")
        for q in canon.get("open_questions", [])
        if q.get("status") == "resolved" and q.get("id")
    }
    return resolved | _valid_ruling_question_ids(decisions)


def _canon_ids(canon: dict[str, Any], decisions: list[dict[str, Any]]) -> set[str]:
    """Ids downstream artifacts may trace to: locks, beats, and resolved
    questions (a writer's ruling is governing canon once approved)."""
    ids = {lock.get("id") for lock in canon.get("locks", [])}
    ids |= {beat.get("id") for beat in canon.get("structure", {}).get("beats", [])}
    ids |= _resolved_question_ids(canon, decisions)
    ids.discard(None)
    return ids


def _entity_names(canon: dict[str, Any]) -> set[str]:
    names = {c.get("name") for c in canon.get("characters", [])}
    names |= {l.get("name") for l in canon.get("locations", [])}
    names.discard(None)
    return names


def _entities_by_name(canon: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entity in canon.get("characters", []) + canon.get("locations", []):
        name = entity.get("name")
        if name:
            out[name] = entity
    return out


def _split_refs(raw: str) -> list[str]:
    """Parse a source_ref/canon_ref value in the skill-documented format:
    whitespace- or comma-separated tokens, each optionally prefixed 'canon:'
    (e.g. "canon:beat-07 lock-003")."""
    tokens: list[str] = []
    for piece in str(raw).replace(",", " ").split():
        token = piece[len("canon:"):] if piece.startswith("canon:") else piece
        if token:
            tokens.append(token)
    return tokens


def _check_blocking_questions(
    canon: dict[str, Any],
    decisions: list[dict[str, Any]],
    stage: str,
) -> None:
    released = _valid_ruling_question_ids(decisions)
    unresolved = [
        q
        for q in canon.get("open_questions", [])
        if q.get("blocking")
        and q.get("status") != "resolved"
        and q.get("id") not in released
    ]
    if unresolved:
        ids = [q.get("id") for q in unresolved]
        _fail(
            f"stage {stage!r} cannot complete: blocking canon questions "
            f"{ids} are unresolved. Each needs the writer's answer recorded "
            f"as a decision_log entry with category='canon_ruling', the "
            f"matching question_id, user_approved=true, and a selected value "
            f"matching one of its options_considered — or status='resolved' "
            f"in the canon packet."
        )


def _check_script(
    script: dict[str, Any],
    canon: dict[str, Any],
    ids: set[str],
    status: str,
) -> None:
    for section in script.get("sections", []):
        raw_ref = section.get("source_ref")
        if not raw_ref:
            _fail(
                f"script section {section.get('id')!r} has no source_ref — "
                f"every section must trace to canon beat/lock ids "
                f"(e.g. \"canon:beat-07 lock-003\"). Known ids: {sorted(ids)}"
            )
        tokens = _split_refs(raw_ref)
        unknown = [t for t in tokens if ids and t not in ids]
        if not tokens or unknown:
            _fail(
                f"script section {section.get('id')!r} source_ref {raw_ref!r} "
                f"contains unknown canon ids {unknown or tokens}. "
                f"Known ids: {sorted(ids)}"
            )

    canon_check = (script.get("metadata") or {}).get("canon_check")
    if not isinstance(canon_check, dict) or not isinstance(
        canon_check.get("locks_honored"), list
    ):
        _fail(
            "script metadata.canon_check with a locks_honored list is required "
            "— the adaptation must state which locks it honored."
        )

    lock_ids = {lock.get("id") for lock in canon.get("locks", [])}
    lock_ids.discard(None)
    missing_locks = sorted(lock_ids - set(canon_check["locks_honored"]))
    if missing_locks:
        _fail(
            f"script metadata.canon_check.locks_honored is missing lock ids "
            f"{missing_locks} — every canon lock is binding on the adaptation "
            f"and must be explicitly honored (a lock you cannot honor is a "
            f"tension for the writer, not an omission)."
        )

    # Tensions are the collision-escalation channel: fine to PRESENT at the
    # gate (awaiting_human), but a stage may not COMPLETE with one unless the
    # writer's ruling is linked.
    tensions = canon_check.get("tensions") or []
    if status == "completed":
        unruled = [
            t for t in tensions
            if not (isinstance(t, dict) and t.get("resolution_ref"))
        ]
        if unruled:
            _fail(
                f"script metadata.canon_check.tensions contains {len(unruled)} "
                f"tension(s) without a resolution_ref: {unruled} — a tension "
                f"completes only after the writer rules (link the canon_ruling "
                f"decision_id), otherwise present it at the gate."
            )
    # Back-compat: an explicit unresolved_conflicts list must be empty always.
    if canon_check.get("unresolved_conflicts"):
        _fail(
            f"script metadata.canon_check.unresolved_conflicts is non-empty: "
            f"{canon_check['unresolved_conflicts']} — conflicts escalate to "
            f"the writer, they never ride along silently."
        )

    joined = "\n".join(
        str(section.get("text", "")) for section in script.get("sections", [])
    )
    for protected in canon.get("protected_lines", []):
        line = protected.get("line", "")
        if line and line not in joined:
            _fail(
                f"protected line {line!r} does not appear verbatim in any "
                f"script section — paraphrase is a canon violation."
            )


def _check_scene_plan(
    scene_plan: dict[str, Any], ids: set[str]
) -> None:
    for scene in scene_plan.get("scenes", []):
        refs = scene.get("canon_refs")
        if not refs:
            _fail(
                f"scene {scene.get('id')!r} has no canon_refs — every scene "
                f"must cite the canon beat/lock ids it realizes so continuity "
                f"constraints travel with it."
            )
        tokens = [t for ref in refs for t in _split_refs(ref)]
        unknown = [t for t in tokens if ids and t not in ids]
        if unknown:
            _fail(
                f"scene {scene.get('id')!r} canon_refs {unknown} do not match "
                f"any canon beat/lock id. Known ids: {sorted(ids)}"
            )


def _check_assets(
    asset_manifest: dict[str, Any],
    canon: dict[str, Any],
    ids: set[str],
) -> None:
    known_refs = ids | _entity_names(canon)
    entities = _entities_by_name(canon)
    for asset in asset_manifest.get("assets", []):
        if asset.get("type") not in VISUAL_ASSET_TYPES:
            continue
        continuity = asset.get("continuity")
        if not isinstance(continuity, dict):
            _fail(
                f"visual asset {asset.get('id')!r} has no continuity evidence "
                f"— authored-film visual assets must record canon_refs, "
                f"references_applied, and risk_notes_applied."
            )
        refs = continuity.get("canon_refs") or []
        unknown = [r for r in refs if known_refs and r not in known_refs]
        if not refs or unknown:
            _fail(
                f"visual asset {asset.get('id')!r} continuity.canon_refs "
                f"{unknown or refs} do not resolve to any canon lock/beat id "
                f"or tracked character/location name. Known: {sorted(known_refs)}"
            )
        # Evidence must be REAL where the canon demands it: an entity with
        # reference assets or continuity risks cannot be depicted by an asset
        # whose evidence arrays are empty — unless the notes field explicitly
        # justifies why not.
        justified = bool(str(continuity.get("notes") or "").strip())
        for ref in refs:
            entity = entities.get(ref)
            if entity is None:
                continue
            if entity.get("reference_assets") and not continuity.get(
                "references_applied"
            ) and not justified:
                _fail(
                    f"visual asset {asset.get('id')!r} depicts {ref!r}, which "
                    f"has canon reference_assets {entity['reference_assets']}, "
                    f"but continuity.references_applied is empty and no "
                    f"continuity.notes justify skipping them."
                )
            if entity.get("visual_continuity_risks") and not continuity.get(
                "risk_notes_applied"
            ) and not justified:
                _fail(
                    f"visual asset {asset.get('id')!r} depicts {ref!r}, which "
                    f"has visual_continuity_risks "
                    f"{entity['visual_continuity_risks']}, but "
                    f"continuity.risk_notes_applied is empty and no "
                    f"continuity.notes justify it."
                )


def _check_canon_pass(
    final_review: dict[str, Any],
    canon: dict[str, Any],
    strict: bool,
) -> None:
    """strict=True (completed): everything must PASS. strict=False
    (awaiting_human): the structured review must exist — the schema already
    guarantees its shape — but failing verdicts are presentable, because the
    escalation checkpoint is where the writer sees them."""
    canon_pass = (final_review.get("checks") or {}).get("canon_pass")
    if not isinstance(canon_pass, dict):
        _fail(
            "final_review.checks.canon_pass is required at compose — the "
            "render must be verified against every lock and protected line."
        )
    if not strict:
        return

    if canon_pass.get("status") != "pass":
        _fail(
            f"canon pass status is {canon_pass.get('status')!r} — compose "
            f"cannot complete on a failing canon pass; write awaiting_human "
            f"and escalate to the writer."
        )
    if canon_pass.get("unresolved_flags"):
        _fail(
            f"canon pass has unresolved flags: {canon_pass['unresolved_flags']}"
        )

    verdicts = {
        entry.get("lock_id"): entry.get("verdict")
        for entry in canon_pass.get("locks", [])
    }
    for lock in canon.get("locks", []):
        lock_id = lock.get("id")
        if verdicts.get(lock_id) != "honored":
            _fail(
                f"canon pass verdict for lock {lock_id!r} is "
                f"{verdicts.get(lock_id)!r} — every lock needs an explicit "
                f"'honored' verdict before compose completes."
            )

    line_entries = {
        entry.get("line"): entry
        for entry in canon_pass.get("protected_lines", [])
    }
    for protected in canon.get("protected_lines", []):
        line = protected.get("line")
        entry = line_entries.get(line, {})
        if entry.get("present_verbatim") is not True:
            _fail(
                f"canon pass does not confirm protected line {line!r} is "
                f"present verbatim in the render."
            )
        if entry.get("audible") is not True:
            _fail(
                f"protected line {line!r} is not confirmed audible in the mix "
                f"— present-in-subtitles is not enough; the writer protected "
                f"a LINE, not a caption."
            )

    verdict = ((canon_pass.get("tone") or {}).get("must_never_feel_like_verdict") or "")
    if not verdict.strip():
        _fail(
            "canon pass has no tone verdict — must_never_feel_like requires "
            "an explicit sentence, not silence."
        )

    spotchecks = {
        entry.get("subject"): entry.get("verdict")
        for entry in canon_pass.get("continuity_spotchecks", [])
    }
    for name in sorted(_entity_names(canon)):
        if spotchecks.get(name) != "consistent":
            _fail(
                f"canon pass continuity_spotchecks do not confirm tracked "
                f"entity {name!r} as 'consistent' (got "
                f"{spotchecks.get(name)!r}) — every tracked character and "
                f"location gets a spot-check before compose completes."
            )


def _check_render_outputs(
    project_dir: Path, render_report: dict[str, Any], canon: dict[str, Any]
) -> None:
    project_root = project_dir.resolve()
    for output in render_report.get("outputs", []):
        raw = str(output.get("path", ""))
        candidate = Path(raw)
        if candidate.is_absolute():
            candidate = candidate.resolve()
        else:
            candidate = (project_root / candidate).resolve()
        if project_root not in candidate.parents and candidate != project_root:
            _fail(
                f"render output path {raw!r} escapes the project workspace "
                f"{project_root} — outputs must live under the project directory."
            )
        if not candidate.is_file() or candidate.stat().st_size == 0:
            _fail(
                f"render output {raw!r} does not exist (or is empty) at "
                f"{candidate} — a completed compose must point at a real render."
            )
        _ffprobe_verify(candidate, raw, output)
        if canon.get("protected_lines"):
            _verify_audio_signal(candidate, raw)


def _verify_audio_signal(path: Path, raw: str) -> None:
    """A canon with protected lines promises AUDIBLE dialogue. The deliverable
    must carry an audio stream with real signal — a silent track passed every
    structural check in the shakedown until a human ear caught it."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        _fail(
            f"ffmpeg is not installed, so audio signal in render output {raw!r} "
            f"cannot be verified. The authored-canon profile fails closed."
        )
    result = subprocess.run(
        [ffmpeg, "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    stderr = result.stderr or ""
    if "max_volume" not in stderr:
        _fail(
            f"render output {raw!r} has no measurable audio stream, but the "
            f"canon protects spoken lines — the film cannot be silent."
        )
    try:
        max_db = float(stderr.split("max_volume:")[1].split("dB")[0].strip())
    except (IndexError, ValueError):
        _fail(f"could not parse audio loudness for render output {raw!r}.")
        return
    if max_db <= -60.0:
        _fail(
            f"render output {raw!r} audio peaks at {max_db} dB — that is "
            f"silence, and the canon protects a line that must be audible. "
            f"Verify the mix was actually muxed into the deliverable."
        )


def _ffprobe_verify(path: Path, raw: str, output: dict[str, Any]) -> None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        _fail(
            f"ffprobe is not installed, so render output {raw!r} cannot be "
            f"verified. The authored-canon profile fails closed: install "
            f"ffmpeg/ffprobe before completing compose."
        )
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(
            f"ffprobe rejected render output {raw!r}: {result.stderr.strip()}"
        )
    try:
        probe = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        probe = {}
    streams = probe.get("streams") or []
    duration = float((probe.get("format") or {}).get("duration") or 0)
    if not streams or duration <= 0:
        _fail(
            f"ffprobe found no playable streams / positive duration in render "
            f"output {raw!r}."
        )

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        _fail(
            f"render output {raw!r} has no video stream — an audio-only file "
            f"is not a completed film."
        )

    reported_duration = output.get("duration_seconds")
    if isinstance(reported_duration, (int, float)) and reported_duration > 0:
        tolerance = max(1.0, reported_duration * 0.15)
        if abs(duration - reported_duration) > tolerance:
            _fail(
                f"render output {raw!r} actual duration {duration:.2f}s does "
                f"not match the reported {reported_duration}s (±{tolerance:.1f}s)."
            )

    reported_res = str(output.get("resolution") or "")
    if "x" in reported_res:
        try:
            want_w, want_h = (int(v) for v in reported_res.lower().split("x", 1))
        except ValueError:
            want_w = want_h = None
        if want_w and want_h:
            got = {(s.get("width"), s.get("height")) for s in video_streams}
            if (want_w, want_h) not in got:
                _fail(
                    f"render output {raw!r} video stream dimensions {got} do "
                    f"not match the reported resolution {reported_res!r}."
                )


def enforce_authored_canon(
    pipeline_dir: Path,
    project_id: str,
    stage: str,
    status: str,
    artifacts: dict[str, Any],
) -> None:
    """Entry point called by lib.checkpoint.write_checkpoint before any state
    is persisted. Raises CheckpointValidationError on violation."""
    if status not in {"completed", "awaiting_human"}:
        return

    canon = _load_canon(pipeline_dir, project_id, artifacts)

    if stage == "canon_ingest":
        return  # schema + manifest contracts already validated the packet

    if canon is None:
        _fail(
            f"stage {stage!r} requires the canon_packet from a completed "
            f"canon_ingest checkpoint, and none is available."
        )

    decisions = _all_decisions(pipeline_dir, project_id, artifacts)
    ids = _canon_ids(canon, decisions)

    # Blocking questions gate COMPLETION. awaiting_human stays writable —
    # that checkpoint is exactly how an unresolved question reaches the writer.
    if status == "completed":
        _check_blocking_questions(canon, decisions, stage)

    if stage == "script":
        _check_script(artifacts.get("script", {}), canon, ids, status)
    elif stage == "scene_plan":
        _check_scene_plan(artifacts.get("scene_plan", {}), ids)
    elif stage == "assets":
        _check_assets(artifacts.get("asset_manifest", {}), canon, ids)
    elif stage == "compose":
        _check_canon_pass(
            artifacts.get("final_review", {}), canon, strict=(status == "completed")
        )
        if status == "completed":
            _check_render_outputs(
                pipeline_dir / project_id, artifacts.get("render_report", {}), canon
            )

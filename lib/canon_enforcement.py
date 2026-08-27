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

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

VISUAL_ASSET_TYPES = {"image", "video", "animation"}

# Stages at or after visual_bible: paid work happens here, so the project
# config (budget, cast cap, egress consent) must be human-bound first.
CONFIG_BOUND_STAGES = {"headshots", "visual_bible", "script", "scene_plan", "assets", "edit", "compose"}
PROJECT_CONFIG_FILENAME = "project.yaml"
CONFIG_ENTITY_ID = "project-config"
STORYBOARD_BATCH_ENTITY_ID = "storyboard_batch"
POSTER_ENTITY_ID = "poster"
# visual_bible 1.1 sheet: one technical turnaround (4 full-body angles + 3
# portraits in a single frame) plus an expression grid. ``wardrobe`` and the
# legacy single-angle views are optional; what a sheet carries is whatever
# roles it names, and every named role is verified and sealed in the receipt.
CHARACTER_SHEET_ROLES = ("turnaround", "expressions")
CHARACTER_SHEET_OPTIONAL_ROLES = ("wardrobe", "front", "three_quarter", "profile", "full_body")


def sheet_roles(entry: dict[str, Any]) -> tuple[str, ...]:
    """Roles present on a character entry's sheet, sorted (hash-stable)."""
    sheet = entry.get("sheet") if isinstance(entry, dict) else None
    return tuple(sorted(sheet.keys())) if isinstance(sheet, dict) else ()
CHARACTER_APPROVAL_KINDS = ("sheet", "hero")
LOOK_LOCK_MANIFEST = ("authored-film", "1.2")
# D19: 1.3 keeps the whole 1.2 look-lock contract and adds sheet QC.
LOOK_LOCK_MANIFESTS = frozenset({("authored-film", "1.2"), ("authored-film", "1.3")})
QC_MANIFEST = ("authored-film", "1.3")
SPOILER_SENSITIVE_FORMATS = {"trailer", "teaser"}
_CONFIG_DIGEST_RE = re.compile(r"config_sha256:\s*([a-f0-9]{64})")

# Media probes run on the checkpoint-write path against writer-supplied
# files; a stalled decode must not hang write_checkpoint forever.
MEDIA_PROBE_TIMEOUT_S = 180


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


def _check_packet_integrity(canon: dict[str, Any]) -> None:
    """Referential sanity the JSON schema cannot express: unique ids (a
    duplicate question id would let one canon_ruling release two blocking
    questions) and beat lock_refs that resolve to real locks."""
    lock_ids = [l.get("id") for l in canon.get("locks", []) if l.get("id")]
    dup_locks = sorted({x for x in lock_ids if lock_ids.count(x) > 1})
    if dup_locks:
        _fail(f"canon packet has duplicate lock ids: {dup_locks}")

    q_ids = [q.get("id") for q in canon.get("open_questions", []) if q.get("id")]
    dup_qs = sorted({x for x in q_ids if q_ids.count(x) > 1})
    if dup_qs:
        _fail(
            f"canon packet has duplicate open_question ids: {dup_qs} — one "
            f"ruling could silently release multiple blocking questions."
        )

    known = set(lock_ids)
    for beat in canon.get("structure", {}).get("beats", []):
        unknown = [r for r in beat.get("lock_refs", []) if r not in known]
        if unknown:
            _fail(
                f"beat {beat.get('id')!r} lock_refs {unknown} do not resolve "
                f"to any lock id in the packet."
            )


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
    non_strings = [x for x in canon_check["locks_honored"] if not isinstance(x, str)]
    if non_strings:
        _fail(
            f"script metadata.canon_check.locks_honored must contain lock id "
            f"strings; found non-string entries: {non_strings!r}"
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
    known_refs |= {
        e.get("id") for e in canon.get("characters", []) + canon.get("locations", [])
        if e.get("id")
    }
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


def _motion_promised(pipeline_dir: Path, project_id: str) -> bool:
    """True when the proposal locked a motion_required delivery promise."""
    checkpoint = _read_json(pipeline_dir / project_id / "checkpoint_proposal.json")
    if not checkpoint:
        return False
    promise = (
        (checkpoint.get("artifacts") or {})
        .get("proposal_packet", {})
        .get("production_plan", {})
        .get("delivery_promise", {})
    )
    return isinstance(promise, dict) and promise.get("motion_required") is True


def _verify_motion(path: Path, raw: str) -> None:
    """A motion_required promise is measured on the deliverable, in its own
    medium: sample frames, compare neighbors, fail when the film is
    effectively a slideshow. Found the hard way — a 60s render whose frames
    were ~92% identical self-certified as a motion-led film."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        _fail(f"ffmpeg missing; cannot verify the motion promise on {raw!r}.")

    # Dependency-free: sample 2fps as raw 160x90 grayscale and diff the bytes.
    W, H = 160, 90
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path),
             "-vf", f"fps=2,scale={W}:{H}",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True,
            timeout=MEDIA_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        _fail(
            f"motion verification of render output {raw!r} timed out after "
            f"{MEDIA_PROBE_TIMEOUT_S}s — the motion promise cannot be certified."
        )
        return
    if result.returncode != 0:
        # Fail CLOSED: a file ffmpeg cannot decode must not pass the motion
        # promise by producing zero frames.
        _fail(
            f"ffmpeg could not decode render output {raw!r} for motion "
            f"verification: {(result.stderr or b'').decode('utf-8', 'replace').strip()[:300]}"
        )
    data = result.stdout
    frame_size = W * H
    n = len(data) // frame_size
    if n < 3:
        return  # too short to judge motion honestly
    frames = [data[i * frame_size:(i + 1) * frame_size] for i in range(n)]
    diffs = []
    for a, b in zip(frames, frames[1:]):
        total = sum(abs(x - y) for x, y in zip(a, b))
        diffs.append(total / frame_size)
    moving = sum(1 for d in diffs if d > 2.0)
    ratio = moving / len(diffs)
    if ratio < 0.25:
        _fail(
            f"render output {raw!r} breaks the motion promise: only "
            f"{ratio:.0%} of sampled frame pairs show visible change "
            f"(threshold 25%). The proposal locked motion_required=true — "
            f"this plays as a slideshow. Add real motion or take a "
            f"still-led downgrade back to the writer for approval."
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
        if _motion_promised(project_dir.parent, project_dir.name):
            _verify_motion(candidate, raw)


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
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=MEDIA_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        _fail(
            f"audio verification of render output {raw!r} timed out after "
            f"{MEDIA_PROBE_TIMEOUT_S}s — the deliverable cannot be certified."
        )
        return
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
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=MEDIA_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        _fail(
            f"ffprobe timed out after {MEDIA_PROBE_TIMEOUT_S}s on render "
            f"output {raw!r} — the deliverable cannot be certified."
        )
        return
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


# ---------------------------------------------------------------------------
# Visual bible (PLAN §3–§7): approval records, receipts, content addressing
# ---------------------------------------------------------------------------

def _load_stage_artifact(
    pipeline_dir: Path, project_id: str, stage: str, name: str, artifacts: dict[str, Any]
) -> dict[str, Any] | None:
    """The artifact as recorded by ``stage``'s checkpoint, else the copy carried
    in the checkpoint being written."""
    checkpoint = _read_json(pipeline_dir / project_id / f"checkpoint_{stage}.json")
    if checkpoint and isinstance(checkpoint.get("artifacts"), dict):
        value = checkpoint["artifacts"].get(name)
        if isinstance(value, dict):
            return value
    value = artifacts.get(name)
    return value if isinstance(value, dict) else None


def _version(artifact: dict[str, Any]) -> str:
    return str(artifact.get("version") or "1.0")


# ---- approval records (the exact bytes a human approved; PLAN §5 D8) ----

def config_approval_record(config_sha256: str) -> dict[str, Any]:
    """Record hashed into a ``kind='config'`` approval receipt."""
    return {"config_sha256": config_sha256}


def character_approval_record(entry: dict[str, Any], palette: dict[str, Any]) -> dict[str, Any]:
    assets = {"hero": (entry.get("hero") or {}).get("asset_id")}
    sheet = entry.get("sheet") or {}
    for role in sheet_roles(entry):
        assets[role] = (sheet.get(role) or {}).get("asset_id")
    record = {
        "id": entry.get("id"),
        "assets": assets,
        "wardrobe_negative": entry.get("wardrobe_negative"),
        "palette": palette,
    }
    if "prompt_recipe" in entry or "look_ref" in entry:
        # visual_bible 1.1: the approval seals the recipe and the look hash,
        # never a verbatim prompt block.
        record["prompt_recipe"] = entry.get("prompt_recipe")
        record["look_hash"] = (entry.get("look_ref") or {}).get("look_hash")
        record["sheet_revision"] = entry.get("sheet_revision")
        if entry.get("qc_receipts") is not None:
            # D19 R1#12: the human-signed digest covers exactly which QC
            # verdicts were relied on; any change between display and
            # signing changes the digest and the gate refuses.
            record["qc_receipts"] = dict(entry["qc_receipts"])
    else:
        record["approved_prompt_block"] = entry.get("approved_prompt_block")
    return record


def location_approval_record(entry: dict[str, Any], palette: dict[str, Any]) -> dict[str, Any]:
    assets = {"establishing": (entry.get("establishing") or {}).get("asset_id")}
    for i, angle in enumerate(entry.get("angles") or []):
        assets[f"angle_{i}"] = (angle or {}).get("asset_id")
    record = {
        "id": entry.get("id"),
        "assets": assets,
        "palette": entry.get("palette_override") or palette,
    }
    if "look_ref" in entry:
        record["look_hash"] = (entry.get("look_ref") or {}).get("look_hash")
        record["sheet_revision"] = entry.get("sheet_revision")
    return record


def poster_approval_record(poster: dict[str, Any], palette: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": POSTER_ENTITY_ID,
        "assets": {
            role: (poster.get(role) or {}).get("asset_id")
            for role in ("key_art", "title_card", "poster_final")
        },
        "palette": palette,
    }


def storyboard_batch_record(frames: dict[str, str]) -> dict[str, Any]:
    """Record hashed into a ``kind='storyboard_batch'`` receipt: an ordered
    map ``shot_id -> sha256`` of every storyboard frame in the batch.
    Canonical hashing sorts keys, so the approval binds each frame to its
    shot — frames cannot be swapped between shots after approval."""
    for shot_id, sha in frames.items():
        if not isinstance(shot_id, str) or not shot_id or not isinstance(sha, str) or len(sha) != 64:
            raise ValueError(f"storyboard batch entry {shot_id!r}: {sha!r} is not shot_id -> sha256")
    return {"storyboard_frames": dict(frames)}


# ---- project config binding (PLAN §0) ----
#
# One verification path: lib.project_config.load_verified_project_config is
# what tools use in preflight and what enforcement uses here. Enforcement adds
# the decision_log ``approval_policy`` requirement (the writer's recorded
# ruling) on top of the receipt binding.

UPLOADING_STAGES = {"headshots", "visual_bible", "assets"}


def _config_decision_bound(decisions: list[dict[str, Any]], digest: str) -> bool:
    for d in decisions:
        if d.get("category") != "approval_policy" or d.get("user_approved") is not True:
            continue
        for field in ("subject", "reason", "selected", "question_id"):
            value = d.get(field)
            if isinstance(value, str) and digest in _CONFIG_DIGEST_RE.findall(value):
                return True
    return False


def _require_config_binding(
    project_dir: Path, decisions: list[dict[str, Any]], stage: str
) -> "VerifiedProjectConfig":
    from lib.project_config import (
        ProjectConfigError,
        load_verified_project_config,
        read_project_config,
    )

    if not (Path(project_dir) / PROJECT_CONFIG_FILENAME).is_file():
        _fail(
            f"stage {stage!r} requires {PROJECT_CONFIG_FILENAME} in the project "
            f"directory (budget_usd_cap, wall_time_minutes, cast_cap, "
            f"provider_egress, default_video_endpoint) bound to a human "
            f"approval — none found."
        )
    try:
        _, digest = read_project_config(project_dir)
    except ProjectConfigError as exc:
        if stage in UPLOADING_STAGES and "provider_egress" in str(exc):
            _fail(
                f"{stage} cannot start: project.yaml has no valid provider_egress "
                f"ruling. The writer must explicitly consent that prompts and "
                f"reference images leave the machine for the provider. ({exc})"
            )
        _fail(str(exc))
    if not _config_decision_bound(decisions, digest):
        _fail(
            f"project.yaml digest {digest} has no approval: an approval_policy "
            f"decision_log entry with user_approved=true carrying "
            f"'config_sha256: {digest}' is required. Changing budget, cast cap "
            f"or egress requires a new human approval."
        )
    try:
        verified = load_verified_project_config(project_dir)
    except ProjectConfigError as exc:
        _fail(
            f"project.yaml digest {digest} has no verified approval receipt "
            f"(kind='config', record {{config_sha256: <digest>}}) — the "
            f"decision_log entry alone is self-attested; the receipt must be "
            f"recorded through the human gate. ({exc})"
        )
    if stage in UPLOADING_STAGES:
        # Prompts AND reference images leave the machine at these stages.
        try:
            verified.require_egress("fal", "prompts", "reference_images")
        except ProjectConfigError as exc:
            _fail(
                f"{stage} cannot start: provider_egress does not cover what "
                f"the stage uploads — {exc}"
            )
    return verified


# ---- migrated artifacts need a receipted human review (PLAN §2; fix #10) ----

def artifact_review_digest(artifact: dict[str, Any]) -> str:
    """Canonical hash of an artifact minus ``migration_status`` — the review
    receipt survives the director flipping needs_review -> ok."""
    from lib.canonical_json import record_sha256

    return record_sha256({k: v for k, v in artifact.items() if k != "migration_status"})


def _find_artifact_review(project_dir: Path, name: str, artifact: dict[str, Any]) -> dict[str, Any] | None:
    """Latest verified artifact_review receipt bound to this exact artifact."""
    from lib import gates
    from lib.receipts import approvals_path, project_id_for, recover_pending_approvals
    from lib.state_io import read_jsonl

    recover_pending_approvals(project_dir)
    digest = artifact_review_digest(artifact)
    expected_project = project_id_for(project_dir)
    for row in reversed(list(read_jsonl(approvals_path(project_dir)))):
        if not isinstance(row, dict) or row.get("kind") != "artifact_review":
            continue
        if row.get("project_id") != expected_project:
            continue
        if (
            row.get("artifact_type") == name
            and row.get("artifact_version") == _version(artifact)
            and row.get("artifact_digest") == digest
            and row.get("migration_status") == "reviewed"
            and gates.verify_receipt(row)
        ):
            return row
    return None


def _check_migrated_artifacts(project_dir: Path, artifacts: dict[str, Any]) -> None:
    """At completion, every produced artifact that came through a migration
    (carries ``migration_status``) needs an ``artifact_review`` receipt bound
    to {artifact_type, artifact_version, artifact_digest, migration_status:
    reviewed}; a still-``needs_review`` artifact is rejected even with the
    receipt until the director flips it to ``ok``."""
    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict) or "migration_status" not in artifact:
            continue
        status = artifact.get("migration_status")
        receipt = _find_artifact_review(project_dir, name, artifact)
        if receipt is None:
            _fail(
                f"{name} was migrated (migration_status={status}) and has no "
                f"verified artifact_review receipt bound to {{artifact_type: "
                f"{name!r}, artifact_version: {_version(artifact)!r}, "
                f"artifact_digest: {artifact_review_digest(artifact)}, "
                f"migration_status: 'reviewed'}}. A human must review the "
                f"regenerated artifact at the gate before the stage completes."
            )
        if status == "needs_review":
            _fail(
                f"{name} is reviewed (receipt {receipt.get('receipt_id')}) but "
                f"still carries migration_status=needs_review — the director "
                f"flips it to 'ok' after the review receipt is recorded."
            )


# ---- ImageRef integrity: path safety, content address, synthetic-only ----

def _generation_receipt_rows(project_dir: Path) -> list[dict[str, Any]]:
    """Only signed + ledgered rows count; a row appended by hand is invisible."""
    from lib.receipts import verified_generation_receipts

    return verified_generation_receipts(project_dir)


def _safe_file_sha256(project_dir: Path, raw_path: str, label: str) -> str:
    from lib.pathsafe import PathSafetyError, resolve_input, sha256_file

    try:
        resolved = resolve_input(raw_path, project_dir)
    except PathSafetyError as exc:
        _fail(f"{label} path {raw_path!r} is not a safe project-local file: {exc}")
    if not resolved.is_file():
        _fail(f"{label} path {raw_path!r} is not a regular file.")
    return sha256_file(resolved)


def _check_image_ref(
    project_dir: Path, label: str, ref: dict[str, Any], receipts: list[dict[str, Any]]
) -> None:
    asset_id = ref.get("asset_id")
    actual = _safe_file_sha256(project_dir, str(ref.get("path", "")), label)
    if actual != asset_id:
        _fail(
            f"{label} content hash mismatch: file {ref.get('path')!r} hashes to "
            f"{actual} but asset_id is {asset_id} — canon images are "
            f"content-addressed; a swapped file is not the approved image."
        )
    provenance = ref.get("provenance") or {}
    receipt_id = provenance.get("generation_receipt_id")
    matched = [
        r for r in receipts
        if r.get("output_sha256") == asset_id and r.get("receipt_id") == receipt_id
    ]
    if not matched:
        _fail(
            f"{label} has no generation receipt (receipt_id {receipt_id!r}, "
            f"output_sha256 {asset_id}) in generation-receipts.jsonl — only "
            f"pipeline-generated (synthetic) images can be canon; an imported "
            f"image has no receipt and is rejected. Rows without a valid "
            f"signature and generation-ledger entry do not count."
        )
    _check_provenance_fields(label, provenance, matched[-1])


# ImageRef.provenance field -> signed generation-receipt field. Every field the
# artifact states is compared against the receipt (Codex R2 #7); the receipt
# is the only source of truth for what generated the image.
_PROVENANCE_FIELD_MAP = {
    "generator_kind": "generator_kind",
    "model_endpoint": "model_endpoint",
    "prompt": "prompt",
    "seed": "seed",
    "provider_request_id": "provider_request_id",
    "tool": "local_tool",
    "local_tool": "local_tool",
    "tool_version": "local_tool_version",
    "local_tool_version": "local_tool_version",
    "parameters_hash": "parameters_hash",
    "input_asset_ids": "input_asset_ids",
    # imported branch (Slice A'): the receipt is the attested import itself.
    "origin_tool": "origin_tool",
    "attestation_receipt_id": "attestation_receipt_id",
    "normalized_pixel_hash": "output_sha256",
    "import_receipt_id": "receipt_id",
}
_PROVENANCE_REQUIRED = {
    "model": ("generator_kind", "model_endpoint", "prompt"),
    "local": ("generator_kind", "tool", "tool_version", "parameters_hash", "input_asset_ids"),
    "imported": ("generator_kind", "origin_tool", "attestation_receipt_id", "import_receipt_id", "normalized_pixel_hash"),
}


def _check_provenance_fields(label: str, provenance: dict[str, Any], receipt: dict[str, Any]) -> None:
    kind = provenance.get("generator_kind")
    required = _PROVENANCE_REQUIRED.get(kind)
    if required is None:
        _fail(f"{label} provenance.generator_kind {kind!r} is not 'model', 'local' or 'imported'.")
    for field in required:
        if field not in provenance:
            _fail(f"{label} provenance lacks {field!r}, which a {kind} generation must state.")
    for field, receipt_field in _PROVENANCE_FIELD_MAP.items():
        if field not in provenance:
            continue
        stated = provenance[field]
        recorded = receipt.get(receipt_field)
        if isinstance(stated, list) or isinstance(recorded, list):
            stated, recorded = list(stated or []), list(recorded or [])
        if stated != recorded:
            _fail(
                f"{label} provenance.{field} {stated!r} does not match the signed "
                f"generation receipt {receipt.get('receipt_id')!r} ({receipt_field} "
                f"{recorded!r}) — provenance is whatever the receipt recorded at "
                f"generation time; an edited artifact does not change it."
            )


def _iter_image_refs(bible: dict[str, Any]):
    for ch in bible.get("characters", []):
        cid = ch.get("id")
        yield f"character {cid!r} hero", ch.get("hero") or {}
        for role in sheet_roles(ch):
            yield f"character {cid!r} sheet.{role}", (ch.get("sheet") or {}).get(role) or {}
    for loc in bible.get("locations", []):
        lid = loc.get("id")
        yield f"location {lid!r} establishing", loc.get("establishing") or {}
        for i, angle in enumerate(loc.get("angles") or []):
            yield f"location {lid!r} angles[{i}]", angle or {}
    poster = bible.get("poster")
    if isinstance(poster, dict):
        for role in ("key_art", "title_card", "poster_final"):
            yield f"poster {role}", poster.get(role) or {}


def _approved_entries(bible: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {
        e.get("id"): e
        for e in bible.get(key, [])
        if isinstance(e, dict) and e.get("status") == "approved" and e.get("id")
    }


def approved_entity_ids(bible: dict[str, Any]) -> set[str]:
    return set(_approved_entries(bible, "characters")) | set(_approved_entries(bible, "locations"))


def approved_image_owners(bible: dict[str, Any]) -> dict[str, str]:
    """asset_id -> entity id for every ImageRef of an approved character/location."""
    owners: dict[str, str] = {}
    for cid, ch in _approved_entries(bible, "characters").items():
        refs = [ch.get("hero") or {}] + [
            (ch.get("sheet") or {}).get(role) or {} for role in sheet_roles(ch)
        ]
        for ref in refs:
            if ref.get("asset_id"):
                owners[ref["asset_id"]] = cid
    for lid, loc in _approved_entries(bible, "locations").items():
        for ref in [loc.get("establishing") or {}] + list(loc.get("angles") or []):
            if ref.get("asset_id"):
                owners[ref["asset_id"]] = lid
    return owners


def _require_entry_receipt(
    project_dir: Path, kinds: tuple[str, ...], entity_id: str, record: dict[str, Any],
    entry: dict[str, Any], label: str,
) -> None:
    from lib.canonical_json import record_sha256
    from lib.receipts import find_approval

    digest = record_sha256(record)
    receipt = None
    for kind in kinds:
        receipt = find_approval(project_dir, kind, entity_id=entity_id, record_sha256=digest)
        if receipt is not None:
            break
    if receipt is None:
        _fail(
            f"{label} is marked approved but no verified approval receipt "
            f"(kind in {list(kinds)}, entity_id {entity_id!r}) matches the "
            f"current record digest {digest}. Either the human never approved "
            f"it at a gate, or the record (assets, prompt block, wardrobe "
            f"negative, palette) changed after approval — re-approve."
        )
    if receipt.get("receipt_id") != entry.get("approval_receipt_id"):
        _fail(
            f"{label} approval_receipt_id {entry.get('approval_receipt_id')!r} "
            f"does not name the verified receipt {receipt.get('receipt_id')!r}."
        )


def _canon_entity_ids(canon: dict[str, Any], key: str) -> set[str]:
    return {
        e.get("id") for e in canon.get(key, []) if isinstance(e, dict) and e.get("id")
    }


def _check_visual_bible(
    project_dir: Path,
    bible: dict[str, Any],
    proposal: dict[str, Any],
    canon: dict[str, Any],
    config: dict[str, Any],
    decisions: list[dict[str, Any]],
    status: str,
) -> None:
    palette = bible.get("palette") or {}
    if not palette.get("hues"):
        _fail("visual_bible.palette.hues is required — the palette is canon.")

    receipts = _generation_receipt_rows(project_dir)
    for label, ref in _iter_image_refs(bible):
        _check_image_ref(project_dir, label, ref, receipts)

    if status != "completed":
        return

    if proposal.get("migration_status") == "needs_review":
        _fail(
            "proposal_packet was migrated from 1.0 without a cast "
            "(migration_status=needs_review); the proposal director must "
            "re-emit it with cast before the visual bible can complete."
        )
    cast = proposal.get("cast") or {}
    char_ids = list(cast.get("character_ids") or [])
    loc_ids = list(cast.get("location_ids") or [])
    cap = config.get("cast_cap") or {}
    approved_chars = _approved_entries(bible, "characters")
    approved_locs = _approved_entries(bible, "locations")
    for kind, count, limit in (
        ("characters", max(len(char_ids), len(approved_chars)), cap.get("characters")),
        ("locations", max(len(loc_ids), len(approved_locs)), cap.get("locations")),
    ):
        if isinstance(limit, int) and count > limit:
            _fail(
                f"cast cap exceeded: {count} {kind} vs project.yaml cast_cap."
                f"{kind}={limit}. Raising the cap is a new human approval."
            )

    missing = [c for c in char_ids if c not in approved_chars]
    missing += [l for l in loc_ids if l not in approved_locs]
    if missing:
        _fail(
            f"visual_bible cannot complete: proposal_packet.cast entities "
            f"{missing} have no status=approved entry. Every cast member "
            f"needs an approved sheet."
        )
    extra = sorted(set(approved_chars) - set(char_ids)) + sorted(set(approved_locs) - set(loc_ids))
    if extra:
        _fail(
            f"visual_bible cannot complete: approved entries {extra} are not "
            f"in proposal_packet.cast — the approved set must equal the cast "
            f"exactly (cast_cap intent); drop them or re-emit the cast."
        )
    if (char_ids or loc_ids) and _version(canon) != "1.1":
        _fail(
            "visual_bible with a cast requires a 1.1 canon_packet "
            "(characters/locations carry ids); migrate the packet first."
        )
    unknown = sorted(set(char_ids) - _canon_entity_ids(canon, "characters"))
    unknown += sorted(set(loc_ids) - _canon_entity_ids(canon, "locations"))
    if unknown:
        _fail(
            f"visual_bible cannot complete: ids {unknown} exist in neither "
            f"canon_packet.characters nor canon_packet.locations — sheets are "
            f"built only for canon entities."
        )

    for cid, entry in approved_chars.items():
        _require_entry_receipt(
            project_dir, CHARACTER_APPROVAL_KINDS, cid,
            character_approval_record(entry, palette), entry, f"character {cid!r}",
        )
    for lid, entry in approved_locs.items():
        _require_entry_receipt(
            project_dir, ("location",), lid,
            location_approval_record(entry, palette), entry, f"location {lid!r}",
        )

    released = _valid_ruling_question_ids(decisions)
    for key in ("characters", "locations"):
        known = {e.get("id") for e in bible.get(key, []) if isinstance(e, dict)}
        for entry in bible.get(key, []):
            if entry.get("status") != "superseded":
                continue
            eid = entry.get("id")
            if entry.get("superseded_by") not in known:
                _fail(
                    f"superseded {key[:-1]} {eid!r} names replacement "
                    f"{entry.get('superseded_by')!r}, which is not in the bible."
                )
            if f"visual:{eid}" not in released:
                _fail(
                    f"{key[:-1]} {eid!r} is superseded without a canon_ruling "
                    f"decision (question_id 'visual:{eid}', user_approved=true, "
                    f"selected in options_considered). Visual canon changes "
                    f"only by logged ruling."
                )

    poster = bible.get("poster")
    if not isinstance(poster, dict):
        _fail(
            "visual_bible cannot complete without a poster (key_art, "
            "title_card, poster_final) — the poster is a required deliverable."
        )
    if poster.get("status") != "approved":
        _fail(
            f"poster status is {poster.get('status')!r} — a completed "
            f"visual_bible requires an approved poster with a receipt."
        )
    _require_entry_receipt(
        project_dir, ("poster",), POSTER_ENTITY_ID,
        poster_approval_record(poster, palette), poster, "poster",
    )


# ---- scene_plan 1.1 ----

def _check_scene_plan_v11(
    scene_plan: dict[str, Any], bible: dict[str, Any], status: str, default_endpoint: str
) -> None:
    if status == "completed" and scene_plan.get("migration_status") == "needs_review":
        _fail(
            "scene_plan was migrated from 1.0 (migration_status=needs_review): "
            "character_refs/location_ref/shots were not synthesized. The scene "
            "director must re-emit the plan and a human must approve it."
        )
    approved = approved_entity_ids(bible)
    scenes = [s for s in scene_plan.get("scenes", []) if isinstance(s, dict)]

    # The project default endpoint is the receipt-bound project.yaml value
    # (D4); a scene that departs from it must say why. Nothing is inferred
    # from the scenes themselves.
    for scene in scenes:
        sid = scene.get("id")
        endpoint = scene.get("model_endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            _fail(f"scene {sid!r} has no model_endpoint — model policy is scene-level.")
        override = scene.get("model_override")
        if endpoint != default_endpoint and override is None:
            _fail(
                f"scene {sid!r} uses model_endpoint {endpoint!r} but the "
                f"project default (project.yaml default_video_endpoint) is "
                f"{default_endpoint!r} — a per-scene departure needs "
                f"model_override {{endpoint, reason}}."
            )
        if override is not None:
            if not str(override.get("reason") or "").strip():
                _fail(f"scene {sid!r} model_override has no reason.")
            if override.get("endpoint") != endpoint:
                _fail(
                    f"scene {sid!r} model_override.endpoint "
                    f"{override.get('endpoint')!r} != model_endpoint {endpoint!r}."
                )
        refs = list(scene.get("character_refs") or [])
        if scene.get("location_ref"):
            refs.append(scene["location_ref"])
        if scene.get("entity_free") is True:
            if refs:
                _fail(
                    f"scene {sid!r} is entity_free but carries refs {refs} — "
                    f"drop the flag or the refs."
                )
            continue
        if not refs:
            _fail(
                f"scene {sid!r} has no character_refs/location_ref and is not "
                f"entity_free — a scene with no canon entity must say so explicitly."
            )
        unknown = [r for r in refs if r not in approved]
        if unknown:
            _fail(
                f"scene {sid!r} references {unknown}, which are not approved "
                f"visual_bible entries. Approved: {sorted(approved)}"
            )
        if not scene.get("shots"):
            _fail(f"scene {sid!r} has no shots[] — storyboard and takes are shot-level.")


# ---- assets 1.1 ----

def _check_assets_v11(
    project_dir: Path,
    manifest: dict[str, Any],
    scene_plan: dict[str, Any],
    bible: dict[str, Any],
) -> None:
    from lib.receipts import ReceiptError, find_generation, normalize_reference, require_storyboard_receipt

    if manifest.get("migration_status") == "needs_review":
        _fail(
            "asset_manifest was migrated from 1.0 (migration_status=needs_review) "
            "— its references are unstructured; regenerate under 1.1."
        )
    if _version(scene_plan) != "1.1":
        _fail("a 1.1 asset_manifest requires a 1.1 scene_plan (shots, entity refs).")

    shot_scene: dict[str, dict[str, Any]] = {}
    for scene in scene_plan.get("scenes", []):
        for shot in scene.get("shots") or []:
            shot_scene[shot.get("shot_id")] = scene
    owners = approved_image_owners(bible)

    assets = [a for a in manifest.get("assets", []) if isinstance(a, dict)]
    file_sha: dict[str, str] = {}
    generation_receipt: dict[str, dict[str, Any]] = {}
    for asset in assets:
        aid = asset.get("id")
        if asset.get("type") in {"image", "video"}:
            sha = _safe_file_sha256(project_dir, str(asset.get("path", "")), f"asset {aid!r}")
            file_sha[aid] = sha
            receipt = find_generation(project_dir, sha)
            if receipt is None:
                _fail(
                    f"asset {aid!r} ({asset.get('path')!r}, sha256 {sha}) has no "
                    f"generation receipt that verifies (signed + ledgered) — every "
                    f"image/video must be produced by a receipted pipeline tool."
                )
            generation_receipt[aid] = receipt

    # Storyboard frames first: one per shot, hashed, so shot_visual references
    # and the per-shot approval can be checked against them.
    storyboard_shots: dict[str, str] = {}
    for asset in assets:
        if asset.get("asset_class") != "storyboard_frame":
            continue
        shot_id = asset.get("shot_id")
        if shot_id in storyboard_shots:
            _fail(f"shot {shot_id!r} has more than one storyboard_frame — exactly one per shot.")
        storyboard_shots[shot_id] = file_sha.get(asset.get("id"), "")

    spending_shots: set[str] = set()
    for asset in assets:
        aid = asset.get("id")
        klass = asset.get("asset_class")
        if klass not in {"shot_visual", "storyboard_frame"}:
            continue
        shot_id = asset.get("shot_id")
        scene = shot_scene.get(shot_id)
        if scene is None:
            _fail(f"asset {aid!r} names shot_id {shot_id!r}, which is not in the scene plan.")
        if asset.get("scene_id") != scene.get("id"):
            _fail(
                f"asset {aid!r} is filed under scene_id {asset.get('scene_id')!r} but its "
                f"shot {shot_id!r} belongs to scene {scene.get('id')!r} in the scene plan."
            )
        continuity = asset.get("continuity") or {}
        applied = [r for r in continuity.get("references_applied") or [] if isinstance(r, dict)]
        entity_refs = [r for r in applied if r.get("role") != "storyboard"]
        board_refs = [r for r in applied if r.get("role") == "storyboard"]
        if scene.get("entity_free") is not True:
            required = set(scene.get("character_refs") or [])
            if scene.get("location_ref"):
                required.add(scene["location_ref"])
            covered = {r.get("visual_bible_entity_id") for r in entity_refs}
            missing = sorted(required - covered)
            if missing:
                _fail(
                    f"asset {aid!r} (shot {shot_id!r}) depicts {sorted(required)} "
                    f"but references_applied covers only {sorted(covered)} — "
                    f"missing approved sheets for {missing}."
                )
        for r in entity_refs:
            owner = owners.get(r.get("asset_id"))
            if owner is None or owner != r.get("visual_bible_entity_id"):
                _fail(
                    f"asset {aid!r} references_applied asset {r.get('asset_id')} "
                    f"for {r.get('visual_bible_entity_id')!r} is not an approved "
                    f"visual_bible ImageRef of that entity."
                )
        if klass == "storyboard_frame":
            if board_refs:
                _fail(f"storyboard_frame {aid!r} cannot itself cite a storyboard reference.")
            continue

        # shot_visual
        for r in board_refs:
            if r.get("shot_id") != shot_id:
                _fail(
                    f"asset {aid!r} (shot {shot_id!r}) cites the storyboard of shot "
                    f"{r.get('shot_id')!r} — a take is conditioned only on its own shot's frame."
                )
            board_sha = storyboard_shots.get(shot_id)
            if not board_sha or r.get("asset_id") != board_sha:
                _fail(
                    f"asset {aid!r} storyboard reference asset_id {r.get('asset_id')} "
                    f"is not the storyboard_frame recorded for shot {shot_id!r} "
                    f"(sha256 {board_sha or 'none'})."
                )
        if asset.get("usage_status") == "rejected":
            continue
        # Candidate OR selected: spend happened, so approval must have preceded it.
        spending_shots.add(shot_id)
        board_sha = storyboard_shots.get(shot_id)
        if not board_sha:
            _fail(
                f"shot_visual {aid!r} exists for shot {shot_id!r} but that shot has "
                f"no storyboard_frame — storyboards are approved as a batch before "
                f"any video call."
            )
        try:
            require_storyboard_receipt(project_dir, shot_id, board_sha)
        except ReceiptError as exc:
            _fail(
                f"shot_visual {aid!r} (usage_status {asset.get('usage_status')!r}) "
                f"was generated without a verified storyboard_batch approval "
                f"receipt covering shot {shot_id!r} -> {board_sha}: {exc}"
            )
        # The approved frame must have been IN the payload (Codex R2 #4): exactly
        # one storyboard citation (already checked to be this shot's approved
        # frame), and the manifest's references_applied must equal, in order,
        # the list the tool sealed into the signed generation receipt.
        if len(board_refs) != 1:
            _fail(
                f"shot_visual {aid!r} (shot {shot_id!r}) cites {len(board_refs)} storyboard "
                f"references — a take is conditioned on exactly one: its shot's approved frame, "
                f"packed last by the video tool."
            )
        receipt = generation_receipt.get(aid) or {}
        recorded = receipt.get("references_applied")
        if not isinstance(recorded, list):
            _fail(
                f"shot_visual {aid!r} generation receipt {receipt.get('receipt_id')!r} records no "
                f"references_applied — takes are generated only through the governed video tool, "
                f"which seals the uploaded reference list into the receipt."
            )
        try:
            stated = [normalize_reference(r) for r in applied]
            sealed = [normalize_reference(r) for r in recorded]
        except ValueError as exc:
            _fail(f"shot_visual {aid!r} has a malformed reference: {exc}")
        if stated != sealed:
            _fail(
                f"shot_visual {aid!r} continuity.references_applied does not equal the reference "
                f"list sealed in generation receipt {receipt.get('receipt_id')!r} — manifest "
                f"{stated} vs receipt {sealed}. Record exactly what the tool reported in "
                f"metadata.references_applied (same objects, same order)."
            )
        if asset.get("usage_status") == "selected" and asset.get("model_endpoint") != scene.get("model_endpoint"):
            _fail(
                f"selected take {aid!r} for shot {shot_id!r} used "
                f"{asset.get('model_endpoint')!r} but scene {scene.get('id')!r} "
                f"locked {scene.get('model_endpoint')!r} — strategies never "
                f"mix within a scene (D4). Rejected candidates are ignored."
            )

    if spending_shots:
        missing_boards = sorted(s for s in shot_scene if s not in storyboard_shots)
        if missing_boards:
            _fail(
                f"takes exist for {sorted(spending_shots)} but shots "
                f"{missing_boards} have no storyboard_frame — storyboards are "
                f"approved as a batch before any video call."
            )


# ---------------------------------------------------------------------------
# authored-film 1.2: look_lock, headshots, look_ref / headshot_ref / lineage
# ---------------------------------------------------------------------------

def _is_look_lock_manifest(pin: Any) -> bool:
    return pin is not None and (getattr(pin, "name", None), getattr(pin, "version", None)) in LOOK_LOCK_MANIFESTS


def _is_qc_manifest(pin: Any) -> bool:
    return pin is not None and (getattr(pin, "name", None), getattr(pin, "version", None)) == QC_MANIFEST


def _cast_keys(proposal: dict[str, Any]) -> list[tuple[str, str]]:
    cast = proposal.get("cast") or {}
    keys = [("character", c) for c in cast.get("character_ids") or []]
    keys += [("location", l) for l in cast.get("location_ids") or []]
    return keys


def _active_looks(project_dir: Path) -> dict[tuple[str, str], Any]:
    from lib.look_ingest import LookIngestError, active_looks

    try:
        return active_looks(project_dir)
    except LookIngestError as exc:
        _fail(f"look_lock receipt chain is not a unique-tip chain: {exc}")
        return {}


def _active_headshots(project_dir: Path) -> dict[str, Any]:
    from lib.headshots import HeadshotError, active_headshots

    try:
        return active_headshots(project_dir)
    except HeadshotError as exc:
        _fail(f"headshot receipt chain is not a unique-tip chain: {exc}")
        return {}


def _check_look_packet_current(
    project_dir: Path, packet: dict[str, Any], proposal: dict[str, Any], stage: str,
    *, required: list[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], Any]:
    """Every REQUIRED entity (default: the whole proposal cast) has an entry
    whose look_hash is the active look, hashes its look_spec, names the
    active receipt, is generation-sufficient, and (trailer/teaser) is not a
    spoiler; every entry PRESENT in the packet is checked the same way (D18:
    a partial packet carries only ratified looks). Returns active looks."""
    from lib.canonical_json import record_sha256
    from lib.look_spec import LookSpecError, generation_sufficient, validate_look_spec

    active = _active_looks(project_dir)
    entries = {(e.get("entity_kind"), e.get("entity_id")): e for e in packet.get("looks", []) if isinstance(e, dict)}
    fmt = str((proposal.get("runtime_shape") or {}).get("format") or "")
    keys = list(_cast_keys(proposal) if required is None else required)
    keys += [k for k in entries if k not in keys]
    for key in keys:
        entry = entries.get(key)
        if entry is None:
            _fail(f"{stage}: cast entity {key} has no entry in look_packet — every cast entity needs a ratified look.")
        spec = entry.get("look_spec") or {}
        try:
            validate_look_spec(spec)
        except LookSpecError as exc:
            _fail(f"{stage}: look_spec for {key} is invalid: {exc}")
        if (spec.get("entity_kind"), spec.get("entity_id")) != key:
            _fail(f"{stage}: look_spec for {key} describes {(spec.get('entity_kind'), spec.get('entity_id'))}.")
        if record_sha256(spec) != entry.get("look_hash"):
            _fail(f"{stage}: look_packet entry {key} look_hash does not hash its look_spec.")
        current = active.get(key)
        if current is None:
            _fail(f"{stage}: no active look_lock receipt for {key} — ratification is the signed receipt (D12).")
        if current.look_hash != entry.get("look_hash") or current.receipt_id != entry.get("receipt_id"):
            _fail(
                f"{stage}: look_packet entry {key} carries look_hash {entry.get('look_hash')} / receipt "
                f"{entry.get('receipt_id')} but the active look is {current.look_hash} / {current.receipt_id}."
            )
        ok, why = generation_sufficient(spec)
        if not ok:
            _fail(f"{stage}: look for {key} cannot drive production: {why}.")
        if fmt in SPOILER_SENSITIVE_FORMATS and spec.get("spoiler") is True:
            _fail(f"{stage}: look for {key} is a spoiler; a {fmt} cast refuses spoiler looks.")
    return active


def _check_look_lock(project_dir: Path, packet: dict[str, Any], proposal: dict[str, Any], status: str) -> None:
    """D18: ``completed`` needs every proposal cast entity; ``in_progress`` /
    ``awaiting_human`` accept a PARTIAL packet whose present entries are all
    current. A packet claiming ``complete: true`` is held to the full cast."""
    if status == "completed":
        if packet.get("complete") is False:
            _fail("look_lock cannot complete with a look_packet marked complete: false — every cast entity needs a ratified look.")
        _check_look_packet_current(project_dir, packet, proposal, "look_lock")
        return
    _check_look_packet_current(
        project_dir, packet, proposal, "look_lock",
        required=None if packet.get("complete") is True else [],
    )


def _look_packet_on_disk(project_dir: Path) -> dict[str, Any]:
    """The look_packet carried by checkpoint_look_lock.json in ANY status
    (D18: an in_progress look_lock carries a partial packet). Entries are
    re-verified against the receipt chain by every consumer, so a partial
    or stale packet grants nothing by itself."""
    checkpoint = _read_json(project_dir / "checkpoint_look_lock.json") or {}
    packet = (checkpoint.get("artifacts") or {}).get("look_packet")
    return packet if isinstance(packet, dict) else {}


def _check_lineage(project_dir: Path, label: str, asset_id: str, receipts_by_sha: dict[str, dict[str, Any]]) -> None:
    from lib.reference_import import ReferenceImportError, verify_lineage

    try:
        verify_lineage(project_dir, asset_id, receipts_by_sha=receipts_by_sha, label=label)
    except ReferenceImportError as exc:
        _fail(str(exc))


def _receipt_for(ref: dict[str, Any], receipts_by_sha: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return receipts_by_sha.get(ref.get("asset_id"), {})


def _require_look_refs(label: str, ref: dict[str, Any], receipt: dict[str, Any], key: tuple[str, str], look_hash: str) -> None:
    """A model/local canon image of an entity must have been generated with
    look_refs naming that entity's active look (never entity-free)."""
    if (ref.get("provenance") or {}).get("generator_kind") == "imported":
        return
    refs = receipt.get("look_refs") or []
    wanted = {"entity_kind": key[0], "entity_id": key[1], "look_hash": look_hash}
    if wanted not in refs:
        _fail(
            f"{label}: generation receipt {receipt.get('receipt_id')!r} does not bind look_ref {wanted} — "
            f"visual_bible-stage generation is never entity-free; the governed tool verifies and seals look_refs."
        )


def _check_sheet_prompt_recipe(label: str, ref: dict[str, Any], receipt: dict[str, Any], look_hash: str) -> None:
    """A sheet ImageRef generated under a builder ``prompt_recipe`` (sealed in
    its receipt) must be the rendering that recipe describes: the receipt's
    ``prompt`` — the text the provider actually received, and what the
    ImageRef's ``provenance.prompt`` is compared against — hashes to
    ``rendered_sha256``, and the recipe names the active look. Receipts
    without a recipe (legacy sheets) are untouched."""
    recipe = receipt.get("prompt_recipe")
    if not isinstance(recipe, dict):
        return
    stated = (ref.get("provenance") or {}).get("prompt")
    prompt = receipt.get("prompt") if stated is None else stated
    rendered = hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()
    if rendered != recipe.get("rendered_sha256"):
        _fail(
            f"{label}: prompt does not hash to the sealed prompt_recipe.rendered_sha256 "
            f"{recipe.get('rendered_sha256')!r} in receipt {receipt.get('receipt_id')!r} — sheet prompts are "
            f"builder renderings, never edited text."
        )
    if recipe.get("look_hash") != look_hash:
        _fail(f"{label}: receipt prompt_recipe.look_hash {recipe.get('look_hash')!r} is not the active look {look_hash}.")


def _check_headshots(project_dir: Path, packet: dict[str, Any], proposal: dict[str, Any], status: str) -> None:
    from lib.headshots import prompt_recipe_sha256

    char_ids = list((proposal.get("cast") or {}).get("character_ids") or [])
    entries = {e.get("entity_id"): e for e in packet.get("characters", []) if isinstance(e, dict)}
    state = packet.get("state")
    if status == "completed" and state != "approved":
        _fail("headshots cannot complete with a pending headshot_packet — every cast character needs an approved hero.")
    if status != "completed" and state not in {"pending", "approved"}:
        _fail(f"headshot_packet.state {state!r} is not pending|approved.")
    extra = sorted(set(entries) - set(char_ids))
    if extra:
        _fail(f"headshot_packet carries characters {extra} outside proposal_packet.cast.character_ids.")
    # D18: completion needs a look for every cast character; a pending /
    # partial packet needs a current look only for the characters it carries.
    wanted = char_ids if status == "completed" else [c for c in char_ids if c in entries]
    active_looks = _check_look_packet_current(
        project_dir, _look_packet_on_disk(project_dir), proposal, "headshots",
        required=[("character", c) for c in wanted],
    )
    receipts = {r["output_sha256"]: r for r in _generation_receipt_rows(project_dir)}
    for cid in char_ids:
        entry = entries.get(cid)
        if entry is None:
            if status == "completed":
                _fail(f"headshots cannot complete: cast character {cid!r} has no approved hero.")
            continue
        look = active_looks.get(("character", cid))
        look_ref = entry.get("look_ref") or {}
        if look is None or look_ref.get("look_hash") != look.look_hash or look_ref.get("receipt_id") != look.receipt_id:
            _fail(f"headshot entry {cid!r} look_ref is not the active look ({look.look_hash if look else None}).")
        recipe = entry.get("prompt_recipe")
        if isinstance(recipe, dict) and recipe.get("look_hash") != look.look_hash:
            _fail(f"headshot entry {cid!r} prompt_recipe.look_hash is not the active look.")
        if state == "pending":
            for i, cand in enumerate(entry.get("candidates") or []):
                label = f"headshot candidate {cid!r}[{i}]"
                _check_image_ref(project_dir, label, cand, list(receipts.values()))
                _check_lineage(project_dir, label, cand.get("asset_id"), receipts)
                _require_look_refs(label, cand, _receipt_for(cand, receipts), ("character", cid), look.look_hash)
            continue
        hero = entry.get("hero") or {}
        label = f"headshot hero {cid!r}"
        _check_image_ref(project_dir, label, hero, list(receipts.values()))
        _check_lineage(project_dir, label, hero.get("asset_id"), receipts)
        _require_look_refs(label, hero, _receipt_for(hero, receipts), ("character", cid), look.look_hash)
        current = _active_headshots(project_dir).get(cid)
        if current is None:
            _fail(f"{label} has no active headshot receipt — faces are approved only through the selection gate.")
        if current.receipt_id != entry.get("approval_receipt_id") or current.asset_id != hero.get("asset_id"):
            _fail(
                f"{label} names receipt {entry.get('approval_receipt_id')!r} / asset {hero.get('asset_id')} but the "
                f"active headshot is receipt {current.receipt_id} / asset {current.asset_id}."
            )
        rec = current.record
        if rec.get("look_hash") != look.look_hash:
            _fail(f"{label} was approved against look {rec.get('look_hash')}, not the active look {look.look_hash}.")
        if rec.get("origin") != entry.get("origin") or rec.get("normalized_pixel_hash") != entry.get("normalized_pixel_hash"):
            _fail(f"{label} origin/pixel hash differ from the signed headshot record.")
        if rec.get("import_receipt_id") != entry.get("import_receipt_id"):
            _fail(f"{label} import_receipt_id differs from the signed headshot record.")
        if rec.get("prompt_recipe_sha256") != prompt_recipe_sha256(recipe):
            _fail(f"{label} prompt_recipe does not match the recipe hash sealed in the headshot record.")
        if entry.get("candidates_checkpoint_digest") not in (None, rec.get("candidates_checkpoint_digest")):
            _fail(f"{label} candidates_checkpoint_digest differs from the signed headshot record.")
        provenance = hero.get("provenance") or {}
        if entry.get("origin") == "imported_synthetic":
            if provenance.get("generator_kind") != "imported" or provenance.get("generation_receipt_id") != entry.get("import_receipt_id"):
                _fail(f"{label} is imported_synthetic but its provenance/import_receipt_id do not cite the imported generation receipt.")
        elif provenance.get("generator_kind") == "imported":
            _fail(f"{label} has imported provenance but origin {entry.get('origin')!r}.")


def _load_headshot_packet(
    project_dir: Path, artifacts: dict[str, Any], *, statuses: frozenset[str] = frozenset({"completed"})
) -> dict[str, Any] | None:
    checkpoint = _read_json(project_dir / "checkpoint_headshots.json")
    if checkpoint and checkpoint.get("status") in statuses and isinstance(checkpoint.get("artifacts"), dict):
        packet = checkpoint["artifacts"].get("headshot_packet")
        if isinstance(packet, dict):
            return packet
    packet = artifacts.get("headshot_packet")
    return packet if isinstance(packet, dict) else None


def _bible_entity_keys(bible: Any) -> list[tuple[str, str]]:
    """(kind, id) of every non-superseded entry carried by a visual_bible."""
    keys: list[tuple[str, str]] = []
    if not isinstance(bible, dict):
        return keys
    for kind, key in (("character", "characters"), ("location", "locations")):
        for entry in bible.get(key) or []:
            if isinstance(entry, dict) and entry.get("status") != "superseded" and entry.get("id"):
                keys.append((kind, str(entry["id"])))
    return keys


def _check_visual_bible_entry_v12(
    project_dir: Path, artifacts: dict[str, Any], proposal: dict[str, Any], status: str = "completed",
) -> tuple[dict[tuple[str, str], Any], dict[str, Any]]:
    """visual_bible: look_packet and headshot_packet must be current.

    D18: ``completed`` needs every cast entity's look and every cast
    character's approved headshot from a completed headshots checkpoint;
    ``in_progress`` / ``awaiting_human`` need them only for the entities the
    bible carries (a sheet for character A needs A's approved face, not
    B's; a location plate needs that location's look), read from a
    look_lock / headshots checkpoint in any status. Returns (active looks,
    active headshots)."""
    look_packet = _look_packet_on_disk(project_dir)
    if not look_packet and isinstance(artifacts.get("look_packet"), dict):
        look_packet = artifacts["look_packet"]
    if not look_packet:
        _fail("visual_bible cannot start: no look_packet from a look_lock checkpoint.")
    complete = status == "completed"
    present = _bible_entity_keys(artifacts.get("visual_bible"))
    active_looks = _check_look_packet_current(
        project_dir, look_packet, proposal, "visual_bible", required=None if complete else present,
    )
    packet = _load_headshot_packet(
        project_dir, artifacts,
        statuses=frozenset({"completed"}) if complete else frozenset({"completed", "awaiting_human", "in_progress"}),
    )
    if complete and (not isinstance(packet, dict) or packet.get("state") != "approved"):
        _fail("visual_bible cannot start: no approved headshot_packet from a completed headshots checkpoint.")
    active_headshots = _active_headshots(project_dir)
    entries = (
        {e.get("entity_id"): e for e in packet.get("characters", []) if isinstance(e, dict)}
        if isinstance(packet, dict) and packet.get("state") == "approved" else {}
    )
    cast_chars = list((proposal.get("cast") or {}).get("character_ids") or [])
    wanted = cast_chars if complete else [cid for kind, cid in present if kind == "character"]
    for cid in wanted:
        entry = entries.get(cid)
        current = active_headshots.get(cid)
        if current is None or (complete and entry is None):
            _fail(f"visual_bible cannot start: character {cid!r} has no approved current headshot.")
        if entry is not None and (
            current.receipt_id != entry.get("approval_receipt_id")
            or current.asset_id != (entry.get("hero") or {}).get("asset_id")
        ):
            _fail(f"visual_bible cannot start: headshot for {cid!r} was superseded or retired; re-approve it.")
        look = active_looks.get(("character", cid))
        if look is None or current.look_hash != look.look_hash:
            _fail(f"visual_bible cannot start: headshot for {cid!r} predates the active look; re-approve it.")
    return active_looks, active_headshots


def _check_visual_bible_v12(
    project_dir: Path, bible: dict[str, Any], proposal: dict[str, Any],
    active_looks: dict[tuple[str, str], Any], active_headshots: dict[str, Any],
    *, qc_required: bool = False, status: str = "completed",
) -> None:
    if _version(bible) != "1.1":
        _fail("authored-film 1.2 requires a 1.1 visual_bible (look_ref, sheet_revision, prompt_recipe).")
    receipts = {r["output_sha256"]: r for r in _generation_receipt_rows(project_dir)}
    config = None
    if qc_required:
        from lib.project_config import ProjectConfigError, load_verified_project_config

        try:
            config = load_verified_project_config(project_dir)
        except ProjectConfigError as exc:
            _fail(f"visual_bible under authored-film 1.3 needs a verified 1.1 project config for sheet QC: {exc}")
    for kind, key in (("character", "characters"), ("location", "locations")):
        for entry in bible.get(key, []):
            if not isinstance(entry, dict) or entry.get("status") == "superseded":
                continue
            eid = entry.get("id")
            look = active_looks.get((kind, eid))
            look_ref = entry.get("look_ref") or {}
            if look is None:
                _fail(f"{kind} {eid!r} has no active look; sheets are built only from ratified looks.")
            if look_ref.get("look_hash") != look.look_hash or look_ref.get("receipt_id") != look.receipt_id:
                _fail(
                    f"{kind} {eid!r} look_ref.look_hash {look_ref.get('look_hash')} is not the active look "
                    f"{look.look_hash} — the look was superseded; a new sheet_revision is required."
                )
            recipe = entry.get("prompt_recipe")
            if isinstance(recipe, dict) and recipe.get("look_hash") != look.look_hash:
                _fail(f"{kind} {eid!r} prompt_recipe.look_hash is not the active look.")
            if kind == "character":
                # D19 R1#11: ONE verifier shared with the gate (construct + pre-commit).
                from lib.sheet_verify import verify_character_sheet

                verify_character_sheet(
                    project_dir, entry, active_look=look, active_headshot=active_headshots.get(eid),
                    receipts_by_sha=receipts, qc_required=qc_required, config=config,
                    qc_must_be_present=qc_required and (status in ("awaiting_human", "completed") or entry.get("status") == "approved"),
                )
            else:
                refs = [("establishing", entry.get("establishing") or {})] + [
                    (f"angles[{i}]", a or {}) for i, a in enumerate(entry.get("angles") or [])
                ]
                for role, ref in refs:
                    label = f"location {eid!r} {role}"
                    _check_lineage(project_dir, label, ref.get("asset_id"), receipts)
                    _require_look_refs(label, ref, _receipt_for(ref, receipts), (kind, eid), look.look_hash)
    poster = bible.get("poster") or {}
    for role in ("key_art", "title_card", "poster_final"):
        ref = poster.get(role) or {}
        if ref.get("asset_id"):
            _check_lineage(project_dir, f"poster {role}", ref["asset_id"], receipts)


def enforce_authored_canon(
    pipeline_dir: Path,
    project_id: str,
    stage: str,
    status: str,
    artifacts: dict[str, Any],
    *,
    pin: Any = None,
) -> None:
    """Entry point called by lib.checkpoint.write_checkpoint before any state
    is persisted. Raises CheckpointValidationError on violation. ``pin`` is
    the project's pinned manifest tuple (lib.pipeline_pin.PinnedPipeline);
    look-lock / headshot / lineage rules apply only under authored-film 1.2."""
    look_lock_profile = _is_look_lock_manifest(pin)
    if look_lock_profile and status == "in_progress":
        # D18 per-entity flow: an in_progress look_lock / headshots checkpoint
        # may carry a PARTIAL packet, but every entry it carries is held to
        # the same receipt rules as at completion. visual_bible stage-entry
        # preflight (R2#12, R2#5): no sheet work starts without a current
        # look and an approved, current headshot for every entity the bible
        # carries.
        project_dir = pipeline_dir / project_id
        proposal = _load_stage_artifact(pipeline_dir, project_id, "proposal", "proposal_packet", artifacts) or {}
        if stage == "look_lock" and isinstance(artifacts.get("look_packet"), dict):
            _check_look_lock(project_dir, artifacts["look_packet"], proposal, status)
        elif stage == "headshots" and isinstance(artifacts.get("headshot_packet"), dict):
            _check_headshots(project_dir, artifacts["headshot_packet"], proposal, status)
        elif stage == "visual_bible":
            active_looks, active_headshots = _check_visual_bible_entry_v12(project_dir, artifacts, proposal, status)
            if isinstance(artifacts.get("visual_bible"), dict):
                _check_visual_bible_v12(project_dir, artifacts["visual_bible"], proposal, active_looks, active_headshots,
                                        qc_required=_is_qc_manifest(pin), status=status)
    if status not in {"completed", "awaiting_human"}:
        return

    canon = _load_canon(pipeline_dir, project_id, artifacts)

    if stage == "canon_ingest":
        if canon is not None:
            _check_packet_integrity(canon)
        return

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

    project_dir = pipeline_dir / project_id
    if status == "completed":
        _check_migrated_artifacts(project_dir, artifacts)

    config = None
    if stage in CONFIG_BOUND_STAGES:
        config = _require_config_binding(project_dir, decisions, stage)

    def _bible() -> dict[str, Any]:
        bible = _load_stage_artifact(
            pipeline_dir, project_id, "visual_bible", "visual_bible", artifacts
        )
        if bible is None:
            _fail(f"stage {stage!r} requires the visual_bible from a completed visual_bible checkpoint.")
        return bible

    if stage == "visual_bible":
        proposal = _load_stage_artifact(
            pipeline_dir, project_id, "proposal", "proposal_packet", artifacts
        ) or {}
        _check_visual_bible(
            project_dir, artifacts.get("visual_bible", {}), proposal, canon,
            config.data if config else {}, decisions, status,
        )
        if look_lock_profile:
            active_looks, active_headshots = _check_visual_bible_entry_v12(project_dir, artifacts, proposal, status)
            _check_visual_bible_v12(
                project_dir, artifacts.get("visual_bible", {}), proposal, active_looks, active_headshots,
                qc_required=_is_qc_manifest(pin), status=status,
            )
    elif stage == "look_lock":
        proposal = _load_stage_artifact(pipeline_dir, project_id, "proposal", "proposal_packet", artifacts) or {}
        _check_look_lock(project_dir, artifacts.get("look_packet", {}), proposal, status)
    elif stage == "headshots":
        proposal = _load_stage_artifact(pipeline_dir, project_id, "proposal", "proposal_packet", artifacts) or {}
        _check_headshots(project_dir, artifacts.get("headshot_packet", {}), proposal, status)
    elif stage == "script":
        _check_script(artifacts.get("script", {}), canon, ids, status)
    elif stage == "scene_plan":
        scene_plan = artifacts.get("scene_plan", {})
        _check_scene_plan(scene_plan, ids)
        if _version(scene_plan) == "1.1":
            _check_scene_plan_v11(
                scene_plan, _bible(), status,
                config.default_video_endpoint if config else "",
            )
    elif stage == "assets":
        manifest = artifacts.get("asset_manifest", {})
        _check_assets(manifest, canon, ids)
        if _version(manifest) == "1.1":
            scene_plan = _load_stage_artifact(
                pipeline_dir, project_id, "scene_plan", "scene_plan", artifacts
            ) or {}
            _check_assets_v11(project_dir, manifest, scene_plan, _bible())
    elif stage == "compose":
        _check_canon_pass(
            artifacts.get("final_review", {}), canon, strict=(status == "completed")
        )
        if status == "completed":
            _check_render_outputs(
                pipeline_dir / project_id, artifacts.get("render_report", {}), canon
            )

"""Behavioral contract tests for the authored-film pipeline.

These reproduce the peer-review probes: manifest-declared stage contracts
must be runtime contracts, canon rulings must be schema-valid and atomic,
blocking questions must gate advancement, canon evidence must be structured,
and compose must prove a real render with a passing canon pass.
"""

import json
import shutil
import subprocess

import pytest

from lib.checkpoint import (
    CheckpointValidationError,
    init_project,
    write_checkpoint,
)
from schemas.artifacts import validate_artifact
from tests.contracts.test_phase0_contracts import sample_artifact

PIPELINE = "authored-film"
SHA = "a" * 64


# ---------------------------------------------------------------------------
# Fixtures — minimal schema-valid authored-film artifacts
# ---------------------------------------------------------------------------

def canon_packet(*, blocking_question: bool = False, resolved: bool = False) -> dict:
    packet = {
        "version": "1.0",
        "project_title": "Test Feature",
        "format": "short",
        "source_documents": [
            {
                "path": "docs/outline.md",
                "doc_type": "outline",
                "authority": "writing_document",
                "sha256": SHA,
            },
            {
                "path": "atoms/lock-001.md",
                "doc_type": "canon_atom",
                "authority": "canon",
                "sha256": SHA,
            },
        ],
        "locks": [
            {
                "id": "lock-001",
                "decision": "The ending is bittersweet.",
                "source_path": "atoms/lock-001.md",
                "source_type": "canon_atom",
            }
        ],
        "protected_lines": [
            {"line": "We were never going home.", "source_path": "docs/outline.md"}
        ],
        "tone": {"tone_words": ["quiet", "tense"]},
        "structure": {
            "beats": [
                {"id": "beat-001", "summary": "Opening image establishes isolation."},
                {"id": "beat-002", "summary": "The turn: the truth lands."},
            ]
        },
        "open_questions": [],
    }
    if blocking_question:
        packet["open_questions"] = [
            {
                "id": "q-001",
                "question": "Does the narrator survive the final scene?",
                "blocking": True,
                "status": "resolved" if resolved else "open",
            }
        ]
    return packet


def canon_ruling_log(question_id: str = "q-001") -> dict:
    return {
        "version": "1.0",
        "project_id": "p",
        "decisions": [
            {
                "decision_id": "d-100",
                "stage": "proposal",
                "category": "canon_ruling",
                "subject": "Narrator survival in final scene",
                "question_id": question_id,
                "options_considered": [
                    {
                        "option_id": "survives",
                        "label": "Narrator survives",
                        "score": 1.0,
                        "reason": "Writer ruling at the proposal gate",
                    }
                ],
                "selected": "survives",
                "reason": "Writer answered at the canon gate.",
                "user_approved": True,
            }
        ],
    }


def plain_decision_log() -> dict:
    return {
        "version": "1.0",
        "project_id": "p",
        "decisions": [
            {
                "decision_id": "d-001",
                "stage": "proposal",
                "category": "renderer_family_selection",
                "subject": "Renderer family",
                "options_considered": [
                    {
                        "option_id": "cinematic-trailer",
                        "label": "Cinematic trailer",
                        "score": 0.9,
                        "reason": "Fits tone document",
                    }
                ],
                "selected": "cinematic-trailer",
                "reason": "Matches comps.",
            }
        ],
    }


def authored_script() -> dict:
    return {
        "version": "1.0",
        "title": "Test Feature",
        "total_duration_seconds": 60,
        "sections": [
            {
                "id": "s1",
                "text": "We were never going home. The station hums.",
                "start_seconds": 0,
                "end_seconds": 30,
                "source_ref": "beat-001",
            },
            {
                "id": "s2",
                "text": "The truth lands quietly.",
                "start_seconds": 30,
                "end_seconds": 60,
                "source_ref": "beat-002",
            },
        ],
        "metadata": {
            "canon_check": {
                "locks_honored": ["lock-001"],
                "protected_lines_verified": True,
                "production_lines": [],
                "tensions": [],
            }
        },
    }


def authored_scene_plan() -> dict:
    return {
        "version": "1.0",
        "scenes": [
            {
                "id": "scene-1",
                "type": "generated",
                "description": "Wide shot of the empty station corridor.",
                "start_seconds": 0,
                "end_seconds": 60,
                "script_section_id": "s1",
                "canon_refs": ["beat-001", "lock-001"],
            }
        ],
    }


def authored_asset_manifest() -> dict:
    return {
        "version": "1.0",
        "assets": [
            {
                "id": "asset-1",
                "type": "video",
                "path": "assets/video/corridor.mp4",
                "source_tool": "video_selector",
                "scene_id": "scene-1",
                "continuity": {
                    "canon_refs": ["lock-001"],
                    "references_applied": ["reference/corridor.png"],
                    "risk_notes_applied": [],
                },
            }
        ],
    }


def render_report(path: str = "renders/output.mp4") -> dict:
    return {
        "version": "1.0",
        "outputs": [
            {
                "path": path,
                "format": "mp4",
                "resolution": "320x240",
                "duration_seconds": 1,
            }
        ],
    }


def passing_final_review(canon: dict) -> dict:
    return {
        "version": "1.0",
        "output_path": "renders/output.mp4",
        "status": "pass",
        "checks": {
            "technical_probe": {"valid_container": True},
            "visual_spotcheck": {"frames_sampled": 4},
            "audio_spotcheck": {"narration_present": True},
            "promise_preservation": {"delivery_promise_honored": True},
            "subtitle_check": {"subtitles_expected": False},
            "canon_pass": {
                "status": "pass",
                "locks": [
                    {"lock_id": lock["id"], "verdict": "honored"}
                    for lock in canon["locks"]
                ],
                "protected_lines": [
                    {"line": pl["line"], "present_verbatim": True, "audible": True}
                    for pl in canon.get("protected_lines", [])
                ],
                "continuity_spotchecks": [
                    {"subject": entity["name"], "verdict": "consistent"}
                    for entity in (
                        canon.get("characters", []) + canon.get("locations", [])
                    )
                ],
                "tone": {
                    "must_never_feel_like_verdict": "Does not read as a tech demo."
                },
                "unresolved_flags": [],
            },
        },
        "recommended_action": "present_to_user",
    }


def make_real_render(project_dir, *, audio: str = "audible",
                     motion: str = "static", duration: int = 1) -> None:
    """Write a real, ffprobe-valid mp4 at renders/output.mp4.

    audio: "audible" (default), "silent" (audio track with no signal),
    or "none" (video-only).
    motion: "static" (held black frame) or "moving" (testsrc — every frame
    differs).
    """
    out = project_dir / "renders" / "output.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    video_src = (
        f"testsrc=size=320x240:rate=30:duration={duration}"
        if motion == "moving"
        else f"color=c=black:s=320x240:d={duration}"
    )
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", video_src]
    if audio == "audible":
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "aac"]
    elif audio == "silent":
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={duration}", "-c:a", "aac"]
    cmd += ["-pix_fmt", "yuv420p", "-shortest", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


ffmpeg_available = (
    shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
)


@pytest.fixture
def project(tmp_path):
    project_dir = init_project(
        "p", title="Test Feature", pipeline_type=PIPELINE, pipeline_dir=tmp_path
    )
    return tmp_path, project_dir


def advance(pipeline_dir, through: str, *, canon: dict | None = None,
            project_dir=None) -> dict:
    """Write valid completed checkpoints through the named stage; return canon."""
    canon = canon or canon_packet()
    order = ["canon_ingest", "proposal", "script", "scene_plan", "assets", "edit", "compose"]
    stage_artifacts = {
        "canon_ingest": {"canon_packet": canon},
        "proposal": {
            "proposal_packet": sample_artifact("proposal_packet"),
            "decision_log": plain_decision_log(),
        },
        "script": {"script": authored_script()},
        "scene_plan": {"scene_plan": authored_scene_plan()},
        "assets": {"asset_manifest": authored_asset_manifest()},
        "edit": {
            "edit_decisions": {
                **sample_artifact("edit_decisions"),
                "render_runtime": "ffmpeg",
            }
        },
        "compose": {
            "render_report": render_report(),
            "final_review": passing_final_review(canon),
        },
    }
    for stage in order:
        if stage == "compose" and project_dir is not None:
            make_real_render(project_dir)
        write_checkpoint(
            pipeline_dir, "p", stage, "completed", stage_artifacts[stage],
            pipeline_type=PIPELINE, human_approved=True,
        )
        if stage == through:
            break
    return canon


# ---------------------------------------------------------------------------
# 1. Manifest stage contracts are runtime contracts
# ---------------------------------------------------------------------------

class TestManifestContracts:
    def test_canon_ingest_cannot_complete_without_canon_packet(self, project):
        pipeline_dir, _ = project
        with pytest.raises(CheckpointValidationError, match="canon_packet"):
            write_checkpoint(
                pipeline_dir, "p", "canon_ingest", "completed", {},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_proposal_cannot_omit_declared_decision_log_output(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest")
        with pytest.raises(CheckpointValidationError, match="decision_log"):
            write_checkpoint(
                pipeline_dir, "p", "proposal", "completed",
                {"proposal_packet": sample_artifact("proposal_packet")},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_proposal_requires_canon_packet_input_from_predecessor(self, project):
        pipeline_dir, _ = project
        # Predecessor checkpoint exists and is approved, but was (hand-)written
        # without its canon_packet — proposal must not advance on top of it.
        path = pipeline_dir / "p" / "checkpoint_canon_ingest.json"
        checkpoint = {
            "version": "1.0",
            "project_id": "p",
            "pipeline_type": PIPELINE,
            "stage": "canon_ingest",
            "status": "completed",
            "timestamp": "2026-08-08T00:00:00+00:00",
            "checkpoint_policy": "guided",
            "human_approval_required": True,
            "human_approved": True,
            "artifacts": {},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(checkpoint), encoding="utf-8")
        with pytest.raises(CheckpointValidationError):
            write_checkpoint(
                pipeline_dir, "p", "proposal", "completed",
                {
                    "proposal_packet": sample_artifact("proposal_packet"),
                    "decision_log": plain_decision_log(),
                },
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_compose_cannot_complete_without_final_review(self, project):
        pipeline_dir, project_dir = project
        advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        with pytest.raises(CheckpointValidationError, match="final_review"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report()},
                pipeline_type=PIPELINE,
            )


# ---------------------------------------------------------------------------
# 2. Canon rulings: schema-valid, atomic decision log
# ---------------------------------------------------------------------------

class TestDecisionLog:
    def test_canon_ruling_category_is_schema_valid(self):
        validate_artifact("decision_log", canon_ruling_log())

    def test_rejected_checkpoint_leaves_no_decision_log_behind(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest")
        bad_log = plain_decision_log()
        bad_log["decisions"][0]["category"] = "not_a_real_category"
        with pytest.raises(CheckpointValidationError):
            write_checkpoint(
                pipeline_dir, "p", "proposal", "completed",
                {
                    "proposal_packet": sample_artifact("proposal_packet"),
                    "decision_log": bad_log,
                },
                pipeline_type=PIPELINE, human_approved=True,
            )
        assert not (pipeline_dir / "p" / "decision_log.json").exists()

    def test_rejected_checkpoint_does_not_mutate_existing_log(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        before = (pipeline_dir / "p" / "decision_log.json").read_text(encoding="utf-8")
        bad_log = plain_decision_log()
        bad_log["decisions"][0]["decision_id"] = "d-999"
        bad_log["decisions"][0]["category"] = "not_a_real_category"
        with pytest.raises(CheckpointValidationError):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": authored_script(), "decision_log": bad_log},
                pipeline_type=PIPELINE, human_approved=True,
            )
        after = (pipeline_dir / "p" / "decision_log.json").read_text(encoding="utf-8")
        assert before == after


# ---------------------------------------------------------------------------
# 3. Blocking questions gate advancement
# ---------------------------------------------------------------------------

class TestBlockingQuestions:
    def test_unresolved_blocking_question_blocks_proposal_completion(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest", canon=canon_packet(blocking_question=True))
        with pytest.raises(CheckpointValidationError, match="q-001"):
            write_checkpoint(
                pipeline_dir, "p", "proposal", "completed",
                {
                    "proposal_packet": sample_artifact("proposal_packet"),
                    "decision_log": plain_decision_log(),
                },
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_blocking_question_resolved_by_canon_ruling_unblocks(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest", canon=canon_packet(blocking_question=True))
        log = plain_decision_log()
        log["decisions"].extend(canon_ruling_log()["decisions"])
        write_checkpoint(
            pipeline_dir, "p", "proposal", "completed",
            {
                "proposal_packet": sample_artifact("proposal_packet"),
                "decision_log": log,
            },
            pipeline_type=PIPELINE, human_approved=True,
        )

    def test_awaiting_human_is_still_writable_with_open_blocking_question(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest", canon=canon_packet(blocking_question=True))
        write_checkpoint(
            pipeline_dir, "p", "proposal", "awaiting_human",
            {
                "proposal_packet": sample_artifact("proposal_packet"),
                "decision_log": plain_decision_log(),
            },
            pipeline_type=PIPELINE,
        )


# ---------------------------------------------------------------------------
# 4. Structured canon evidence in downstream artifacts
# ---------------------------------------------------------------------------

class TestCanonEvidence:
    def test_script_without_source_refs_is_rejected(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        for section in script["sections"]:
            section.pop("source_ref")
        with pytest.raises(CheckpointValidationError, match="source_ref"):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": script},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_script_dropping_protected_line_is_rejected(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        script["sections"][0]["text"] = "We were never going home, were we?"
        with pytest.raises(CheckpointValidationError, match="[Pp]rotected"):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": script},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_scene_plan_without_canon_refs_is_rejected(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "script")
        plan = authored_scene_plan()
        plan["scenes"][0].pop("canon_refs")
        with pytest.raises(CheckpointValidationError, match="canon_refs"):
            write_checkpoint(
                pipeline_dir, "p", "scene_plan", "completed",
                {"scene_plan": plan},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_visual_asset_without_continuity_evidence_is_rejected(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "scene_plan")
        manifest = authored_asset_manifest()
        manifest["assets"][0].pop("continuity")
        with pytest.raises(CheckpointValidationError, match="continuity"):
            write_checkpoint(
                pipeline_dir, "p", "assets", "completed",
                {"asset_manifest": manifest},
                pipeline_type=PIPELINE, human_approved=True,
            )


# ---------------------------------------------------------------------------
# 5. Compose reality check: canon pass + real render
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
class TestComposeReality:
    def test_final_review_without_canon_pass_is_rejected(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        del review["checks"]["canon_pass"]
        with pytest.raises(CheckpointValidationError, match="canon_pass"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_failing_canon_pass_blocks_compose_completion(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        review["checks"]["canon_pass"]["locks"][0]["verdict"] = "violated"
        review["checks"]["canon_pass"]["status"] = "fail"
        with pytest.raises(CheckpointValidationError, match="canon"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_canon_pass_must_cover_every_lock(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        review["checks"]["canon_pass"]["locks"] = []
        with pytest.raises(CheckpointValidationError, match="lock-001"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_nonexistent_render_output_is_rejected(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        review = passing_final_review(canon)
        with pytest.raises(CheckpointValidationError, match="renders/output.mp4"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_render_path_may_not_escape_project_dir(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        with pytest.raises(CheckpointValidationError, match="escape|outside"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {
                    "render_report": render_report("../../outside.mp4"),
                    "final_review": review,
                },
                pipeline_type=PIPELINE,
            )

    def test_full_happy_path_completes(self, project):
        pipeline_dir, project_dir = project
        advance(pipeline_dir, "compose", project_dir=project_dir)


# ---------------------------------------------------------------------------
# 6. Canon packet provenance hardening
# ---------------------------------------------------------------------------

class TestSecondRoundFindings:
    """Regressions for the second independent review's P1/P2 findings."""

    # P1: a failed canon pass must be able to reach the writer.
    @pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
    def test_failed_canon_pass_can_reach_awaiting_human(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        review["checks"]["canon_pass"]["locks"][0]["verdict"] = "violated"
        review["checks"]["canon_pass"]["status"] = "fail"
        review["status"] = "fail"
        review["recommended_action"] = "block"
        write_checkpoint(
            pipeline_dir, "p", "compose", "awaiting_human",
            {"render_report": render_report(), "final_review": review},
            pipeline_type=PIPELINE,
        )

    # P1: an unapproved ruling is not a ruling.
    def test_unapproved_canon_ruling_does_not_release_block(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest", canon=canon_packet(blocking_question=True))
        log = plain_decision_log()
        ruling = canon_ruling_log()["decisions"][0]
        ruling["user_approved"] = False
        log["decisions"].append(ruling)
        with pytest.raises(CheckpointValidationError, match="q-001"):
            write_checkpoint(
                pipeline_dir, "p", "proposal", "completed",
                {
                    "proposal_packet": sample_artifact("proposal_packet"),
                    "decision_log": log,
                },
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_ruling_selecting_unknown_option_does_not_release_block(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "canon_ingest", canon=canon_packet(blocking_question=True))
        log = plain_decision_log()
        ruling = canon_ruling_log()["decisions"][0]
        ruling["selected"] = "not-an-option"
        log["decisions"].append(ruling)
        with pytest.raises(CheckpointValidationError, match="q-001"):
            write_checkpoint(
                pipeline_dir, "p", "proposal", "completed",
                {
                    "proposal_packet": sample_artifact("proposal_packet"),
                    "decision_log": log,
                },
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_canon_ruling_without_question_id_is_schema_invalid(self):
        log = canon_ruling_log()
        del log["decisions"][0]["question_id"]
        with pytest.raises(Exception):
            validate_artifact("decision_log", log)

    # P1: the skill-documented composite source_ref must be accepted.
    def test_composite_source_ref_is_accepted(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        script["sections"][0]["source_ref"] = "canon:beat-001 lock-001"
        write_checkpoint(
            pipeline_dir, "p", "script", "completed",
            {"script": script},
            pipeline_type=PIPELINE, human_approved=True,
        )

    def test_locks_honored_must_cover_every_lock(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        script["metadata"]["canon_check"]["locks_honored"] = []
        with pytest.raises(CheckpointValidationError, match="lock-001"):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": script},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_unresolved_tension_blocks_script_completion(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        script["metadata"]["canon_check"]["tensions"] = [
            {"description": "Treatment runtime cannot fit the final beat."}
        ]
        with pytest.raises(CheckpointValidationError, match="[Tt]ension"):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": script},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_tension_is_presentable_at_the_gate(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        script["metadata"]["canon_check"]["tensions"] = [
            {"description": "Treatment runtime cannot fit the final beat."}
        ]
        write_checkpoint(
            pipeline_dir, "p", "script", "awaiting_human",
            {"script": script},
            pipeline_type=PIPELINE,
        )

    # P1: audible protected lines, tone verdict, continuity coverage.
    @pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
    def test_inaudible_protected_line_blocks_compose(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        review["checks"]["canon_pass"]["protected_lines"][0]["audible"] = False
        with pytest.raises(CheckpointValidationError, match="audible"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_canon_pass_without_tone_verdict_is_schema_invalid(self):
        review = passing_final_review(canon_packet())
        del review["checks"]["canon_pass"]["tone"]
        with pytest.raises(Exception):
            validate_artifact("final_review", review)

    @pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
    def test_continuity_spotchecks_must_cover_tracked_entities(self, project):
        pipeline_dir, project_dir = project
        canon = canon_packet()
        canon["characters"] = [{"name": "The Keeper"}]
        advance(pipeline_dir, "edit", canon=canon, project_dir=project_dir)
        make_real_render(project_dir)
        review = passing_final_review(canon)
        review["checks"]["canon_pass"]["continuity_spotchecks"] = []
        with pytest.raises(CheckpointValidationError, match="Keeper"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    # P1: continuity evidence must be real, not an empty shell.
    def test_empty_continuity_evidence_is_rejected_when_canon_requires_it(self, project):
        pipeline_dir, _ = project
        canon = canon_packet()
        canon["characters"] = [
            {
                "name": "The Keeper",
                "reference_assets": ["reference/keeper.png"],
                "visual_continuity_risks": ["scar must remain on the left cheek"],
            }
        ]
        advance(pipeline_dir, "scene_plan", canon=canon)
        manifest = authored_asset_manifest()
        manifest["assets"][0]["continuity"] = {
            "canon_refs": ["The Keeper"],
            "references_applied": [],
            "risk_notes_applied": [],
        }
        with pytest.raises(CheckpointValidationError, match="Keeper"):
            write_checkpoint(
                pipeline_dir, "p", "assets", "completed",
                {"asset_manifest": manifest},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_unknown_continuity_canon_ref_is_rejected(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "scene_plan")
        manifest = authored_asset_manifest()
        manifest["assets"][0]["continuity"]["canon_refs"] = ["nobody-anyone-knows"]
        with pytest.raises(CheckpointValidationError, match="nobody-anyone-knows"):
            write_checkpoint(
                pipeline_dir, "p", "assets", "completed",
                {"asset_manifest": manifest},
                pipeline_type=PIPELINE, human_approved=True,
            )

    # P1: an audio-only file is not a film.
    @pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
    def test_audio_only_render_is_rejected(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        out = project_dir / "renders" / "output.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:a", "aac", str(out),
            ],
            check=True,
            capture_output=True,
        )
        review = passing_final_review(canon)
        with pytest.raises(CheckpointValidationError, match="video stream"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    # P2: decision log and checkpoint move together or not at all.
    def test_checkpoint_swap_failure_rolls_back_decision_log(self, project, monkeypatch):
        import os as os_module

        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        log_path = pipeline_dir / "p" / "decision_log.json"
        before = log_path.read_text(encoding="utf-8")

        new_log = plain_decision_log()
        new_log["decisions"][0]["decision_id"] = "d-050"

        real_replace = os_module.replace

        def failing_replace(src, dst):
            from pathlib import Path as _P

            if _P(str(dst)).name.startswith("checkpoint_"):
                raise OSError("simulated disk failure during checkpoint swap")
            return real_replace(src, dst)

        monkeypatch.setattr(os_module, "replace", failing_replace)
        with pytest.raises(OSError):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": authored_script(), "decision_log": new_log},
                pipeline_type=PIPELINE, human_approved=True,
            )
        monkeypatch.setattr(os_module, "replace", real_replace)

        after = log_path.read_text(encoding="utf-8")
        assert before == after, "decision log must roll back when the checkpoint swap fails"
        assert "d-050" not in after


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
class TestAudibleDeliverable:
    """A film whose canon protects a spoken line must DELIVER audio signal.

    Found by the human ear in the shakedown run: the runtime accepted a
    completed compose whose audio track was digital silence (-91 dB)."""

    def test_silent_audio_track_is_rejected(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir, audio="silent")
        review = passing_final_review(canon)
        with pytest.raises(CheckpointValidationError, match="[Ss]ilen"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_missing_audio_track_is_rejected_when_lines_are_protected(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        make_real_render(project_dir, audio="none")
        review = passing_final_review(canon)
        with pytest.raises(CheckpointValidationError, match="audio"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_audible_render_completes(self, project):
        pipeline_dir, project_dir = project
        advance(pipeline_dir, "compose", project_dir=project_dir)


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
class TestMotionPromise:
    """A delivery promise of motion_required must be measured on the
    deliverable. Found in the first real production: a 60s film whose frames
    were ~92% identical passed every check — a slideshow self-certified as a
    motion-led film."""

    def _promise_motion(self, pipeline_dir) -> None:
        """Rewrite the proposal checkpoint with motion_required: true."""
        import json as _json
        path = pipeline_dir / "p" / "checkpoint_proposal.json"
        cp = _json.loads(path.read_text(encoding="utf-8"))
        cp["artifacts"]["proposal_packet"]["production_plan"]["delivery_promise"] = {
            "promise_type": "motion_led",
            "motion_required": True,
            "tone_mode": "cinematic",
            "quality_floor": "presentable",
        }
        path.write_text(_json.dumps(cp), encoding="utf-8")

    def test_static_render_breaks_a_motion_promise(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        self._promise_motion(pipeline_dir)
        make_real_render(project_dir, motion="static", duration=3)
        review = passing_final_review(canon)
        rr = render_report()
        rr["outputs"][0]["duration_seconds"] = 3
        with pytest.raises(CheckpointValidationError, match="[Mm]otion"):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": rr, "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_moving_render_satisfies_a_motion_promise(self, project):
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        self._promise_motion(pipeline_dir)
        make_real_render(project_dir, motion="moving", duration=3)
        review = passing_final_review(canon)
        rr = render_report()
        rr["outputs"][0]["duration_seconds"] = 3
        write_checkpoint(
            pipeline_dir, "p", "compose", "completed",
            {"render_report": rr, "final_review": review},
            pipeline_type=PIPELINE,
        )

    def test_static_render_is_fine_without_a_motion_promise(self, project):
        pipeline_dir, project_dir = project
        # default sample proposal makes no motion promise — static passes
        advance(pipeline_dir, "compose", project_dir=project_dir)


class TestCodeRabbitFindings:
    """Regressions for the CodeRabbit PR review (fork PR #1). Only the
    undecodable-render probe needs media tools; the rest run everywhere."""

    @pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg/ffprobe not installed")
    def test_undecodable_render_fails_motion_promise_closed(self, project):
        import json as _json
        pipeline_dir, project_dir = project
        canon = advance(pipeline_dir, "edit", project_dir=project_dir)
        path = pipeline_dir / "p" / "checkpoint_proposal.json"
        cp = _json.loads(path.read_text(encoding="utf-8"))
        cp["artifacts"]["proposal_packet"]["production_plan"]["delivery_promise"] = {
            "promise_type": "motion_led", "motion_required": True,
            "tone_mode": "cinematic", "quality_floor": "presentable",
        }
        path.write_text(_json.dumps(cp), encoding="utf-8")
        out = project_dir / "renders" / "output.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 4096)  # not a video at all
        review = passing_final_review(canon)
        with pytest.raises(CheckpointValidationError):
            write_checkpoint(
                pipeline_dir, "p", "compose", "completed",
                {"render_report": render_report(), "final_review": review},
                pipeline_type=PIPELINE,
            )

    def test_duplicate_question_ids_rejected_at_ingest(self, project):
        pipeline_dir, _ = project
        canon = canon_packet(blocking_question=True)
        canon["open_questions"].append(dict(canon["open_questions"][0]))
        with pytest.raises(CheckpointValidationError, match="duplicate"):
            write_checkpoint(
                pipeline_dir, "p", "canon_ingest", "completed",
                {"canon_packet": canon},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_beat_lock_ref_must_resolve(self, project):
        pipeline_dir, _ = project
        canon = canon_packet()
        canon["structure"]["beats"][0]["lock_refs"] = ["lock-nowhere"]
        with pytest.raises(CheckpointValidationError, match="lock-nowhere"):
            write_checkpoint(
                pipeline_dir, "p", "canon_ingest", "completed",
                {"canon_packet": canon},
                pipeline_type=PIPELINE, human_approved=True,
            )

    def test_non_string_locks_honored_fails_cleanly(self, project):
        pipeline_dir, _ = project
        advance(pipeline_dir, "proposal")
        script = authored_script()
        script["metadata"]["canon_check"]["locks_honored"] = [{"id": "atom-001"}]
        with pytest.raises(CheckpointValidationError, match="string"):
            write_checkpoint(
                pipeline_dir, "p", "script", "completed",
                {"script": script},
                pipeline_type=PIPELINE, human_approved=True,
            )


class TestCanonPacketHardening:
    def test_source_document_requires_sha256(self):
        packet = canon_packet()
        del packet["source_documents"][0]["sha256"]
        with pytest.raises(Exception):
            validate_artifact("canon_packet", packet)

    def test_sha256_must_be_64_hex(self):
        packet = canon_packet()
        packet["source_documents"][0]["sha256"] = "not-a-hash"
        with pytest.raises(Exception):
            validate_artifact("canon_packet", packet)

    def test_empty_source_path_rejected(self):
        packet = canon_packet()
        packet["source_documents"][0]["path"] = ""
        with pytest.raises(Exception):
            validate_artifact("canon_packet", packet)

    def test_empty_lock_fields_rejected(self):
        packet = canon_packet()
        packet["locks"][0]["id"] = ""
        with pytest.raises(Exception):
            validate_artifact("canon_packet", packet)

    def test_writing_document_authority_tier_exists(self):
        validate_artifact("canon_packet", canon_packet())  # uses writing_document

    def test_pitch_export_lock_source_type_exists(self):
        packet = canon_packet()
        packet["locks"][0]["source_type"] = "pitch_export"
        validate_artifact("canon_packet", packet)

    def test_open_question_requires_id_and_status(self):
        packet = canon_packet()
        packet["open_questions"] = [{"question": "Unnamed?", "blocking": True}]
        with pytest.raises(Exception):
            validate_artifact("canon_packet", packet)

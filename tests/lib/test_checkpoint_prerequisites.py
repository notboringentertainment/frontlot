import json

import pytest
from tests.contracts.test_phase0_contracts import sample_artifact

from lib.checkpoint import (
    CheckpointValidationError,
    init_project,
    write_checkpoint,
)


def _script_artifact() -> dict:
    return {
        "version": "1.0",
        "title": "Smoke",
        "total_duration_seconds": 1,
        "sections": [
            {
                "id": "s1",
                "text": "One second.",
                "start_seconds": 0,
                "end_seconds": 1,
            }
        ],
    }


def test_later_stage_cannot_skip_a_missing_predecessor(tmp_path) -> None:
    init_project(
        "run",
        title="Run",
        pipeline_type="framework-smoke",
        pipeline_dir=tmp_path,
    )

    with pytest.raises(CheckpointValidationError, match="PREREQUISITE VIOLATION"):
        write_checkpoint(
            tmp_path,
            "run",
            "script",
            "completed",
            {"script": _script_artifact()},
            pipeline_type="framework-smoke",
            human_approved=True,
        )


def test_later_stage_rejects_unapproved_gated_predecessor(tmp_path) -> None:
    project_dir = init_project(
        "run",
        title="Run",
        pipeline_type="framework-smoke",
        pipeline_dir=tmp_path,
    )
    predecessor_path = write_checkpoint(
        tmp_path,
        "run",
        "research",
        "awaiting_human",
        {"research_brief": sample_artifact("research_brief")},
        pipeline_type="framework-smoke",
    )
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor["status"] = "completed"
    predecessor["human_approved"] = False
    predecessor_path.write_text(json.dumps(predecessor), encoding="utf-8")

    with pytest.raises(CheckpointValidationError, match="completed without required approval"):
        write_checkpoint(
            tmp_path,
            "run",
            "script",
            "completed",
            {"script": _script_artifact()},
            pipeline_type="framework-smoke",
            human_approved=True,
        )


def test_malformed_predecessor_cannot_forge_completion(tmp_path) -> None:
    project_dir = init_project(
        "run",
        title="Run",
        pipeline_type="framework-smoke",
        pipeline_dir=tmp_path,
    )
    (project_dir / "checkpoint_research.json").write_text(
        json.dumps({"status": "completed", "human_approved": True}),
        encoding="utf-8",
    )

    with pytest.raises(CheckpointValidationError, match="incomplete or missing"):
        write_checkpoint(
            tmp_path,
            "run",
            "script",
            "completed",
            {"script": _script_artifact()},
            pipeline_type="framework-smoke",
            human_approved=True,
        )


def test_in_progress_heartbeat_is_not_blocked_by_prerequisites(tmp_path) -> None:
    init_project(
        "run",
        title="Run",
        pipeline_type="framework-smoke",
        pipeline_dir=tmp_path,
    )

    path = write_checkpoint(
        tmp_path,
        "run",
        "script",
        "in_progress",
        {},
        pipeline_type="framework-smoke",
    )

    assert path.exists()


def test_unknown_style_playbook_fails_before_project_creation(tmp_path) -> None:
    with pytest.raises(CheckpointValidationError, match="style_playbook"):
        init_project(
            "run",
            title="Run",
            pipeline_type="framework-smoke",
            pipeline_dir=tmp_path,
            style_playbook="does-not-exist",
        )

    assert not (tmp_path / "run").exists()


def test_marker_derived_unknown_playbook_blocks_later_writes(tmp_path) -> None:
    project_dir = tmp_path / "run"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        json.dumps({
            "version": "1.0",
            "project_id": "run",
            "pipeline_type": "framework-smoke",
            "style_playbook": "does-not-exist",
        }),
        encoding="utf-8",
    )

    with pytest.raises(CheckpointValidationError, match="style_playbook"):
        write_checkpoint(tmp_path, "run", "research", "in_progress", {})


# ---------------------------------------------------------------------------
# Manifest-declared partial predecessors (authored-film 1.6, plan 2026-09-08)
# ---------------------------------------------------------------------------

from lib.checkpoint import (  # noqa: E402
    PARTIAL_PREDECESSORS,
    _declared_partial_predecessors,
    _predecessor_satisfies,
)


def test_partial_predecessors_default_to_the_code_constant() -> None:
    """No declaration in the manifest -> the D18 code default decides."""
    assert _declared_partial_predecessors(None) is None
    assert _declared_partial_predecessors({"name": "script"}) is None
    for stage, allowed in PARTIAL_PREDECESSORS.items():
        for predecessor in allowed:
            assert _predecessor_satisfies(stage, predecessor, "in_progress") is True
            assert _predecessor_satisfies(stage, predecessor, "awaiting_human") is True
    assert _predecessor_satisfies("script", "visual_bible", "in_progress") is False


def test_a_declaration_replaces_the_code_default_for_that_stage() -> None:
    declared = _declared_partial_predecessors(
        {"name": "script", "partial_predecessors": ["look_lock", "headshots", "visual_bible"]}
    )
    assert declared == frozenset({"look_lock", "headshots", "visual_bible"})
    assert _predecessor_satisfies("script", "visual_bible", "in_progress", declared=declared) is True
    assert _predecessor_satisfies("script", "proposal", "in_progress", declared=declared) is False


def test_an_empty_declaration_allows_nothing_partial() -> None:
    declared = _declared_partial_predecessors({"name": "headshots", "partial_predecessors": []})
    assert declared == frozenset()
    assert _predecessor_satisfies("headshots", "look_lock", "in_progress", declared=declared) is False
    assert _predecessor_satisfies("headshots", "look_lock", "completed", declared=declared) is True


def test_a_failed_or_missing_predecessor_never_satisfies() -> None:
    declared = frozenset({"visual_bible"})
    for status in ("failed", "unknown", None, ""):
        assert _predecessor_satisfies("script", "visual_bible", status, declared=declared) is False

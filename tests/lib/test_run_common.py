"""D20.5 — neutral run utilities. Invented names only."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from lib import run_common
from lib.run_common import RunError
from tests.lib.look_lock_helpers import CHAR, PROJECT


@pytest.fixture
def projects(tmp_path, monkeypatch):
    import lib.paths as paths_mod

    base = tmp_path / "projects"
    base.mkdir()
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", base)
    root = base / PROJECT
    root.mkdir()
    (root / "project.yaml").write_text("version: '1.1'\n")
    return base, root


class TestResolve:
    def test_registered_root(self, projects):
        base, root = projects
        assert run_common.resolve_project_root(PROJECT) == root.resolve()
        with pytest.raises(RunError, match="no registered project"):
            run_common.resolve_project_root("proj-missing")
        with pytest.raises(RunError, match="must match"):
            run_common.resolve_project_root("../escape")
        link = base / "proj-link"
        link.symlink_to(root)
        with pytest.raises(RunError, match="symlink"):
            run_common.resolve_project_root("proj-link")
        (base / "proj-bare").mkdir()
        with pytest.raises(RunError, match="project.yaml"):
            run_common.resolve_project_root("proj-bare")

    def test_entity_id(self):
        assert run_common.require_entity_id(CHAR) == CHAR
        with pytest.raises(RunError):
            run_common.require_entity_id("CHAR INVALID")


class TestRequests:
    def test_state_and_read(self, projects):
        _, root = projects
        request_id = f"look-{CHAR}-1"
        assert run_common.request_state(root, request_id) == "missing"
        req_dir = root / ".gate-requests"
        (req_dir / "done").mkdir(parents=True)
        (req_dir / "declined").mkdir(parents=True)
        (req_dir / f"{request_id}.json").write_text(json.dumps({"request_id": request_id, "kind": "look_lock"}))
        assert run_common.request_state(root, request_id) == "pending"
        state, req = run_common.read_request(root, request_id)
        assert state == "pending" and req["kind"] == "look_lock"
        (req_dir / "done" / f"{request_id}.json").write_text(json.dumps({"request_id": request_id}))
        with pytest.raises(RunError, match="more than one state"):
            run_common.request_state(root, request_id)
        (req_dir / f"{request_id}.json").unlink()
        assert run_common.request_state(root, request_id) == "done"
        (req_dir / "declined" / "x.json").write_text(json.dumps({"request_id": "y", "declined_note": "no"}))
        with pytest.raises(RunError, match="does not carry"):
            run_common.read_request(root, "x")
        with pytest.raises(RunError, match="invalid request_id"):
            run_common.request_state(root, "../../etc")
        assert run_common.read_request(root, "nothing") == ("missing", None)

    def test_gate_command(self, projects):
        _, root = projects
        request_id = f"look-{CHAR}-1"
        invocation = run_common.gate_invocation(root, request_id)
        assert invocation == {
            "cwd": run_common.REPO,
            "argv": [str(run_common.REPO / ".venv/bin/python"), "scripts/gate_sign.py", "--project", PROJECT,
                     "--request", request_id],
        }
        assert run_common.gate_command(root, request_id) == (
            f"cd {shlex.quote(str(invocation['cwd']))} && {shlex.join(invocation['argv'])}"
        )

    def test_gate_command_quotes_repository_path(self, projects, monkeypatch, tmp_path):
        _, root = projects
        repository = tmp_path / "repository with spaces"
        monkeypatch.setattr(run_common, "REPO", repository)
        invocation = run_common.gate_invocation(root, "request.with-dots")
        assert run_common.gate_command(root, "request.with-dots") == (
            f"cd {shlex.quote(str(repository))} && {shlex.join(invocation['argv'])}"
        )


class TestDecisions:
    def test_write_decision_appends_validated_log(self, projects):
        _, root = projects
        d = run_common.write_decision(root, stage="headshots", category="revision",
                                      subject=f"reject-all for {CHAR}", reason="too stern; try again")
        log = json.loads((root / "decision_log.json").read_text())
        assert log["project_id"] == PROJECT and log["decisions"][0]["decision_id"] == d["decision_id"]
        assert log["decisions"][0]["reason"] == "too stern; try again" and log["decisions"][0]["category"] == "revision"
        run_common.write_decision(root, stage="headshots", category="revision", subject="again", reason="second")
        assert len(json.loads((root / "decision_log.json").read_text())["decisions"]) == 2
        from lib.checkpoint import CheckpointValidationError
        with pytest.raises(CheckpointValidationError):
            run_common.write_decision(root, stage="headshots", category="not-a-category", subject="s", reason="r")


class TestLease:
    def test_hold_lease_uses_config_wall_time(self, projects):
        _, root = projects

        class Cfg:
            data = {"wall_time_minutes": 7}

        with run_common.hold_lease(root, Cfg()) as lease:
            record = json.loads((root / lease.path.name).read_text())
            assert record["wall_time_minutes"] == 7.0
        assert not (root / lease.path.name).exists()

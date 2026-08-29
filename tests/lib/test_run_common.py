"""D20.5 — neutral run utilities. Invented names only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import run_common
from lib.run_common import RunError


@pytest.fixture
def projects(tmp_path, monkeypatch):
    import lib.paths as paths_mod

    base = tmp_path / "projects"
    base.mkdir()
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", base)
    root = base / "proj-lantern"
    root.mkdir()
    (root / "project.yaml").write_text("version: '1.1'\n")
    return base, root


class TestResolve:
    def test_registered_root(self, projects):
        base, root = projects
        assert run_common.resolve_project_root("proj-lantern") == root.resolve()
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
        assert run_common.require_entity_id("marlow-vex") == "marlow-vex"
        with pytest.raises(RunError):
            run_common.require_entity_id("Marlow Vex")


class TestRequests:
    def test_state_and_read(self, projects):
        _, root = projects
        assert run_common.request_state(root, "look-marlow-vex-1") == "missing"
        req_dir = root / ".gate-requests"
        (req_dir / "done").mkdir(parents=True)
        (req_dir / "declined").mkdir(parents=True)
        (req_dir / "look-marlow-vex-1.json").write_text(json.dumps({"request_id": "look-marlow-vex-1", "kind": "look_lock"}))
        assert run_common.request_state(root, "look-marlow-vex-1") == "pending"
        state, req = run_common.read_request(root, "look-marlow-vex-1")
        assert state == "pending" and req["kind"] == "look_lock"
        (req_dir / "done" / "look-marlow-vex-1.json").write_text(json.dumps({"request_id": "look-marlow-vex-1"}))
        with pytest.raises(RunError, match="more than one state"):
            run_common.request_state(root, "look-marlow-vex-1")
        (req_dir / "look-marlow-vex-1.json").unlink()
        assert run_common.request_state(root, "look-marlow-vex-1") == "done"
        (req_dir / "declined" / "x.json").write_text(json.dumps({"request_id": "y", "declined_note": "no"}))
        with pytest.raises(RunError, match="does not carry"):
            run_common.read_request(root, "x")
        with pytest.raises(RunError, match="invalid request_id"):
            run_common.request_state(root, "../../etc")
        assert run_common.read_request(root, "nothing") == ("missing", None)

    def test_gate_command(self, projects):
        _, root = projects
        cmd = run_common.gate_command(root, "look-marlow-vex-1")
        assert cmd.endswith("scripts/gate_approve.py --project proj-lantern --request look-marlow-vex-1")


class TestDecisions:
    def test_write_decision_appends_validated_log(self, projects):
        _, root = projects
        d = run_common.write_decision(root, stage="headshots", category="revision",
                                      subject="reject-all for marlow-vex", reason="too stern; try again")
        log = json.loads((root / "decision_log.json").read_text())
        assert log["project_id"] == "proj-lantern" and log["decisions"][0]["decision_id"] == d["decision_id"]
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

"""Manifest versioning and the pipeline_migration receipt chain (R3#1, R4#1, R5#1, R5#2)."""

import json

import pytest

from lib.pipeline_loader import (
    list_pipelines,
    load_pipeline,
    manifest_digest,
    manifest_versions,
    parse_pipeline_ref,
)
from lib.pipeline_pin import (
    PipelinePinError,
    pinned_pipeline,
    prepare_migration_request,
    refresh_cache,
)
from tests.lib.look_lock_helpers import pin_project


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({"project_id": "p", "pipeline_type": "authored-film"}))
    return d


class TestLoader:
    def test_versioned_manifests_resolve(self):
        assert manifest_versions("authored-film") == ["1.1", "1.2"]
        assert load_pipeline("authored-film@1.2")["version"] == "1.2"
        assert parse_pipeline_ref("authored-film@1.2") == ("authored-film", "1.2")
        assert "authored-film" in list_pipelines() and "authored-film@1.2" not in list_pipelines()

    def test_bare_manifest_is_the_1_1_alias(self):
        assert load_pipeline("authored-film") == load_pipeline("authored-film@1.1")
        assert manifest_digest("authored-film") == manifest_digest("authored-film@1.1")

    def test_look_lock_exists_only_in_1_2(self):
        names = lambda v: [s["name"] for s in load_pipeline(f"authored-film@{v}")["stages"]]
        assert "look_lock" not in names("1.1") and "headshots" not in names("1.1")
        assert names("1.2")[1:5] == ["proposal", "look_lock", "headshots", "visual_bible"]

    def test_unversioned_pipeline_has_no_versions(self):
        assert manifest_versions("cinematic") == []
        with pytest.raises(FileNotFoundError):
            load_pipeline("cinematic@9.9")


class TestPin:
    def test_default_without_receipt_is_1_1(self, project_dir):
        pin = pinned_pipeline(project_dir, "authored-film")
        assert (pin.version, pin.receipt_id) == ("1.1", None)
        assert pin.manifest_digest == manifest_digest("authored-film@1.1")

    def test_receipt_pins_and_chain_is_monotonic(self, project_dir):
        r1 = pin_project(project_dir, "1.2")
        assert pinned_pipeline(project_dir, "authored-film").version == "1.2"
        # a second root (no supersedes) makes the chain ambiguous: fail closed
        pin_project(project_dir, "1.1")
        with pytest.raises(PipelinePinError, match="tips|roots"):
            pinned_pipeline(project_dir, "authored-film")

    def test_downgrade_is_a_new_receipt_superseding_the_tip(self, project_dir):
        r1 = pin_project(project_dir, "1.2")
        pin_project(project_dir, "1.1", supersedes=r1["receipt_id"])
        assert pinned_pipeline(project_dir, "authored-film").version == "1.1"

    def test_cache_disagreeing_with_pin_fails_closed(self, project_dir):
        pin_project(project_dir, "1.2")
        marker = json.loads((project_dir / "project.json").read_text())
        marker["pipeline_manifest_version"] = "1.1"
        (project_dir / "project.json").write_text(json.dumps(marker))
        with pytest.raises(PipelinePinError, match="cache"):
            pinned_pipeline(project_dir, "authored-film")
        assert refresh_cache(project_dir, "authored-film").version == "1.2"
        assert json.loads((project_dir / "project.json").read_text())["pipeline_manifest_version"] == "1.2"

    def test_digest_mismatch_fails_closed(self, project_dir, monkeypatch):
        pin_project(project_dir, "1.2")
        import lib.pipeline_pin as pp

        monkeypatch.setattr(pp, "manifest_digest", lambda ref: "0" * 64)
        with pytest.raises(PipelinePinError, match="digest"):
            pinned_pipeline(project_dir, "authored-film")

    def test_explicit_version_must_equal_pin(self, project_dir):
        with pytest.raises(PipelinePinError, match="signed pin"):
            pinned_pipeline(project_dir, "authored-film@1.2")
        assert pinned_pipeline(project_dir, "authored-film@1.1").version == "1.1"

    def test_unversioned_pipeline_needs_no_receipt(self, project_dir):
        pin = pinned_pipeline(project_dir, "cinematic")
        assert pin.receipt_id is None and pin.manifest_digest == manifest_digest("cinematic")

    def test_migration_helper_prepares_request_and_mints_nothing(self, project_dir):
        path = prepare_migration_request(project_dir, "p", "authored-film", "1.1")
        req = json.loads(path.read_text())
        assert req["kind"] == "pipeline_migration" and req["envelope"] == {"supersedes_receipt_id": None}
        assert req["approval_record"] == {"pipeline_name": "authored-film", "version": "1.1",
                                          "manifest_digest": manifest_digest("authored-film@1.1"),
                                          "bound_checkpoints": []}
        assert not (project_dir / "approvals.jsonl").exists()
        r = pin_project(project_dir, "1.1")
        req = json.loads(prepare_migration_request(project_dir, "p", "authored-film", "1.2").read_text())
        assert req["envelope"]["supersedes_receipt_id"] == r["receipt_id"]
        with pytest.raises(PipelinePinError):
            prepare_migration_request(project_dir, "p", "authored-film", "7.7")


class TestChainWalk:
    """Slice A #15: the tip-backward walk must visit every receipt exactly once."""

    def _row(self, rid, prev, version="1.2"):
        return {"receipt_id": rid, "supersedes_receipt_id": prev,
                "record": {"pipeline_name": "authored-film", "version": version, "manifest_digest": "0" * 64}}

    def test_disconnected_cycle_beside_a_valid_chain_is_refused(self):
        from lib.pipeline_pin import _chain_tip

        rows = [self._row("r0", None), self._row("r1", "r0"), self._row("a", "b"), self._row("b", "a")]
        with pytest.raises(PipelinePinError, match="not on the root-to-tip path"):
            _chain_tip(rows, "authored-film")

    def test_two_roots_with_one_tip_are_refused(self):
        from lib.pipeline_pin import _chain_tip

        rows = [self._row("r0", None), self._row("x", None), self._row("r1", "r0"), self._row("r2", "x")]
        with pytest.raises(PipelinePinError, match="tips"):
            _chain_tip(rows, "authored-film")
        rows = [self._row("r0", None), self._row("x", None), self._row("r1", "r0")]
        with pytest.raises(PipelinePinError, match="tips"):
            _chain_tip(rows, "authored-film")

    def test_linear_chain_resolves_to_tip(self):
        from lib.pipeline_pin import _chain_tip

        rows = [self._row("r0", None), self._row("r1", "r0"), self._row("r2", "r1")]
        assert _chain_tip(rows, "authored-film")["receipt_id"] == "r2"
        assert _chain_tip([], "authored-film") is None

    def test_dangling_supersedes_is_refused(self):
        from lib.pipeline_pin import _chain_tip

        with pytest.raises(PipelinePinError, match="unknown receipt"):
            _chain_tip([self._row("r1", "ghost")], "authored-film")

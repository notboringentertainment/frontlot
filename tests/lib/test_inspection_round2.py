"""Post-build inspection round 2 fixes (Codex findings #2, #3, #4 narrowed, #5, #7, #8).

Every receipt here is minted through the real gates dir; invented names only.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import gates, receipts  # noqa: E402
from lib.canonical_json import record_sha256  # noqa: E402
from lib.checkpoint import (  # noqa: E402
    CheckpointValidationError,
    entity_free_scene_ids,
    get_completed_stages,
    get_latest_checkpoint,
    init_project,
    read_checkpoint,
    write_checkpoint,
)
from lib.look_ingest import LookIngestError, parse_look_ticket  # noqa: E402
from scripts import gate_approve  # noqa: E402
from tests.lib.look_lock_helpers import (  # noqa: E402
    approve_request,
    character_look,
    location_look,
    pin_project,
    write_ticket,
)
from tests.lib.test_authored_film_contract import (  # noqa: E402,F401  (gates_dir autouse)
    gates_dir,
    plain_decision_log,
    project,
    write_project_config,
)
from tests.lib.test_visual_bible_contract import (  # noqa: E402
    canon_packet_v11,
    proposal_v11,
    setup_through_scene_plan,
)

PROJECT = "p"
RECORD = {"asset_id": "aa" * 32, "role": "hero", "approved_prompt_block": "a figure at dusk", "palette": ["#112233"]}


def _mint(stage="visual_bible", scope="hero:ch-001", record=RECORD, project_id=PROJECT):
    with gates.handler_context():
        return gates.mint_gate_token(project_id, stage, scope, record_sha256(record), user_response="approve")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "projects" / PROJECT
    d.mkdir(parents=True)
    return d


# ---- #2: receipt transactions are serialized by an interprocess lock ---------------


class TestReceiptLock:
    def test_lock_file_lives_under_gates_locks(self, gates_dir):
        with gates.receipt_lock(PROJECT, "approval"):
            assert (gates_dir / "locks" / "p.approval.lock").exists()
            with gates.receipt_lock(PROJECT, "approval"):  # re-entrant within a thread
                pass

    def test_lock_serializes_a_second_thread(self):
        order: list[str] = []
        entered = threading.Event()

        def holder():
            with gates.receipt_lock(PROJECT, "generation"):
                entered.set()
                time.sleep(0.2)
                order.append("holder-done")

        def waiter():
            entered.wait()
            with gates.receipt_lock(PROJECT, "generation"):
                order.append("waiter-in")

        threads = [threading.Thread(target=holder), threading.Thread(target=waiter)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert order == ["holder-done", "waiter-in"]

    def test_concurrent_generations_produce_one_serialized_chain(self, root):
        n = 6
        barrier = threading.Barrier(n)
        errors: list[BaseException] = []

        def generate(i: int):
            try:
                barrier.wait()
                receipts.record_generation(
                    root, execution_id=f"exec-{i}", tool="image_gen", model_endpoint="fake/model",
                    normalized_inputs_hash="11" * 32, output_sha256=hashlib.sha256(str(i).encode()).hexdigest(),
                    cost_usd=0.0, started_at="t0", finished_at="t1",
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=generate, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        chain = gates.chain_rows(PROJECT, "generation")
        assert len(chain) == n
        # every row links to the one before it — no sibling rows
        assert [r["prev_receipt_id"] for r in chain] == [None] + [r["receipt_id"] for r in chain[:-1]]
        local = receipts.chained_rows(root, "generation")  # local projection == chain, in order
        assert [r["receipt_id"] for r in local] == [r["receipt_id"] for r in chain]
        assert len(receipts.verified_generation_receipts(root)) == n

    def test_origin_uniqueness_recheck_has_exactly_one_loser(self, root):
        """Two approvals for the same origin, each re-deriving uniqueness under
        the consumed token: the lock makes the second recheck see the first
        commit, so exactly one wins and the loser's token is spent."""
        tokens = [_mint(scope=f"hero:ch-00{i}") for i in (1, 2)]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def recheck():
            if receipts.find_approval(root, "hero", record_sha256=record_sha256(RECORD)) is not None:
                raise ValueError("origin already claimed")
            time.sleep(0.1)  # widen the window: without the lock both rechecks pass

        def approve(i: int):
            barrier.wait()
            try:
                receipts.record_human_approval(
                    root, PROJECT, "visual_bible", f"hero:ch-00{i}", RECORD, tokens[i - 1], "hero",
                    entity_id=f"ch-00{i}", pre_commit_check=recheck,
                )
                outcomes.append("won")
            except ValueError as exc:
                outcomes.append(str(exc))

        threads = [threading.Thread(target=approve, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(outcomes) == ["origin already claimed", "won"]
        assert len(_rows(root / "approvals.jsonl")) == 1
        assert len(receipts.verified_approvals(root, "hero")) == 1
        assert not list((Path(gates.gates_dir()) / "pending").iterdir())  # both tokens spent
        assert gates.wal_entries() == []


# ---- #3: chain advancement is journaled and a dangling signed tail is recovered ----


class TestChainJournal:
    def _generate(self, root, i=1):
        return receipts.record_generation(
            root, execution_id=f"exec-{i}", tool="image_gen", model_endpoint="fake/model",
            normalized_inputs_hash="11" * 32, output_sha256=hashlib.sha256(str(i).encode()).hexdigest(),
            cost_usd=0.0, started_at="t0", finished_at="t1",
        )

    def test_crash_between_row_and_tip_is_repaired_on_next_open(self, root):
        self._generate(root, 1)
        tip_before = gates.chain_tip(PROJECT, "generation")

        def crash(*a, **k):
            raise OSError("simulated crash after the row append, before the tip write")

        with pytest.MonkeyPatch.context() as mp:  # scoped: never undoes the gates-dir isolation
            mp.setattr(gates, "_write_tip", crash)
            with pytest.raises(OSError, match="simulated crash"):
                self._generate(root, 2)
        # on disk: two chain rows, a stale tip, and the journal of the in-flight advance
        assert len(_rows(gates.chain_path(PROJECT, "generation"))) == 2
        assert gates.chain_tip(PROJECT, "generation") == tip_before
        assert gates.chain_journal_path(PROJECT, "generation").exists()
        # next open recovers the signed, correctly linked tail instead of rejecting it
        rows = gates.chain_rows(PROJECT, "generation")
        assert len(rows) == 2 and rows[-1]["prev_receipt_id"] == tip_before
        assert gates.chain_tip(PROJECT, "generation") == rows[-1]["receipt_id"]
        assert not gates.chain_journal_path(PROJECT, "generation").exists()
        assert len(receipts.verified_generation_receipts(root)) == 2
        self._generate(root, 3)  # the chain keeps growing
        assert len(gates.chain_rows(PROJECT, "generation")) == 3

    def test_journal_is_written_before_the_row_and_removed_after_the_tip(self, root, monkeypatch):
        seen: list[bool] = []
        real_append = gates.append_jsonl

        def spy(path, obj):
            if str(path).endswith(".generation.jsonl"):
                seen.append(gates.chain_journal_path(PROJECT, "generation").exists())
            return real_append(path, obj)

        monkeypatch.setattr(gates, "append_jsonl", spy)
        self._generate(root, 1)
        assert seen == [True]
        assert not gates.chain_journal_path(PROJECT, "generation").exists()

    def test_crash_between_journal_and_row_leaves_nothing_to_recover(self, root):
        self._generate(root, 1)
        real_append = gates.append_jsonl

        def crash(path, obj):
            if str(path).endswith(".generation.jsonl"):
                raise OSError("simulated crash after the journal, before the row")
            return real_append(path, obj)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gates, "append_jsonl", crash)
            with pytest.raises(OSError, match="simulated crash"):
                self._generate(root, 2)
        assert gates.chain_journal_path(PROJECT, "generation").exists()
        assert len(gates.chain_rows(PROJECT, "generation")) == 1  # nothing was committed
        assert not gates.chain_journal_path(PROJECT, "generation").exists()

    def test_a_dangling_tail_with_a_bad_signature_is_still_rejected(self, root):
        self._generate(root, 1)
        first = gates.chain_rows(PROJECT, "generation")[0]
        forged = dict(first, receipt_id="forged", prev_receipt_id=first["receipt_id"], signature="0" * 64)
        gates.append_jsonl(gates.chain_path(PROJECT, "generation"), forged)
        with pytest.raises(gates.ReceiptChainError, match="bad signature"):
            gates.chain_rows(PROJECT, "generation")

    def test_a_dangling_tail_that_does_not_link_to_the_tip_is_rejected(self, root):
        self._generate(root, 1)
        first = gates.chain_rows(PROJECT, "generation")[0]
        stray = {k: v for k, v in first.items() if k != "signature"}
        stray.update(receipt_id="stray", prev_receipt_id="somewhere-else")
        # a correctly signed row (the key is local) that links elsewhere: mislinked, not dangling
        stray["signature"] = gates._sign_chain_row(stray)
        gates.append_jsonl(gates.chain_path(PROJECT, "generation"), stray)
        with pytest.raises(gates.ReceiptChainError, match="links to"):
            gates.chain_rows(PROJECT, "generation")


# ---- #4 (narrowed): no programmatic answer; minting only inside the handler ----------


class _Tty(io.StringIO):
    def isatty(self):
        return True


PROJECT_CONFIG = {
    "version": "1.0", "budget_usd_cap": 50.0, "wall_time_minutes": 30,
    "cast_cap": {"characters": 2, "locations": 2},
    "default_video_endpoint": "fal-ai/kling-video/o3/pro/reference-to-video",
    "provider_egress": {"provider": "fal", "content_classes": ["prompts", "reference_images"]},
}


def _config_request(tmp_path):
    import yaml

    root = tmp_path / "projects" / "demo"
    (root / ".gate-requests").mkdir(parents=True)
    raw = yaml.safe_dump(PROJECT_CONFIG, sort_keys=True).encode("utf-8")
    (root / "project.yaml").write_bytes(raw)
    req = {
        "request_id": "req-1", "project_id": "demo", "stage": "proposal",
        "scope": "config", "kind": "config",
        "entity_id": "project_config", "artifact": None,
        "approval_record": {"config_sha256": hashlib.sha256(raw).hexdigest()},
        "source_checkpoint_digest": None, "summary": "Approve project.yaml.", "preview_paths": [],
    }
    (root / ".gate-requests" / "req-1.json").write_text(json.dumps(req))
    return root, req


class TestHandlerOnlyMinting:
    def test_mint_refuses_outside_handler_context(self):
        with pytest.raises(gates.GateHandlerRequired, match="only callable from the gate handler"):
            gates.mint_gate_token(PROJECT, "visual_bible", "hero:ch-001", "ab" * 32)
        assert not (Path(gates.gates_dir()) / "pending").exists() or not list((Path(gates.gates_dir()) / "pending").iterdir())

    def test_environment_variable_alone_is_not_the_handler(self, monkeypatch):
        monkeypatch.setenv(gates.HANDLER_ENV, "1")
        with pytest.raises(gates.GateHandlerRequired):
            gates.mint_gate_token(PROJECT, "visual_bible", "hero:ch-001", "ab" * 32)
        monkeypatch.setenv(gates.HANDLER_ENV, "a" * 32)
        with pytest.raises(gates.GateHandlerRequired):
            gates.mint_gate_token(PROJECT, "visual_bible", "hero:ch-001", "ab" * 32)

    def test_handler_context_is_per_process_and_cleared_after(self, monkeypatch):
        monkeypatch.delenv(gates.HANDLER_ENV, raising=False)
        with gates.handler_context():
            nonce = gates._handler_nonce
            assert nonce and gates.HANDLER_ENV in __import__("os").environ
            gates.mint_gate_token(PROJECT, "visual_bible", "hero:ch-001", "ab" * 32)
        assert gates._handler_nonce is None
        assert gates.HANDLER_ENV not in __import__("os").environ
        # the nonce that was valid a moment ago no longer is
        monkeypatch.setenv(gates.HANDLER_ENV, nonce)
        with pytest.raises(gates.GateHandlerRequired):
            gates.mint_gate_token(PROJECT, "visual_bible", "hero:ch-001", "ab" * 32)

    def test_docstrings_call_it_a_discipline_boundary(self):
        assert "discipline boundary, not a privilege boundary" in gates.handler_context.__doc__
        assert "discipline boundary" in gates.__doc__


class TestNoProgrammaticAnswer:
    def test_public_decide_and_answer_parameter_are_gone(self):
        assert not hasattr(gate_approve, "decide")
        params = inspect.signature(gate_approve._decide).parameters
        assert "answer" not in params
        assert params["shown"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["shown"].default is inspect.Parameter.empty

    def test_decide_refuses_a_shown_record_that_differs_from_the_rebuilt_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _Tty())
        root, req = _config_request(tmp_path)
        shown = gate_approve.construct(root, req)
        stale = gate_approve.Constructed(dict(shown.record, config_sha256="0" * 64), shown.envelope, shown.entity_id)
        with gates.handler_context():
            with pytest.raises(gate_approve.GateHandlerError, match="changed between display and approval"):
                gate_approve._decide(req, root, shown=stale, note=None)
            with pytest.raises(gate_approve.GateHandlerError, match="Constructed record that was displayed"):
                gate_approve._decide(req, root, shown=None, note=None)  # type: ignore[arg-type]
        assert not (root / "approvals.jsonl").exists()
        assert (root / ".gate-requests" / "req-1.json").exists()

    def test_decide_outside_the_handler_context_mints_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _Tty())
        root, req = _config_request(tmp_path)
        shown = gate_approve.construct(root, req)
        with pytest.raises(gates.GateHandlerRequired):
            gate_approve._decide(req, root, shown=shown, note=None)
        assert not (root / "approvals.jsonl").exists()

    def _run_main(self, tmp_path, monkeypatch, answers: list[str]) -> int:
        monkeypatch.setattr(sys, "stdin", _Tty())
        prompts = iter(answers)
        monkeypatch.setattr("builtins.input", lambda *_: next(prompts))
        return gate_approve.main(["--project", "demo", "--request", "req-1", "--projects-dir", str(tmp_path / "projects")])

    def test_main_reads_the_decision_from_the_prompt_after_showing_the_record(self, tmp_path, monkeypatch, capsys):
        import os

        root, req = _config_request(tmp_path)
        rc = self._run_main(tmp_path, monkeypatch, ["y", "looks right"])
        out = capsys.readouterr().out
        assert rc == 0 and "record to be signed" in out and "approved — receipt" in out
        assert out.index("record to be signed") < out.index("approved — receipt")
        found = receipts.find_approval(root, "config", record_sha256=record_sha256(req["approval_record"]))
        assert found and found["user_response"] == {"answer": "approved", "note": "looks right", "selection": None}
        assert gates.HANDLER_ENV not in os.environ and gates._handler_nonce is None  # marker cleared after
        assert (root / ".gate-requests" / "done" / "req-1.json").exists()

    def test_main_declines_on_anything_but_y(self, tmp_path, monkeypatch, capsys):
        root, _ = _config_request(tmp_path)
        rc = self._run_main(tmp_path, monkeypatch, ["yes please", "not now"])
        assert rc == 0 and "declined" in capsys.readouterr().out
        assert not (root / "approvals.jsonl").exists()
        declined = json.loads((root / ".gate-requests" / "declined" / "req-1.json").read_text())
        assert declined["declined_note"] == "not now"


# ---- #5: id blockers must be exactly one resolved ticket ------------------------------


class TestIdBlockersResolve:
    def test_unknown_id_blocker_is_refused(self, tmp_path):
        path = write_ticket(tmp_path, character_look(), blocked_by=["wf-11111111"])
        with pytest.raises(LookIngestError, match="'wf-11111111' matches 0 resolved"):
            parse_look_ticket(path, wayfinder_root=tmp_path)

    def test_open_ticket_with_that_id_does_not_count(self, tmp_path):
        write_ticket(tmp_path, location_look(), ticket_id="wf-11111111", resolved=False, name="open.md")
        path = write_ticket(tmp_path, character_look(), blocked_by=["wf-11111111"])
        with pytest.raises(LookIngestError, match="matches 0 resolved"):
            parse_look_ticket(path, wayfinder_root=tmp_path)

    def test_duplicate_ids_under_resolved_are_refused(self, tmp_path):
        write_ticket(tmp_path, location_look(), ticket_id="wf-11111111", name="one.md")
        write_ticket(tmp_path, location_look("loc-02-0ddba11e"), ticket_id="wf-11111111", name="two.md")
        path = write_ticket(tmp_path, character_look(), blocked_by=["wf-11111111"])
        with pytest.raises(LookIngestError, match="matches 2 resolved"):
            parse_look_ticket(path, wayfinder_root=tmp_path)

    def test_resolved_id_blocker_binds_the_exact_file(self, tmp_path):
        dep = write_ticket(tmp_path, location_look(), ticket_id="wf-11111111", name="dep.md")
        undeclared = character_look()
        del undeclared["depends_on"]
        path = write_ticket(tmp_path, undeclared, blocked_by=["wf-11111111"])
        look = parse_look_ticket(path, wayfinder_root=tmp_path)
        assert look.payload["depends_on"] == [
            {"id": "wf-11111111", "path": "wayfinder/resolved/dep.md",
             "content_sha256": hashlib.sha256(dep.read_bytes()).hexdigest()},
        ]
        # the ticket's own id never satisfies its own blocker
        selfish = write_ticket(tmp_path, character_look("char-02-cafef00d"), ticket_id="wf-22222222",
                               blocked_by=["wf-22222222"], name="selfish.md")
        with pytest.raises(LookIngestError, match="matches 0 resolved"):
            parse_look_ticket(selfish, wayfinder_root=tmp_path)

    def test_schema_accepts_the_derived_full_ref_and_rejects_partial_ones(self):
        from lib.look_spec import LookSpecError, validate_look_spec

        full = {"id": "wf-11111111", "path": "wayfinder/resolved/dep.md", "content_sha256": "0" * 64}
        validate_look_spec(character_look(depends_on=[full]))
        for partial in ({"id": "wf-11111111", "path": "x.md"}, {"id": "wf-11111111", "content_sha256": "0" * 64},
                        {"id": "bad", "path": "x.md", "content_sha256": "0" * 64}):
            with pytest.raises(LookSpecError):
                validate_look_spec(character_look(depends_on=[partial]))


# ---- #7: entity_free needs a receipt-bound scene_plan checkpoint ---------------------


def _review_scene_plan(project_dir: Path, digest: str) -> dict:
    from lib.canonical_json import record_sha256 as _rs

    record = {"artifact_type": "scene_plan", "artifact_digest": digest}
    with gates.handler_context():
        token = gates.mint_gate_token(PROJECT, "scene_plan", "artifact:scene_plan", _rs(record), user_response="approve")
    return receipts.record_human_approval(
        project_dir, PROJECT, "scene_plan", "artifact:scene_plan", record, token, "artifact_review",
        artifact={"artifact_type": "scene_plan", "artifact_version": "1.1", "artifact_digest": digest,
                  "migration_status": "native"},
    )


class TestEntityFreeSceneIds:
    def test_unreceipted_checkpoint_authorizes_nothing(self, project):
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        cp = json.loads((project_dir / "checkpoint_scene_plan.json").read_text())
        assert cp["status"] == "completed" and cp["human_approved"] is True
        assert cp["artifacts"]["scene_plan"]["scenes"][0]["entity_free"] is True
        assert entity_free_scene_ids(project_dir) == set()

    def test_receipt_bound_to_the_checkpoint_digest_authorizes_its_entity_free_scenes(self, project):
        from lib.checkpoint import checkpoint_digest

        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        path = project_dir / "checkpoint_scene_plan.json"
        _review_scene_plan(project_dir, checkpoint_digest(path))
        assert entity_free_scene_ids(project_dir) == {"scene-1"}
        # an edit after the review changes the digest: the receipt no longer binds this file
        cp = json.loads(path.read_text())
        cp["artifacts"]["scene_plan"]["scenes"][0]["description"] = "edited after review"
        path.write_text(json.dumps(cp, indent=2))
        assert entity_free_scene_ids(project_dir) == set()

    def test_flipping_entity_free_on_disk_is_not_authority(self, project):
        from lib.checkpoint import checkpoint_digest

        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=False)
        path = project_dir / "checkpoint_scene_plan.json"
        _review_scene_plan(project_dir, checkpoint_digest(path))
        assert entity_free_scene_ids(project_dir) == set()  # reviewed, but no scene is entity_free
        cp = json.loads(path.read_text())
        scene = cp["artifacts"]["scene_plan"]["scenes"][0]
        scene.update(entity_free=True, character_refs=[], location_ref=None)
        path.write_text(json.dumps(cp, indent=2))
        assert entity_free_scene_ids(project_dir) == set()

    def test_receipt_for_another_artifact_or_digest_does_not_count(self, project):
        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        _review_scene_plan(project_dir, "0" * 64)
        assert entity_free_scene_ids(project_dir) == set()


# ---- #8: every project-context read applies the pin ---------------------------------


def _pinned_project(tmp_path, *, pin: bool):
    pipeline_dir = tmp_path
    project_dir = init_project(PROJECT, title="Test Feature", pipeline_type="authored-film", pipeline_dir=pipeline_dir)
    write_project_config(project_dir)
    if pin:
        pin_project(project_dir, "1.2")
    write_checkpoint(pipeline_dir, PROJECT, "canon_ingest", "completed", {"canon_packet": canon_packet_v11()},
                     pipeline_type="authored-film", human_approved=True)
    return pipeline_dir, project_dir


def _forge_digest(project_dir: Path, stage: str = "canon_ingest") -> None:
    path = project_dir / f"checkpoint_{stage}.json"
    cp = json.loads(path.read_text())
    cp["pipeline"]["manifest_digest"] = "f" * 64
    path.write_text(json.dumps(cp, indent=2))


def _strip_tuple(project_dir: Path, stage: str = "canon_ingest") -> None:
    path = project_dir / f"checkpoint_{stage}.json"
    cp = json.loads(path.read_text())
    cp.pop("pipeline", None)
    cp.pop("predecessors", None)
    path.write_text(json.dumps(cp, indent=2))


class TestPinEnforcedOnReads:
    def test_pinned_project_reads_its_own_checkpoints(self, tmp_path):
        pipeline_dir, project_dir = _pinned_project(tmp_path, pin=True)
        assert read_checkpoint(pipeline_dir, PROJECT, "canon_ingest")["pipeline"]["version"] == "1.2"
        assert get_latest_checkpoint(pipeline_dir, PROJECT)["stage"] == "canon_ingest"
        assert get_completed_stages(pipeline_dir, PROJECT, "authored-film") == ["canon_ingest"]

    @pytest.mark.parametrize("tamper", [_forge_digest, _strip_tuple], ids=["other-tuple", "unbound-legacy"])
    def test_pinned_project_fails_closed_on_every_read(self, tmp_path, tamper):
        pipeline_dir, project_dir = _pinned_project(tmp_path, pin=True)
        tamper(project_dir)
        with pytest.raises(CheckpointValidationError, match="PIPELINE PIN VIOLATION"):
            read_checkpoint(pipeline_dir, PROJECT, "canon_ingest")
        with pytest.raises(CheckpointValidationError, match="PIPELINE PIN VIOLATION"):
            get_latest_checkpoint(pipeline_dir, PROJECT)
        with pytest.raises(CheckpointValidationError, match="PIPELINE PIN VIOLATION"):
            get_completed_stages(pipeline_dir, PROJECT, "authored-film")

    def test_bound_legacy_checkpoint_still_reads_under_the_pin(self, tmp_path):
        pipeline_dir, project_dir = _pinned_project(tmp_path, pin=False)  # legacy 1.1 checkpoint first
        pin_project(project_dir, "1.2")  # the migration receipt binds the file as it is on disk
        assert read_checkpoint(pipeline_dir, PROJECT, "canon_ingest")["pipeline"]["version"] == "1.1"
        assert get_completed_stages(pipeline_dir, PROJECT, "authored-film") == ["canon_ingest"]
        _strip_tuple(project_dir)  # edited after binding: no longer in the bound set
        with pytest.raises(CheckpointValidationError, match="PIPELINE PIN VIOLATION"):
            read_checkpoint(pipeline_dir, PROJECT, "canon_ingest")

    @pytest.mark.parametrize("tamper", [_forge_digest, _strip_tuple], ids=["other-tuple", "legacy"])
    def test_unpinned_project_is_unchanged(self, tmp_path, tamper):
        pipeline_dir, project_dir = _pinned_project(tmp_path, pin=False)
        tamper(project_dir)
        assert read_checkpoint(pipeline_dir, PROJECT, "canon_ingest")["stage"] == "canon_ingest"
        assert get_latest_checkpoint(pipeline_dir, PROJECT)["stage"] == "canon_ingest"
        assert get_completed_stages(pipeline_dir, PROJECT, "authored-film") == ["canon_ingest"]

    def test_entity_free_ids_fail_closed_on_a_pin_mismatch(self, project):
        from lib.checkpoint import checkpoint_digest

        pipeline_dir, project_dir = project
        setup_through_scene_plan(pipeline_dir, project_dir, entity_free=True)
        path = project_dir / "checkpoint_scene_plan.json"
        _review_scene_plan(project_dir, checkpoint_digest(path))
        assert entity_free_scene_ids(project_dir) == {"scene-1"}
        pin_project(project_dir, "1.2")  # binds the file; re-reviewing is not needed for the bound set
        assert entity_free_scene_ids(project_dir) == {"scene-1"}
        _forge_digest(project_dir, "scene_plan")
        assert entity_free_scene_ids(project_dir) == set()

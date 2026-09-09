"""Slice A inspection fixes (Codex findings #1, #3, #6, #10, #12, #13, #14).

Every receipt here is minted through the real gates dir; invented names only.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import gates, receipts  # noqa: E402
from lib.canonical_json import record_sha256  # noqa: E402
from lib.look_ingest import active_looks  # noqa: E402
from lib.look_spec import look_hash  # noqa: E402
from lib.pipeline_pin import pinned_pipeline  # noqa: E402
from lib.receipts import ReceiptChainError  # noqa: E402
from lib.reference_import import (  # noqa: E402
    ORIGIN_CASTING,
    ORIGIN_IMPORTED_SYNTHETIC,
    ReferenceImportError,
    import_record,
    normalize_image_bytes,
    prepare_reference_import,
    tainted_hashes,
    validate_import_record,
)
from scripts import gate_approve  # noqa: E402
from tests.lib.look_lock_helpers import (  # noqa: E402
    CHAR,
    activate_look,
    approve_request,
    character_look,
    decline_request,
    image,
    look_refs_for,
    pin_project,
    retire_look,
    write_ticket,
)
from tests.lib.test_gate_approve_selection import (  # noqa: E402
    RECIPE_PROMPT,
    _headshot_request,
    _pending_checkpoint,
    candidate,
    recipe_for,
)


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def human_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "projects" / "p"
    (root / ".gate-requests").mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project_id": "p", "pipeline_type": "authored-film"}))
    return root


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _casting_receipt(project_dir: Path, pixel_hash: str) -> dict:
    record = import_record(ORIGIN_CASTING, pixel_hash)
    with gates.handler_context():
        token = gates.mint_gate_token("p", "look_lock", f"reference:{pixel_hash}", record_sha256(record))
    return receipts.record_human_approval(
        project_dir, "p", "look_lock", f"reference:{pixel_hash}", record, token, "reference_import",
        entity_id=f"reference-{pixel_hash[:12]}",
        envelope={"origin_class": ORIGIN_CASTING, "normalized_pixel_hash": pixel_hash},
    )


# ---- #1: the orchestrator-owned receipt chain ---------------------------------


class TestReceiptChain:
    def test_deleting_a_casting_receipt_locally_fails_closed(self, root):
        c = character_look()
        activate_look(root, c)
        tainted = "7" * 64
        _casting_receipt(root, tainted)
        assert tainted_hashes(root) == {tainted}
        rows = _rows(root / "approvals.jsonl")
        _write_rows(root / "approvals.jsonl", [r for r in rows if r["kind"] != "reference_import"])
        # taint is never "cleared": every reader of the projection refuses it
        with pytest.raises(ReceiptChainError, match="reference_import.*missing from the local receipt file"):
            tainted_hashes(root)
        with pytest.raises(ReceiptChainError):
            active_looks(root)
        with pytest.raises(ReceiptChainError):
            receipts.find_approval(root, "look_lock", entity_id=CHAR)

    def test_deleting_a_retire_receipt_does_not_restore_the_old_look(self, root):
        c = character_look()
        activate_look(root, c)
        retire_look(root, c)
        assert active_looks(root) == {}
        rows = _rows(root / "approvals.jsonl")
        _write_rows(root / "approvals.jsonl", [r for r in rows if r.get("action") != "retire"])
        with pytest.raises(ReceiptChainError, match="missing from the local receipt file"):
            active_looks(root)
        from lib.look_ingest import active_look_for, verify_look_refs

        with pytest.raises(ReceiptChainError):
            active_look_for(root, "character", CHAR)
        with pytest.raises(ReceiptChainError):
            verify_look_refs(root, [{"entity_kind": "character", "entity_id": CHAR, "look_hash": look_hash(c)}])

    def test_reordered_rows_and_deleted_migration_fail_closed(self, root):
        first = pin_project(root, "1.2")
        pin_project(root, "1.1", supersedes=first["receipt_id"])
        assert pinned_pipeline(root, "authored-film").version == "1.1"
        rows = _rows(root / "approvals.jsonl")
        _write_rows(root / "approvals.jsonl", list(reversed(rows)))
        with pytest.raises(ReceiptChainError, match="reordered"):
            pinned_pipeline(root, "authored-film")
        # deleting the downgrade receipt does not resurrect the 1.2 pin (a manifest downgrade/upgrade by deletion)
        _write_rows(root / "approvals.jsonl", rows[:1])
        with pytest.raises(ReceiptChainError, match="missing from the local receipt file"):
            pinned_pipeline(root, "authored-film")
        with pytest.raises(Exception, match="missing from the local receipt file"):
            from lib.invalidation import invalidated_checkpoints

            invalidated_checkpoints(root.parent, "p", ["canon_ingest", "headshots"])

    def test_chain_rows_are_signed_and_linked(self, root):
        c = character_look()
        r1 = activate_look(root, c)
        r2 = retire_look(root, c)
        chain = gates.chain_rows("p", "approval")
        assert [r["receipt_id"] for r in chain] == [r1["receipt_id"], r2["receipt_id"]]
        assert chain[0]["prev_receipt_id"] is None and chain[1]["prev_receipt_id"] == r1["receipt_id"]
        assert chain[1]["kind"] == "look_lock" and chain[1]["record_sha256"] == gates.receipt_digest(r2)
        assert gates.chain_tip("p", "approval") == r2["receipt_id"]
        # tampering with the orchestrator-side chain is detected too
        path = gates.chain_path("p", "approval")
        rows = _rows(path)
        rows[0]["kind"] = "headshot"
        _write_rows(path, rows)
        with pytest.raises(gates.ReceiptChainError, match="bad signature"):
            gates.chain_rows("p", "approval")

    def test_crash_between_local_append_and_chain_append_is_recovered(self, root, monkeypatch):
        """The one tolerated divergence: THIS receipt is the unchained last row."""
        c = character_look()
        real = gates.chain_append

        def crash(project_id, stream, receipt):
            raise OSError("disk full before the chain row")

        monkeypatch.setattr(gates, "chain_append", crash)
        with pytest.raises(OSError):
            activate_look(root, c)
        monkeypatch.setattr(gates, "chain_append", real)
        assert gates.chain_rows("p", "approval") == [] and len(_rows(root / "approvals.jsonl")) == 1
        # readers run WAL recovery first: the unchained tail row is exactly the WAL's receipt, so it is chained
        assert active_looks(root)[("character", CHAR)].look_hash == look_hash(c)
        assert len(gates.chain_rows("p", "approval")) == 1 and gates.wal_entries() == []
        # any OTHER unchained row is still a divergence
        rows = _rows(root / "approvals.jsonl")
        _write_rows(root / "approvals.jsonl", rows + [dict(rows[0], receipt_id="extra")])
        with pytest.raises(ReceiptChainError, match="not in the signed chain"):
            active_looks(root)


# ---- #3: gate-side constructors ------------------------------------------------


class TestGateConstructors:
    def _wayfinder(self, root, tmp_path, payload):
        wf = tmp_path / "wf"
        ticket = write_ticket(wf, payload)
        (root / "project.yaml").write_text(f"wayfinder_root: {wf}\n")
        return wf, ticket

    def test_look_lock_hint_that_disagrees_with_the_ticket_is_refused(self, root, human_tty, tmp_path):
        from lib.look_ingest import look_lock_request, parse_look_ticket

        c = character_look()
        wf, ticket = self._wayfinder(root, tmp_path, c)
        look = parse_look_ticket(ticket, wayfinder_root=wf)
        req = json.loads(look_lock_request(root, "p", look, ticket_path=ticket.relative_to(wf)).read_text())
        req["approval_record"] = dict(c, minor=True)  # the agent's record is only a hint
        (root / ".gate-requests" / f"{req['request_id']}.json").write_text(json.dumps(req))
        with pytest.raises(gate_approve.GateHandlerError, match="does not equal the record constructed"):
            approve_request(req, root, note=None)
        assert not (root / "approvals.jsonl").exists()
        # a malformed look block in the ticket itself never becomes canon either
        bad = write_ticket(wf, character_look(prompt_safe_description="too short"), name="bad.md")
        req["approval_record"] = None
        req["source_ticket_path"] = str(bad.relative_to(wf))
        with pytest.raises(gate_approve.GateHandlerError, match="look ticket refused"):
            approve_request(req, root, note=None)
        # ...and a ticket outside the configured wayfinder root is not authority
        elsewhere = write_ticket(tmp_path / "elsewhere", c)
        req["source_ticket_path"] = str(elsewhere)
        with pytest.raises(gate_approve.GateHandlerError, match="not confined|not under"):
            approve_request(req, root, note=None)

    def test_constructed_record_is_printed_as_canonical_json(self, root, human_tty, tmp_path, capsys):
        from lib.look_ingest import look_lock_request, parse_look_ticket

        c = character_look()
        wf, ticket = self._wayfinder(root, tmp_path, c)
        look = parse_look_ticket(ticket, wayfinder_root=wf)
        req = json.loads(look_lock_request(root, "p", look, ticket_path=ticket.relative_to(wf)).read_text())
        built = gate_approve.construct(root, req)
        gate_approve.show_request(req, root, built=built)
        out = capsys.readouterr().out
        assert gate_approve.canonical_text(look.payload) in out
        assert f"record sha256: {look.look_hash}" in out and "supersedes_look_hash" in out

    def test_reference_import_record_is_rebuilt_from_the_staged_bytes(self, root, human_tty):
        from tests.lib.look_lock_helpers import png_bytes

        src = root / ".staging" / "in.png"
        src.parent.mkdir(parents=True)
        src.write_bytes(png_bytes("made-elsewhere"))
        prepared = prepare_reference_import(root, "p", src, origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="elsewhere-gen")
        req = json.loads(prepared.request_path.read_text())
        # attestation text is fixed: an edited hint is refused, an edited staged file is refused
        forged = dict(req, approval_record=dict(req["approval_record"], attestation_text="a photo, honestly"))
        (root / ".gate-requests" / f"{req['request_id']}.json").write_text(json.dumps(forged))
        with pytest.raises(gate_approve.GateHandlerError, match="does not equal the record constructed"):
            approve_request(forged, root, note=None)
        (root / ".gate-requests" / f"{req['request_id']}.json").write_text(json.dumps(req))
        staged_bytes = prepared.staged_path.read_bytes()
        prepared.staged_path.write_bytes(png_bytes("swapped"))
        with pytest.raises(gate_approve.GateHandlerError, match="hashes to"):
            approve_request(req, root, note=None)
        prepared.staged_path.write_bytes(staged_bytes)
        receipt = approve_request(req, root, note=None)
        assert receipt["record"] == import_record(ORIGIN_IMPORTED_SYNTHETIC, prepared.normalized_pixel_hash, origin_tool="elsewhere-gen")
        assert receipt["entity_id"] == f"reference-{prepared.normalized_pixel_hash[:12]}"

    def test_pipeline_migration_binds_checkpoints_on_disk_and_rejects_foreign_hint(self, root, human_tty):
        from lib.pipeline_pin import prepare_migration_request

        (root / "checkpoint_canon_ingest.json").write_text(json.dumps({"stage": "canon_ingest", "status": "completed"}))
        req = json.loads(prepare_migration_request(root, "p", "authored-film", "1.2").read_text())
        assert req["approval_record"]["bound_checkpoints"] == [{
            "stage": "canon_ingest",
            "checkpoint_digest": hashlib.sha256((root / "checkpoint_canon_ingest.json").read_bytes()).hexdigest(),
        }]
        # the checkpoint changed after the request was written: the record no longer matches
        (root / "checkpoint_canon_ingest.json").write_text(json.dumps({"stage": "canon_ingest", "status": "completed", "x": 1}))
        with pytest.raises(gate_approve.GateHandlerError, match="does not equal the record constructed"):
            approve_request(req, root, note=None)
        req["approval_record"] = None
        req["envelope"] = None
        receipt = approve_request(req, root, note=None)
        pin = pinned_pipeline(root, "authored-film")
        assert pin.version == "1.2" and pin.binds_checkpoint(
            "canon_ingest", hashlib.sha256((root / "checkpoint_canon_ingest.json").read_bytes()).hexdigest()
        )
        assert receipt["record"]["version"] == "1.2"

    def test_config_record_is_the_digest_of_project_yaml_on_disk(self, root, human_tty):
        from tests.lib.test_gate_approve_cli import PROJECT_CONFIG

        import yaml

        raw = yaml.safe_dump(PROJECT_CONFIG, sort_keys=True).encode("utf-8")
        (root / "project.yaml").write_bytes(raw)
        req = {"request_id": "cfg", "project_id": "p", "stage": "proposal", "scope": "config", "kind": "config",
               "entity_id": "project_config", "approval_record": {"config_sha256": "0" * 64}, "summary": "s"}
        (root / ".gate-requests" / "cfg.json").write_text(json.dumps(req))
        with pytest.raises(gate_approve.GateHandlerError, match="does not equal the record constructed"):
            approve_request(req, root, note=None)
        req["approval_record"] = None
        receipt = approve_request(req, root, note=None)
        assert receipt["record"] == {"config_sha256": hashlib.sha256(raw).hexdigest()}
        from lib.project_config import load_verified_project_config

        assert load_verified_project_config(root).digest == hashlib.sha256(raw).hexdigest()

    def test_every_kind_has_a_constructor_and_a_checkpoint_kind_needs_the_pending_checkpoint(self, root, human_tty):
        assert set(gate_approve.CONSTRUCTORS) == set(receipts.APPROVAL_KINDS)
        for kind, stage, scope in (("hero", "visual_bible", "character:x"), ("storyboard_batch", "assets", "storyboard_batch"),
                                   ("artifact_review", "scene_plan", "artifact:scene_plan")):
            req = {"request_id": f"r-{kind}", "project_id": "p", "stage": stage, "scope": scope, "kind": kind,
                   "entity_id": "x" if kind == "hero" else None, "artifact": {"artifact_type": "scene_plan"},
                   "approval_record": {"id": "x", "assets": {"hero": "a" * 64}}, "summary": "s"}
            (root / ".gate-requests" / f"r-{kind}.json").write_text(json.dumps(req))
            with pytest.raises(gate_approve.GateHandlerError, match=f"no checkpoint_{stage}.json"):
                approve_request(req, root, note=None)
        assert not (root / "approvals.jsonl").exists()


# ---- #6: headshot selection re-runs enforcement ------------------------------


class TestHeadshotSelectionEnforcement:
    @pytest.fixture
    def pinned(self, root):
        pin_project(root, "1.2")
        return root

    def test_candidate_not_generated_from_the_active_look_is_refused(self, pinned, human_tty):
        c = character_look()
        activate_look(pinned, c)
        stray = image(pinned, "stray", prompt=RECIPE_PROMPT, receipt_prompt_recipe=recipe_for(c))  # no look_refs
        _pending_checkpoint(pinned, c, [stray])
        with pytest.raises(gate_approve.GateHandlerError, match="not receipted with look_ref"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)
        assert receipts.find_approval(pinned, "headshot", entity_id=CHAR) is None

    def test_recipe_and_prompt_must_match_the_receipt(self, pinned, human_tty):
        c = character_look()
        activate_look(pinned, c)
        edited = image(pinned, "edited", look_refs=look_refs_for(c), receipt_prompt_recipe=recipe_for(c), prompt="hand-edited prompt")
        _pending_checkpoint(pinned, c, [edited])
        with pytest.raises(gate_approve.GateHandlerError, match="does not hash to prompt_recipe.rendered_sha256"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)
        other_recipe = dict(recipe_for(c), builder_version="builder/9")
        _pending_checkpoint(pinned, c, [candidate(pinned, c, "ok")], recipe=other_recipe)
        with pytest.raises(gate_approve.GateHandlerError, match="receipted with prompt_recipe"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)

    def test_packet_look_ref_must_be_the_active_receipt(self, pinned, human_tty):
        c = character_look()
        r = activate_look(pinned, c)
        cand = candidate(pinned, c, "c0")
        stale_ref = {"entity_kind": "character", "entity_id": CHAR, "look_hash": look_hash(c), "receipt_id": "stale-receipt"}
        _pending_checkpoint(pinned, c, [cand], look_ref=stale_ref)
        with pytest.raises(gate_approve.GateHandlerError, match="active look_lock receipt is"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)
        # a superseded look can no longer approve a face
        c2 = character_look(hair="shaved")
        activate_look(pinned, c2, supersedes=look_hash(c))
        _pending_checkpoint(pinned, c, [cand], look_ref=dict(stale_ref, receipt_id=r["receipt_id"]))
        with pytest.raises(gate_approve.GateHandlerError, match="active look check failed"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)

    def test_imported_claim_needs_the_import_receipts(self, pinned, human_tty):
        c = character_look()
        activate_look(pinned, c)
        cand = candidate(pinned, c, "c0")
        forged = dict(cand, provenance={
            "generator_kind": "imported", "origin_tool": "elsewhere", "attestation_receipt_id": "nope",
            "import_receipt_id": cand["provenance"]["generation_receipt_id"], "normalized_pixel_hash": cand["asset_id"],
            "generation_receipt_id": cand["provenance"]["generation_receipt_id"],
        })
        _pending_checkpoint(pinned, c, [forged])
        with pytest.raises(gate_approve.GateHandlerError, match="generator_kind 'imported' but its receipt says"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)

    def test_invalid_packet_shape_is_refused_by_schema(self, pinned, human_tty):
        c = character_look()
        activate_look(pinned, c)
        cand = candidate(pinned, c, "c0")
        cp = _pending_checkpoint(pinned, c, [cand])
        cp["artifacts"]["headshot_packet"]["characters"][0]["candidates"][0].pop("provenance")
        (pinned / "checkpoint_headshots.json").write_text(json.dumps(cp))
        with pytest.raises(gate_approve.GateHandlerError, match="schema"):
            approve_request(_headshot_request(pinned), pinned, note=None, selection=1)


# ---- #10: legacy checkpoints must be in the bound set ---------------------------


class TestLegacyCheckpointBinding:
    def test_edited_legacy_checkpoint_cannot_satisfy_a_1_2_pin(self, tmp_path):
        from lib.checkpoint import CheckpointValidationError, write_checkpoint
        from tests.lib.test_authored_film_contract import plain_decision_log, write_project_config
        from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11
        from lib.checkpoint import init_project

        pipeline_dir = tmp_path
        project_dir = init_project("p", title="Test Feature", pipeline_type="authored-film", pipeline_dir=pipeline_dir)
        write_project_config(project_dir)
        # legacy (1.1, unpinned) checkpoints
        write_checkpoint(pipeline_dir, "p", "canon_ingest", "completed", {"canon_packet": canon_packet_v11()},
                         pipeline_type="authored-film", human_approved=True)
        write_checkpoint(pipeline_dir, "p", "proposal", "completed",
                         {"proposal_packet": proposal_v11((CHAR,), ()), "decision_log": plain_decision_log()},
                         pipeline_type="authored-film", human_approved=True)
        legacy = json.loads((project_dir / "checkpoint_proposal.json").read_text())
        assert legacy["pipeline"]["version"] == "1.1"  # the default pin, no receipt
        pin_project(project_dir, "1.2")  # binds both legacy files as they are on disk
        write_checkpoint(pipeline_dir, "p", "look_lock", "in_progress", {}, pipeline_type="authored-film")
        # an edited legacy predecessor is no longer in the bound set
        legacy["artifacts"]["decision_log"]["decisions"].append(dict(legacy["artifacts"]["decision_log"]["decisions"][0], decision_id="d-999"))
        (project_dir / "checkpoint_proposal.json").write_text(json.dumps(legacy, indent=2))
        with pytest.raises(CheckpointValidationError, match="PIPELINE PIN VIOLATION.*'proposal' was written under pipeline tuple"):
            write_checkpoint(pipeline_dir, "p", "look_lock", "awaiting_human", {}, pipeline_type="authored-film")

    def test_checkpoint_from_another_tuple_is_refused(self, tmp_path):
        from lib.checkpoint import CheckpointValidationError, write_checkpoint, init_project
        from tests.lib.test_authored_film_contract import write_project_config
        from tests.lib.test_visual_bible_contract import canon_packet_v11

        pipeline_dir = tmp_path
        project_dir = init_project("p", title="Test Feature", pipeline_type="authored-film", pipeline_dir=pipeline_dir)
        write_project_config(project_dir)
        first = pin_project(project_dir, "1.2")
        write_checkpoint(pipeline_dir, "p", "canon_ingest", "completed", {"canon_packet": canon_packet_v11()},
                         pipeline_type="authored-film", human_approved=True)
        cp = json.loads((project_dir / "checkpoint_canon_ingest.json").read_text())
        assert cp["pipeline"]["version"] == "1.2"
        # forge the tuple to another digest: it is neither the pin nor a bound legacy file
        cp["pipeline"]["manifest_digest"] = "f" * 64
        (project_dir / "checkpoint_canon_ingest.json").write_text(json.dumps(cp, indent=2))
        with pytest.raises(CheckpointValidationError, match="written under pipeline tuple"):
            write_checkpoint(pipeline_dir, "p", "proposal", "awaiting_human", {}, pipeline_type="authored-film")


# ---- #12 / #13 / #14: reference import records, ICC, atomic origin recheck ------


class TestReferenceImportRecords:
    def test_casting_record_carries_no_caller_metadata(self, root):
        from tests.lib.look_lock_helpers import png_bytes

        src = root / ".staging" / "Real Person Name.jpg"
        src.parent.mkdir(parents=True)
        src.write_bytes(png_bytes("a-face"))
        prepared = prepare_reference_import(root, "p", src, origin_class=ORIGIN_CASTING,
                                            source_name="Real Person Name.jpg", origin_tool="phone")
        assert set(prepared.record) == {"origin_class", "normalized_pixel_hash", "attestation_text", "normalizer_version"}
        text = prepared.request_path.read_text() + json.dumps(prepared.record)
        assert "Real Person" not in text and "phone" not in text
        with pytest.raises(ReferenceImportError, match="no origin_tool"):
            import_record(ORIGIN_CASTING, "a" * 64, origin_tool="phone")
        with pytest.raises(ReferenceImportError, match="not the canonical"):
            validate_import_record(dict(prepared.record, source_name="x"))
        with pytest.raises(ReferenceImportError, match="not the canonical"):
            validate_import_record(dict(prepared.record, attestation_text="whatever"))

    def test_invalid_icc_profile_is_rejected(self):
        from PIL import Image

        im = Image.new("RGB", (4, 4), (10, 20, 30))
        buf = io.BytesIO()
        im.save(buf, format="PNG", icc_profile=b"not an icc profile at all")
        with pytest.raises(ReferenceImportError, match="ICC profile could not be read"):
            normalize_image_bytes(buf.getvalue())

    def test_origin_uniqueness_is_rechecked_under_the_consumed_token(self, root):
        pixel_hash = "9" * 64
        record = import_record(ORIGIN_IMPORTED_SYNTHETIC, pixel_hash, origin_tool="elsewhere")
        with gates.handler_context():
            token = gates.mint_gate_token("p", "look_lock", f"reference:{pixel_hash}", record_sha256(record))
        calls = []

        def conflict():
            calls.append(1)
            raise ReferenceImportError("pixel hash already imported as casting_inspiration")

        with pytest.raises(ReferenceImportError):
            receipts.record_human_approval(
                root, "p", "look_lock", f"reference:{pixel_hash}", record, token, "reference_import",
                entity_id="reference-999999999999",
                envelope={"origin_class": ORIGIN_IMPORTED_SYNTHETIC, "normalized_pixel_hash": pixel_hash},
                pre_commit_check=conflict,
            )
        assert calls == [1]
        # the token was spent, nothing was appended, ledgered, chained, or left in the WAL
        assert gates.consumed_binding(gates.token_digest(token)) is not None
        assert not (root / "approvals.jsonl").exists()
        assert gates.chain_rows("p", "approval") == [] and gates.wal_entries() == []
        with pytest.raises(gates.GateTokenInvalid):
            receipts.record_human_approval(
                root, "p", "look_lock", f"reference:{pixel_hash}", record, token, "reference_import",
                entity_id="reference-999999999999",
                envelope={"origin_class": ORIGIN_IMPORTED_SYNTHETIC, "normalized_pixel_hash": pixel_hash},
            )

    def test_two_pending_requests_for_the_same_pixels_cannot_both_land(self, root, human_tty):
        from tests.lib.look_lock_helpers import png_bytes

        data = png_bytes("same-pixels")
        a = root / ".staging" / "a.png"
        a.parent.mkdir(parents=True)
        a.write_bytes(data)
        b = root / ".staging" / "b.png"
        b.write_bytes(data)
        first = prepare_reference_import(root, "p", a, origin_class=ORIGIN_CASTING, request_id="first")
        second = prepare_reference_import(root, "p", b, origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool="x", request_id="second")
        assert first.normalized_pixel_hash == second.normalized_pixel_hash
        approve_request(json.loads(first.request_path.read_text()), root, note=None)
        with pytest.raises(gate_approve.GateHandlerError, match="already imported as casting_inspiration"):
            approve_request(json.loads(second.request_path.read_text()), root, note=None)
        assert tainted_hashes(root) == {first.normalized_pixel_hash}
        assert len(receipts.verified_approvals(root, "reference_import")) == 1

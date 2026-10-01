"""WriterOS export as a look source (look sessions, 2026-09-30, Task 9):
per-record freshness against the package's memory snapshot, the look_lock gate
and look_run with --source writeros, the exact activate-envelope source rule,
and supersession across sources. Invented names only."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from lib.look_ingest import (
    IngestedLook,
    LookIngestError,
    active_look_for,
    build_look_packet,
    ingest_writeros_looks,
    parse_writeros_export,
    verify_look_refs,
)
from lib.look_spec import look_hash as _look_hash
from lib.receipts import validate_envelope
from schemas.artifacts import validate_artifact
from scripts import gate_approve, look_run
from scripts.look_run import LookRunError, run_look
from tests.lib.d19_helpers import config_1_1
from tests.lib.look_lock_helpers import (
    CHAR, CHAR2, LOC, PROJECT, approve_request, character_look, location_look, pin_project, write_ticket,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401
    PIPELINE, approval_policy_decision, gates_dir, plain_decision_log, project, write_project_config,
)
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11

WRITEROS_PROJECT = "vector-show-1234"
REF_DEP = {"writeros_record_id": "mem_" + "0123456789abcdef" * 2, "content_hash": "ab" * 32}
CAPTURED = "2026-09-30T12:00:00.000Z"


def rid(n: int) -> str:
    return f"mem_{n:032x}"


def w1(**overrides) -> dict:
    return character_look(version="1.1", depends_on=[REF_DEP], **overrides)


def look_record(record_id: str, spec: dict, *, reference="generated-elsewhere", status="active", kind="canon") -> dict:
    return {
        "id": record_id, "projectId": WRITEROS_PROJECT, "kind": kind, "status": status,
        "claim": f"Look: {spec['entity_id']}", "tags": ["look"], "entities": ["Someone"],
        "source": {"workflow": "writeros", "sourceId": f"looks/{spec['entity_kind']}/{spec['entity_id']}",
                   "sourceUri": "writeros:looks", "sourceHash": _look_hash(spec), "capturedAt": CAPTURED, "approval": "explicit"},
        "evidence": [], "safety": "clear", "spoiler": False, "supersedes": [], "createdAt": CAPTURED, "updatedAt": CAPTURED,
        "payload": {"kind": "look_spec", "version": spec["version"], "spec": spec, "lookHash": _look_hash(spec), "reference": reference},
    }


def other_record(record_id: str) -> dict:
    return {"id": record_id, "projectId": WRITEROS_PROJECT, "kind": "decision", "status": "active", "claim": "Unrelated.",
            "tags": [], "entities": [], "source": {"workflow": "writeros", "sourceId": "x", "sourceUri": "x",
            "sourceHash": "f" * 64, "capturedAt": CAPTURED, "approval": "none"}, "evidence": [], "safety": "clear",
            "spoiler": False, "supersedes": [], "createdAt": CAPTURED, "updatedAt": CAPTURED}


def export_entry(record: dict) -> dict:
    spec = record["payload"]["spec"]
    return {"entity_kind": spec["entity_kind"], "entity_id": spec["entity_id"], "look_spec": spec,
            "look_hash": record["payload"]["lookHash"], "promotion_id": record["id"], "promoted_at": CAPTURED,
            "reference": record["payload"]["reference"]}


class Package:
    """A synthetic .writeros package laid out as WriterOS writes it."""

    def __init__(self, parent: Path):
        self.path = parent / "Vector Show (vector).writeros"
        (self.path / "memory" / "exports").mkdir(parents=True)
        (self.path / "project.json").write_text(json.dumps({"schemaVersion": 1, "projectId": WRITEROS_PROJECT}))
        self.records: list[dict] = []
        self.revision = 0

    def snapshot(self) -> None:
        (self.path / "memory" / "snapshot.json").write_text(json.dumps({
            "schemaVersion": 1, "projectId": WRITEROS_PROJECT, "revision": self.revision,
            "records": self.records, "conflicts": []}))

    def add(self, record: dict) -> dict:
        self.revision += 1
        self.records.append(record)
        self.snapshot()
        return record

    def supersede(self, record_id: str) -> None:
        for record in self.records:
            if record["id"] == record_id:
                record["status"] = "superseded"
        self.snapshot()

    def export(self, entries: list[dict] | None = None, *, revision: int | None = None, keep_old: bool = False) -> Path:
        exports = self.path / "memory" / "exports"
        if not keep_old:
            for old in exports.glob("look-locks-*.json"):
                old.unlink()
        if entries is None:
            entries = [export_entry(r) for r in self.records
                       if r["kind"] == "canon" and r["status"] == "active" and r.get("payload")]
        rev = self.revision if revision is None else revision
        path = exports / f"look-locks-{rev}.json"
        path.write_text(json.dumps({"version": 1, "project_id": WRITEROS_PROJECT, "memory_revision": rev,
                                    "written_at": CAPTURED, "looks": entries}, indent=2))
        return path


@pytest.fixture
def pkg(tmp_path) -> Package:
    return Package(tmp_path)


# ---- parse_writeros_export: per-record freshness ----

class TestExportIngest:
    def test_happy_path_carries_the_source_and_reference(self, pkg):
        rec = pkg.add(look_record(rid(1), w1(), reference="casting-inspiration"))
        pkg.export()
        looks = parse_writeros_export(pkg.path)
        look = looks[("character", CHAR)]
        assert look.look_hash == _look_hash(w1())
        assert look.source_ticket_ref is None
        assert look.source_ref == {"system": "writeros", "record_id": rec["id"], "memory_revision": 1,
                                   "reference": "casting-inspiration"}

    def test_still_accepted_after_unrelated_memory_writes(self, pkg):
        pkg.add(look_record(rid(1), w1()))
        pkg.export()
        pkg.add(other_record(rid(2)))
        pkg.add(other_record(rid(3)))
        assert pkg.revision == 3
        assert ("character", CHAR) in parse_writeros_export(pkg.path)

    def test_hash_mismatch_is_refused(self, pkg):
        rec = pkg.add(look_record(rid(1), w1()))
        entry = dict(export_entry(rec), look_hash="0" * 64)
        pkg.export([entry])
        with pytest.raises(LookIngestError, match="hash mismatch"):
            parse_writeros_export(pkg.path)

    @pytest.mark.parametrize("change", ["superseded", "spec", "reference", "not-canon", "missing-record"])
    def test_a_stale_entry_is_refused(self, pkg, change):
        rec = pkg.add(look_record(rid(1), w1()))
        pkg.export()
        if change == "superseded":
            pkg.supersede(rec["id"])
        elif change == "spec":
            rec["payload"]["spec"] = w1(hair="shaved")
            rec["payload"]["lookHash"] = _look_hash(rec["payload"]["spec"])
            pkg.snapshot()
        elif change == "reference":
            rec["payload"]["reference"] = "none"
            pkg.snapshot()
        elif change == "not-canon":
            rec["kind"] = "decision"
            pkg.snapshot()
        else:
            pkg.records.clear()
            pkg.snapshot()
        with pytest.raises(LookIngestError, match="Re-export"):
            parse_writeros_export(pkg.path)

    def test_an_export_missing_an_active_look_is_refused(self, pkg):
        a = pkg.add(look_record(rid(1), w1()))
        pkg.export()
        pkg.add(look_record(rid(2), location_look(version="1.1")))  # promoted; its export write failed
        with pytest.raises(LookIngestError, match="exactly the active WriterOS looks"):
            parse_writeros_export(pkg.path)
        pkg.export()
        assert set(parse_writeros_export(pkg.path)) == {("character", CHAR), ("location", LOC)}
        assert a

    def test_duplicate_entries_are_refused(self, pkg):
        rec = pkg.add(look_record(rid(1), w1()))
        pkg.export([export_entry(rec), export_entry(rec)])
        with pytest.raises(LookIngestError, match="twice"):
            parse_writeros_export(pkg.path)

    def test_the_highest_revision_file_is_read(self, pkg):
        old = pkg.add(look_record(rid(1), w1()))
        pkg.export()
        pkg.supersede(old["id"])
        pkg.add(look_record(rid(2), w1(hair="shaved")))
        pkg.export(keep_old=True)
        files = sorted(p.name for p in (pkg.path / "memory" / "exports").iterdir())
        assert files == ["look-locks-1.json", "look-locks-2.json"]
        assert parse_writeros_export(pkg.path)[("character", CHAR)].source_ref["record_id"] == rid(2)

    def test_an_entry_without_a_valid_reference_is_refused(self, pkg):
        rec = pkg.add(look_record(rid(1), w1()))
        entry = export_entry(rec)
        del entry["reference"]
        pkg.export([entry])
        with pytest.raises(LookIngestError, match="entry shape"):
            parse_writeros_export(pkg.path)
        pkg.export([dict(export_entry(rec), reference="photo")])
        with pytest.raises(LookIngestError, match="reference"):
            parse_writeros_export(pkg.path)

    def test_project_id_mismatch_is_refused(self, pkg):
        pkg.add(look_record(rid(1), w1()))
        path = pkg.export()
        data = json.loads(path.read_text()); data["project_id"] = "someone-else"
        path.write_text(json.dumps(data))
        with pytest.raises(LookIngestError, match="names project"):
            parse_writeros_export(pkg.path)

    def test_package_path_rules(self, pkg, tmp_path):
        pkg.add(look_record(rid(1), w1()))
        pkg.export()
        link = tmp_path / "Linked.writeros"
        link.symlink_to(pkg.path)
        with pytest.raises(LookIngestError, match="symlink"):
            parse_writeros_export(link)
        with pytest.raises(LookIngestError, match="absolute"):
            parse_writeros_export(Path("relative.writeros"))
        exports = pkg.path / "memory" / "exports"
        target = tmp_path / "elsewhere.json"
        target.write_text((exports / "look-locks-1.json").read_text())
        (exports / "look-locks-1.json").unlink()
        (exports / "look-locks-9.json").symlink_to(target)
        with pytest.raises(LookIngestError):
            parse_writeros_export(pkg.path)

    def test_ingested_look_needs_exactly_one_source(self):
        spec = w1()
        with pytest.raises(LookIngestError):
            IngestedLook("character", CHAR, spec, _look_hash(spec))
        with pytest.raises(LookIngestError):
            IngestedLook("character", CHAR, spec, _look_hash(spec), source_ticket_ref={"id": "wf-0badc0de"},
                         source_ref={"system": "writeros", "record_id": rid(1), "memory_revision": 1, "reference": "none"})


# ---- the exact activate-envelope source rule ----

def _env(**source):
    spec = w1()
    return spec, {"action": "activate", "entity_kind": "character", "look_hash": _look_hash(spec),
                  "supersedes_look_hash": None, **source}


class TestEnvelopeSource:
    def test_wayfinder_with_empty_promotion_refs_is_the_existing_shape(self):
        spec, env = _env(source_ticket_ref={"id": "wf-0badc0de"}, promotion_refs=[])
        validate_envelope("look_lock", spec, _look_hash(spec), CHAR, env)
        spec, env = _env(source_ticket_ref={"path": "wayfinder/resolved/a.md", "content_sha256": "c" * 64})
        validate_envelope("look_lock", spec, _look_hash(spec), CHAR, env)

    def test_writeros_promotion_without_a_ticket_key(self):
        spec, env = _env(promotion_refs=[{"system": "writeros", "record_id": rid(1)}])
        validate_envelope("look_lock", spec, _look_hash(spec), CHAR, env)

    @pytest.mark.parametrize("source", [
        {"source_ticket_ref": None, "promotion_refs": [{"system": "writeros", "record_id": rid(1)}]},
        {"source_ticket_ref": {"id": "wf-0badc0de"}, "promotion_refs": [{"system": "writeros", "record_id": rid(1)}]},
        {"promotion_refs": [{"system": "writeros", "record_id": "mem_short"}]},
        {"promotion_refs": [{"system": "writeros", "record_id": rid(1), "memory_revision": 3}]},
        {"promotion_refs": [{"system": "wayfinder", "record_id": rid(1)}]},
        {"promotion_refs": []},
        {},
        {"source_ticket_ref": {"id": "wf-XYZ"}},
    ])
    def test_everything_else_is_refused(self, source):
        spec, env = _env(**source)
        with pytest.raises(ValueError):
            validate_envelope("look_lock", spec, _look_hash(spec), CHAR, env)

    def test_retire_keeps_its_contract(self):
        spec = w1()
        record = {"entity_kind": "character", "entity_id": CHAR, "look_hash": _look_hash(spec)}
        validate_envelope("look_lock", record, "x" * 64, CHAR,
                          {"action": "retire", "entity_kind": "character", "look_hash": _look_hash(spec)})


# ---- look_run --source writeros through the real gate ----

def write(pipeline_dir, stage, artifacts, *, status="completed"):
    from lib.checkpoint import write_checkpoint
    return write_checkpoint(pipeline_dir, PROJECT, stage, status, artifacts, pipeline_type=PIPELINE, human_approved=True)


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    pipeline_dir, project_dir = project
    import sys

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())
    import lib.events as events_mod
    import lib.paths as paths_mod
    monkeypatch.setattr(events_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", pipeline_dir)
    wf = tmp_path / "wayfinder-root"
    wf.mkdir()
    package = Package(tmp_path)
    cfg = config_1_1(); cfg["wayfinder_root"] = str(wf); cfg["writeros_package"] = str(package.path)
    digest = write_project_config(project_dir, cfg)
    pin_project(project_dir, "1.3")
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    log = plain_decision_log(); log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11((CHAR, CHAR2), (LOC,)), "decision_log": log})
    return {"pipeline": pipeline_dir, "project": project_dir, "wf": wf, "pkg": package, "out": io.StringIO()}


def _req(w, request_id):
    return json.loads((w["project"] / ".gate-requests" / f"{request_id}.json").read_text())


class TestLookRunWriterOS:
    def test_promotion_to_gate_to_packet(self, world):
        w = world
        rec = w["pkg"].add(look_record(rid(1), w1(), reference="casting-inspiration"))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        assert r["status"] == "pending"
        req = _req(w, r["request_id"])
        assert req["source_promotion_id"] == rec["id"] and "source_ticket_path" not in req
        assert req["envelope"]["promotion_refs"] == [{"system": "writeros", "record_id": rec["id"]}]
        assert "source_ticket_ref" not in req["envelope"]
        assert "Reference image: casting-inspiration" in req["summary"]

        built = gate_approve.construct(w["project"], req)
        shown = io.StringIO()
        gate_approve.show_constructed(built, shown)
        assert f"Source: WriterOS promotion {rec['id']} (memory revision 1) · Reference image: casting-inspiration" in shown.getvalue()
        assert "casting-inspiration" not in json.dumps(built.record) and "reference" not in built.envelope

        receipt = approve_request(req, w["project"])
        assert receipt["promotion_refs"] == [{"system": "writeros", "record_id": rec["id"]}]
        assert "source_ticket_ref" not in receipt
        r2 = run_look(w["project"], CHAR, out=w["out"])
        assert r2["status"] == "ratified"
        cp = json.loads((w["project"] / "checkpoint_look_lock.json").read_text())
        packet = cp["artifacts"]["look_packet"]
        validate_artifact("look_packet", packet)
        assert packet["looks"][0]["source_ref"] == {"system": "writeros", "record_id": rec["id"],
                                                     "memory_revision": 1, "reference": "casting-inspiration"}
        assert active_look_for(w["project"], "character", CHAR).look_hash == _look_hash(w1())
        verify_look_refs(w["project"], [{"entity_kind": "character", "entity_id": CHAR, "look_hash": _look_hash(w1())}])

    def test_approval_survives_another_promotion_rewriting_the_export(self, world):
        w = world
        w["pkg"].add(look_record(rid(1), w1()))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        # Before Ben approves, the writer promotes a location: WriterOS rewrites the export at a new revision.
        w["pkg"].add(look_record(rid(2), location_look(version="1.1")))
        w["pkg"].export()
        assert not (w["pkg"].path / "memory" / "exports" / "look-locks-1.json").exists()
        approve_request(_req(w, r["request_id"]), w["project"])
        assert run_look(w["project"], CHAR, out=w["out"])["status"] == "ratified"

    def test_a_superseded_promotion_cannot_be_approved(self, world):
        w = world
        w["pkg"].add(look_record(rid(1), w1()))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        w["pkg"].supersede(rid(1))
        w["pkg"].add(look_record(rid(2), w1(hair="shaved")))
        w["pkg"].export()
        with pytest.raises(gate_approve.GateHandlerError, match="not an active look"):
            gate_approve.construct(w["project"], _req(w, r["request_id"]))

    def test_republish_refuses_a_different_promotion_of_the_same_look(self, world):
        w = world
        w["pkg"].add(look_record(rid(1), w1(), reference="none"))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()  # the request file goes missing
        # Same block (same hash), promoted again with a different reference mode: a new record.
        w["pkg"].supersede(rid(1))
        w["pkg"].add(look_record(rid(2), w1(), reference="casting-inspiration"))
        w["pkg"].export()
        with pytest.raises(LookRunError, match="promotion changed mid-run"):
            run_look(w["project"], CHAR, out=w["out"])
        assert not (w["project"] / ".gate-requests" / f"{r['request_id']}.json").exists()

    def test_republish_restores_the_same_promotion(self, world):
        w = world
        rec = w["pkg"].add(look_record(rid(1), w1(), reference="none"))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()
        again = run_look(w["project"], CHAR, out=w["out"])
        assert again["status"] == "pending" and again["request_id"] == r["request_id"]
        assert _req(w, r["request_id"])["source_promotion_id"] == rec["id"]

    def test_auto_prefers_writeros_when_the_export_names_the_entity(self, world):
        w = world
        w["pkg"].add(look_record(rid(1), w1()))
        w["pkg"].export()
        write_ticket(w["wf"], location_look())
        r_char = run_look(w["project"], CHAR, out=w["out"])
        assert "source_promotion_id" in _req(w, r_char["request_id"])
        r_loc = run_look(w["project"], LOC, entity_kind="location", out=w["out"])
        assert "source_ticket_path" in _req(w, r_loc["request_id"])

    def test_writeros_source_without_a_package_is_a_plain_error(self, world):
        w = world
        with pytest.raises(LookRunError, match="no look export"):
            run_look(w["project"], CHAR, source="writeros", out=w["out"])

    def test_wayfinder_look_superseded_by_a_writeros_promotion(self, world):
        w = world
        old = character_look()
        write_ticket(w["wf"], old)
        r = run_look(w["project"], CHAR, source="wayfinder", out=w["out"])
        approve_request(_req(w, r["request_id"]), w["project"])
        run_look(w["project"], CHAR, out=w["out"])
        old_hash = _look_hash(old)
        assert active_look_for(w["project"], "character", CHAR).look_hash == old_hash

        w["pkg"].add(look_record(rid(1), w1()))
        w["pkg"].export()
        with pytest.raises(LookRunError, match="WriterOS look now hashes"):
            run_look(w["project"], CHAR, source="writeros", out=w["out"])
        r2 = run_look(w["project"], CHAR, source="writeros", supersede=True, out=w["out"])
        req2 = _req(w, r2["request_id"])
        assert req2["envelope"]["supersedes_look_hash"] == old_hash
        receipt = approve_request(req2, w["project"])
        assert receipt["supersedes_look_hash"] == old_hash
        run_look(w["project"], CHAR, out=w["out"])
        assert active_look_for(w["project"], "character", CHAR).look_hash == _look_hash(w1())
        with pytest.raises(LookIngestError, match="superseded"):
            verify_look_refs(w["project"], [{"entity_kind": "character", "entity_id": CHAR, "look_hash": old_hash}])

    def test_packet_with_both_sources_validates(self, world):
        w = world
        ticket_look = location_look()
        write_ticket(w["wf"], ticket_look)
        r = run_look(w["project"], LOC, entity_kind="location", source="wayfinder", out=w["out"])
        approve_request(_req(w, r["request_id"]), w["project"])
        run_look(w["project"], LOC, entity_kind="location", out=w["out"])
        w["pkg"].add(look_record(rid(1), w1()))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        approve_request(_req(w, r["request_id"]), w["project"])
        run_look(w["project"], CHAR, out=w["out"])
        packet = json.loads((w["project"] / "checkpoint_look_lock.json").read_text())["artifacts"]["look_packet"]
        validate_artifact("look_packet", packet)
        assert {("source_ref" in e, "source_ticket_ref" in e) for e in packet["looks"]} == {(True, False), (False, True)}

        looks = dict(ingest_writeros_looks(w["project"]))
        built = build_look_packet(w["project"], looks)
        validate_artifact("look_packet", built)
        assert "source_ticket_ref" not in built["looks"][0]

    def test_front_lot_renders_the_writeros_source(self, world):
        from backlot import state

        w = world
        rec = w["pkg"].add(look_record(rid(1), w1(), reference="none"))
        w["pkg"].export()
        r = run_look(w["project"], CHAR, source="writeros", out=w["out"])
        rendered = state._render_look_lock(w["project"], _req(w, r["request_id"]))
        assert rendered["source_ref"]["record_id"] == rec["id"] and rendered["look_hash"] == _look_hash(w1())
        approve_request(_req(w, r["request_id"]), w["project"])
        run_look(w["project"], CHAR, out=w["out"])
        summary = state._looks_summary(w["project"])
        assert summary[0]["source_ref"]["reference"] == "none"

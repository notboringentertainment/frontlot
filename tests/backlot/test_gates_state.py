"""Read-only Backlot gate state and lazy detail packets.

The fixture is deliberately raw: no gate constructor or receipt reader is
used to author expected state.  All names are invented test identifiers.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from backlot.state import GATE_RENDERERS, load_board_state, load_gate_detail
from lib import qc_receipts, receipts, run_common, run_lease
from lib.canon_view import plan_view
from lib.canon_enforcement import character_approval_record
from lib.pipeline_loader import manifest_digest
from lib.pipeline_pin import checkpoint_digests_on_disk, migration_record, pinned_pipeline
from lib.reference_import import (
    ORIGIN_CASTING,
    ORIGIN_IMPORTED_SYNTHETIC,
    STAGING_DIR,
    import_record,
    normalize_reference_import,
    publish_import_request,
    stage_reference_upload,
)
from lib.receipts import APPROVAL_KINDS
from tests.lib.look_lock_helpers import CHAR, PROJECT, _approve, character_look, pin_project, png_bytes, write_ticket


LOC_ID = "loc-01-aabbccdd"
POSTER_ID = "poster"
CANON_CHAR = "char-02-aabbccdd"
FIXED_MTIME = 1_700_000_000
TICKET_LOOK_HASH = "939a9ca11a23d5d68006ea8d82b46c60b9c562be761758087c8e2c794d2fcc63"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_image(project: Path, seed: str, *, role: str, relative: str | None = None) -> dict[str, object]:
    data = png_bytes(seed, size=(11, 7))
    digest = hashlib.sha256(data).hexdigest()
    rel = relative or f"canon/visual/objects/{digest}.png"
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "asset_id": digest,
        "path": rel,
        "role": role,
        "provenance": {
            "generator_kind": "model",
            "model_endpoint": "test/image-model",
            "prompt": f"invented {role} fixture",
            "generation_receipt_id": f"generation-{seed}",
        },
    }


def _request(project: Path, state: str, request_id: str, kind: str, **extra: object) -> dict:
    row = {
        "request_id": request_id,
        "project_id": PROJECT,
        "kind": kind,
        "stage": str(extra.pop("stage", "visual_bible")),
        "scope": str(extra.pop("scope", f"character:{CHAR}")),
        "entity_id": str(extra.pop("entity_id", CHAR)),
        "summary": str(extra.pop("summary", f"Review {kind} for {CHAR}.")),
        **extra,
    }
    relative = {"pending": Path(), "done": Path("done"), "declined": Path("declined"), "abandoned": Path("abandoned")}[state]
    path = project / ".gate-requests" / relative / f"{request_id}.json"
    _write_json(path, row)
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return row


def _qc_verdict(project: Path, *, receipt_role: str, asset_id: str, attempt: str,
                verdict: str = "pass", failing_item: str | None = None) -> dict:
    items = [{"id": failing_item or "single_subject", "answer": "no" if verdict == "fail" else "yes",
              "note": "invented evidence", "severity": "fail"}]
    value = {
        "version": "1.0",
        "project_id": PROJECT,
        "entity_kind": "character",
        "entity_id": CHAR,
        "role": receipt_role,
        "asset_id": asset_id,
        "look_hash": "c" * 64,
        "look_receipt_id": "look-receipt-current",
        "headshot_asset_id": "d" * 64,
        "headshot_receipt_id": "headshot-receipt-current",
        "policy_version": "test-policy/1",
        "policy_bundle_sha256": "e" * 64,
        "provider": "openai",
        "model": "test-judge",
        "prompt_sha256": "f" * 64,
        "provider_request_id": f"provider-{attempt}",
        "provider_model_version": "test-judge-1",
        "raw_response_asset_id": hashlib.sha256(f"raw-{attempt}".encode()).hexdigest(),
        "attempt_id": attempt,
        "attempt_n": 1,
        "items": items,
        "verdict": verdict,
        "failing_items": [failing_item] if failing_item else [],
        "warnings": [],
        "cost_usd": 0.01,
        "judged_at": "2026-08-30T00:00:00+00:00",
    }
    value["tuple_sha256"] = qc_receipts.tuple_sha256(value)
    return qc_receipts.record_verdict(project, value)


@pytest.fixture
def gate_world(tmp_path: Path, monkeypatch) -> dict[str, object]:
    external_gates = tmp_path / "external-gates"
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(external_gates))
    projects = tmp_path / "projects"
    project = projects / PROJECT
    project.mkdir(parents=True)
    wayfinder = tmp_path / "wayfinder-project"
    wayfinder.mkdir()
    ticket = write_ticket(wayfinder, character_look())

    (project / "project.yaml").write_text(
        f"version: '1.2'\npipeline: authored-film\nwayfinder_root: {wayfinder}\n",
        encoding="utf-8",
    )
    _write_json(project / "project.json", {
        "project_id": PROJECT,
        "title": PROJECT,
        "pipeline_type": "authored-film",
        "created_at": "2026-08-30T00:00:00Z",
    })
    prior_migration = pin_project(project, "1.3")
    pipeline_tuple = pinned_pipeline(project, "authored-film").to_dict()

    refs = {
        "hero": _write_image(project, "hero", role="hero"),
        "turnaround": _write_image(project, "turnaround", role="turnaround"),
        "expressions": _write_image(project, "expressions", role="expressions"),
        "override": _write_image(project, "override", role="expressions"),
        "location": _write_image(project, "location", role="establishing"),
        "angle": _write_image(project, "angle", role="angle"),
        "angle_b": _write_image(project, "angle-b", role="angle"),
        "key_art": _write_image(project, "key-art", role="key_art"),
        "title_card": _write_image(project, "title-card", role="title_card"),
        "poster_final": _write_image(project, "poster-final", role="poster_final"),
        "story_a": _write_image(project, "story-a", role="storyboard_frame"),
        "story_b": _write_image(project, "story-b", role="storyboard_frame"),
    }
    prompt_recipe = {
        "look_hash": "c" * 64,
        "builder_version": "test-builder/1",
        "fields_used": ["prompt_safe_description", "hair"],
        "rendered_sha256": hashlib.sha256(b"invented structured prompt").hexdigest(),
    }
    look_ref = {
        "entity_kind": "character", "entity_id": CHAR, "look_hash": "c" * 64,
        "receipt_id": "look-receipt-current",
    }
    candidate_a = _write_image(project, "candidate-a", role="hero")
    candidate_b = _write_image(project, "candidate-b", role="hero", relative=".headshot-staging/candidate-b.png")
    candidate_c = _write_image(project, "candidate-c", role="hero", relative=".import-staging/candidate-c.png")
    candidate_b["provenance"] = {
        "generator_kind": "local", "tool": "test-local-tool", "tool_version": "1",
        "parameters_hash": "1" * 64, "input_asset_ids": [refs["hero"]["asset_id"]],
        "generation_receipt_id": "generation-candidate-b",
    }
    candidate_c["provenance"] = {
        "generator_kind": "imported", "origin_tool": "test-origin-tool",
        "attestation_receipt_id": "reference-import-receipt",
        "import_receipt_id": "generation-candidate-c",
        "normalized_pixel_hash": candidate_c["asset_id"],
        "generation_receipt_id": "generation-candidate-c",
    }
    candidates = [
        candidate_a, candidate_b, candidate_c,
    ]
    imported = _write_image(project, "normalized-import", role="hero")
    imported_path = project / imported["path"]
    staged_import = project / STAGING_DIR / f"reference-{imported['asset_id']}.png"
    staged_import.parent.mkdir(parents=True, exist_ok=True)
    staged_import.write_bytes(imported_path.read_bytes())

    draft_entry = {
            "id": CHAR,
            "status": "draft",
            "sheet_revision": 2,
            "hero": refs["hero"],
            "sheet": {"turnaround": refs["turnaround"], "expressions": refs["expressions"]},
            "qc_receipts": {},
            "wardrobe_negative": "no hat",
            "prompt_recipe": prompt_recipe,
            "look_ref": look_ref,
    }
    canon_entry = dict(draft_entry, id=CANON_CHAR, status="approved", approval_receipt_id="canon-sheet-receipt",
                       qc_receipts={}, look_ref=dict(look_ref, entity_id=CANON_CHAR))
    bible = {
        "version": "1.1",
        "project_slug": PROJECT,
        "palette": {"hues": ["#112233", "#445566", "#778899"]},
        "generator_defaults": {"image_model": "test/image-model", "edit_model": "test/edit-model"},
        "characters": [draft_entry, canon_entry],
        "locations": [{
            "id": LOC_ID,
            "status": "draft",
            "sheet_revision": 1,
            "look_ref": {"entity_kind": "location", "entity_id": LOC_ID, "look_hash": "2" * 64,
                         "receipt_id": "look-receipt-location"},
            "establishing": refs["location"],
            "angles": [refs["angle"], refs["angle_b"]],
        }],
        "poster": {
            "status": "approved",
            "approval_receipt_id": "poster-approval-receipt",
            "key_art": refs["key_art"],
            "title_card": refs["title_card"],
            "poster_final": refs["poster_final"],
        },
    }
    qc_decoy_turnaround = _qc_verdict(
        project, receipt_role="turnaround", asset_id=refs["story_a"]["asset_id"], attempt="decoy-turnaround",
    )
    qc_decoy_expressions = _qc_verdict(
        project, receipt_role="expressions", asset_id=refs["story_b"]["asset_id"], attempt="decoy-expressions",
    )
    qc_turnaround = _qc_verdict(
        project, receipt_role="turnaround", asset_id=refs["turnaround"]["asset_id"], attempt="target-turnaround",
    )
    qc_expressions = _qc_verdict(
        project, receipt_role="expressions", asset_id=refs["expressions"]["asset_id"], attempt="target-expressions",
    )
    qc_override = _qc_verdict(
        project, receipt_role="expressions", asset_id=refs["override"]["asset_id"],
        attempt="target-override", verdict="fail", failing_item="grid_2x3",
    )
    qc_rows = [qc_decoy_turnaround, qc_decoy_expressions, qc_turnaround, qc_expressions, qc_override]
    draft_entry["qc_receipts"] = {
        "turnaround": qc_turnaround["receipt_id"],
        "expressions": qc_expressions["receipt_id"],
    }
    _write_json(project / "checkpoint_visual_bible.json", {
        "version": "1.0",
        "project_id": PROJECT,
        "pipeline_type": "authored-film",
        "pipeline": pipeline_tuple,
        "predecessors": [],
        "stage": "visual_bible",
        "status": "awaiting_human",
        "timestamp": "2026-08-30T00:00:00Z",
        "artifacts": {"visual_bible": bible},
    })
    _write_json(project / "checkpoint_headshots.json", {
        "version": "1.0",
        "project_id": PROJECT,
        "pipeline_type": "authored-film",
        "pipeline": pipeline_tuple,
        "predecessors": [],
        "stage": "headshots",
        "status": "awaiting_human",
        "timestamp": "2026-08-30T00:00:00Z",
        "artifacts": {"headshot_packet": {"version": "1.0", "state": "pending", "characters": [{
            "entity_kind": "character",
            "entity_id": CHAR,
            "look_ref": look_ref,
            "prompt_recipe": prompt_recipe,
            "candidates": candidates,
            "rejection_notes": ["invented first rejection", "invented second rejection"],
        }]}},
    })
    _write_json(project / "checkpoint_assets.json", {
        "project_id": PROJECT,
        "pipeline_type": "authored-film",
        "stage": "assets",
        "status": "awaiting_human",
        "timestamp": "2026-08-30T00:00:00Z",
        "artifacts": {"asset_manifest": {"assets": [
            {"id": "frame-a", "asset_class": "storyboard_frame", "shot_id": "shot-a", **refs["story_a"]},
            {"id": "frame-b", "asset_class": "storyboard_frame", "shot_id": "shot-b", **refs["story_b"]},
            {"id": "audio-a", "asset_class": "audio", "asset_id": "f" * 64},
        ]}},
    })
    _write_json(project / "checkpoint_look_lock.json", {
        "project_id": PROJECT,
        "pipeline_type": "authored-film",
        "stage": "look_lock",
        "status": "completed",
        "timestamp": "2026-08-30T00:00:00Z",
        "artifacts": {"look_packet": {"looks": [{
            "entity_id": CHAR,
            "entity_kind": "character",
            "look_hash": "c" * 64,
            "source_ticket_ref": {"id": "wf-0badc0de"},
        }]}},
    })
    qc_ledger = project / "qc-receipts.jsonl"
    qc_lines = qc_ledger.read_bytes().splitlines(keepends=True)
    assert len(qc_lines) == len(qc_rows)
    qc_ledger.write_bytes(b"".join(qc_lines[:2]) + b"{mid-write\n" + b"".join(qc_lines[2:]))

    approval_row = _approve(
        project, "visual_bible", f"character:{CHAR}", {"sheet_revision": 1}, "sheet", CHAR, None,
    )
    _write_json(project / "cost_log.json", {"entries": [
        {"status": "completed", "actual_usd": 1.25},
        {"status": "failed", "actual_usd": 0.5},
        {"status": "reserved", "reserved_usd": 0.75},
        {"status": "pending", "actual_usd": 99},
    ]})
    _write_json(project / ".run-lease", {
        "pid": os.getpid(),
        "hostname": __import__("socket").gethostname(),
        "process_start_time": run_lease.process_start_time(os.getpid()),
        "acquired_at": "2026-08-30T00:00:00+00:00",
    })
    wal_output = project / refs["story_a"]["path"]
    receipts.stage_generation_wal(
        project,
        execution_id="state-must-not-replay",
        outputs=[{"staging_path": None, "output_path": wal_output,
                  "output_sha256": refs["story_a"]["asset_id"]}],
        receipt={
            "tool": "test-image-tool",
            "generator_kind": "model",
            "model_endpoint": "test/image-model",
            "normalized_inputs_hash": "3" * 64,
            "cost_usd": 0.01,
            "started_at": "2026-08-30T00:00:00+00:00",
        },
    )

    rids = {
        "headshot": "headshot-char-01-deadbeef-1",
        "sheet": "sheet-char-01-deadbeef-2",
        "done": "sheet-char-01-deadbeef-1",
        "declined": "qc-char-01-deadbeef-1",
        "abandoned": "sheet-char-01-deadbeef-3",
        "conflict": "sheet-char-01-deadbeef-4",
        "qc_override": "qc-override-char-01-deadbeef-1",
        "reference_import": "reference-char-01-deadbeef-1",
        "look_lock": "look-char-01-deadbeef-1",
        "config": "config-p-1",
        "pipeline_migration": "pipeline-p-1",
        "hero": "hero-char-01-deadbeef-1",
        "location": "location-loc-01-aabbccdd-1",
        "poster": "poster-p-1",
        "storyboard_batch": "storyboard-p-1",
        "headshot_grandfather": "grandfather-char-01-deadbeef-1",
        "artifact_review": "artifact-p-1",
    }
    headshot_digest = hashlib.sha256((project / "checkpoint_headshots.json").read_bytes()).hexdigest()
    sheet_digest = hashlib.sha256((project / "checkpoint_visual_bible.json").read_bytes()).hexdigest()
    sheet_record = character_approval_record(draft_entry, bible["palette"])
    import_record_hint = import_record(
        ORIGIN_IMPORTED_SYNTHETIC, imported["asset_id"], origin_tool="test-origin-tool", entity_id=CHAR,
    )
    migration_record_hint = migration_record(
        "authored-film", "1.4", manifest_digest("authored-film@1.4"), checkpoint_digests_on_disk(project),
    )

    _request(project, "pending", rids["headshot"], "headshot", stage="headshots", artifact=None,
             approval_record=None, source_checkpoint_digest=headshot_digest, preview_paths=[])
    _request(project, "pending", rids["sheet"], "sheet", artifact=None, approval_record=sheet_record,
             source_checkpoint_digest=sheet_digest, preview_paths=["/outside/must-not-be-used.png"])
    _request(project, "done", rids["done"], "sheet", approval_record={"sheet_revision": 1},
             source_checkpoint_digest="4" * 64, approval_receipt_id=approval_row["receipt_id"],
             summary="Archived sheet request.", preview_paths=[])
    _request(project, "declined", rids["declined"], "qc_override", declined_note="Invented decline note.")
    _request(project, "abandoned", rids["abandoned"], "sheet", approval_record={"sheet_revision": 3},
             source_checkpoint_digest="5" * 64, summary="Abandoned sheet request.")
    duplicate = _request(project, "pending", rids["conflict"], "sheet",
                         approval_record={"sheet_revision": 4}, source_checkpoint_digest="6" * 64)
    _request(project, "done", rids["conflict"], "sheet", approval_record={"sheet_revision": 4},
             source_checkpoint_digest="6" * 64, summary=duplicate["summary"])
    _request(project, "pending", rids["qc_override"], "qc_override",
             qc_receipt_id=qc_override["receipt_id"], item_ids=["grid_2x3"], reason="",
             preview_paths=[f"canon/visual/objects/{refs['override']['asset_id']}.png"])
    _request(project, "pending", rids["reference_import"], "reference_import", stage="look_lock",
             scope=f"reference:{imported['asset_id']}", entity_id=f"reference-{imported['asset_id'][:12]}",
             artifact=None, approval_record=import_record_hint,
             envelope={"normalized_pixel_hash": imported["asset_id"], "origin_class": ORIGIN_IMPORTED_SYNTHETIC},
             source_checkpoint_digest="7" * 64,
             preview_paths=[f".import-staging/reference-{imported['asset_id']}.png"])
    _request(project, "pending", rids["look_lock"], "look_lock", stage="look_lock",
             source_ticket_path=str(ticket), envelope={"action": "activate", "look_hash": "d" * 64,
                                                      "source_ticket_ref": {"id": "wf-deadbeef"}})
    _request(project, "pending", rids["config"], "config", stage="config", entity_id="project-config",
             scope="project:config", summary="Review project configuration.")
    _request(project, "pending", rids["pipeline_migration"], "pipeline_migration", stage="pipeline",
             entity_id="authored-film", scope="pipeline:authored-film", artifact=None,
             pipeline_version="1.4", approval_record=migration_record_hint,
             envelope={"supersedes_receipt_id": prior_migration["receipt_id"]},
             source_checkpoint_digest=None, preview_paths=[])
    _request(project, "pending", rids["hero"], "hero")
    _request(project, "pending", rids["location"], "location", entity_id=LOC_ID, scope=f"location:{LOC_ID}")
    _request(project, "pending", rids["poster"], "poster", entity_id=POSTER_ID, scope="project:poster")
    _request(project, "pending", rids["storyboard_batch"], "storyboard_batch", stage="assets",
             entity_id="storyboard-batch", scope="project:storyboard")
    _request(project, "pending", rids["headshot_grandfather"], "headshot_grandfather",
             preview_paths=["/private/evidence/hero.png"], envelope={"secret": "excluded"},
             approval_record={"secret": "excluded"}, qc_receipt_id="qc-grandfather")
    _request(project, "pending", rids["artifact_review"], "artifact_review",
             preview_paths=["drafts/review-a.png", "/private/review-b.png"],
             envelope={"secret": "excluded"}, approval_record={"secret": "excluded"},
             artifact={"artifact_type": "script"})
    (project / ".gate-requests" / "nonregular.json").mkdir()

    return {
        "projects": projects,
        "project": project,
        "wayfinder": wayfinder,
        "ticket": ticket,
        "refs": refs,
        "candidates": candidates,
        "imported": imported,
        "external_gates": external_gates,
        "rids": rids,
        "approval_row": approval_row,
        "prior_migration": prior_migration,
        "qc_rows": qc_rows,
        "qc_targets": {"turnaround": qc_turnaround, "expressions": qc_expressions, "override": qc_override},
    }


def _row(state: dict, request_id: str) -> dict:
    return next(row for row in state["gates"]["requests"] if row["request_id"] == request_id)


def _fingerprint(root: Path) -> dict[str, tuple[str, int, str]]:
    result: dict[str, tuple[str, int, str]] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        kind = "link" if stat.S_ISLNK(info.st_mode) else "dir" if stat.S_ISDIR(info.st_mode) else "file"
        if kind == "file":
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        elif kind == "link":
            content = hashlib.sha256(os.readlink(path).encode()).hexdigest()
        else:
            content = hashlib.sha256(b"").hexdigest()
        result[path.relative_to(root).as_posix()] = (kind, stat.S_IMODE(info.st_mode), content)
    return result


def _minimal_governed(base: Path) -> Path:
    project = base / PROJECT
    project.mkdir(parents=True)
    (project / "project.yaml").write_text("pipeline: authored-film\n", encoding="utf-8")
    (project / ".gate-requests").mkdir()
    return project


def _public_request(request_id: str, summary: object = "ok") -> dict[str, object]:
    return {
        "request_id": request_id,
        "project_id": PROJECT,
        "kind": "config",
        "stage": "p",
        "scope": "p",
        "entity_id": "p",
        "summary": summary,
    }


def _request_bytes_of_size(request_id: str, target: int) -> bytes:
    empty = json.dumps(_public_request(request_id, ""), separators=(",", ":"), sort_keys=True).encode()
    assert target >= len(empty)
    raw = json.dumps(
        _public_request(request_id, "x" * (target - len(empty))),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert len(raw) == target
    return raw


def test_gate_summary_rows_are_exact_unique_and_pending_only_actionable(gate_world):
    project = gate_world["project"]
    rids = gate_world["rids"]
    state = load_board_state(project)
    gates = state["gates"]

    assert set(gates) == {
        "requests", "truncated", "total_requests", "canon", "looks", "cost", "run_lease",
    }
    assert gates["truncated"] is False
    assert gates["total_requests"] == len(gates["requests"])
    headshot = _row(state, rids["headshot"])
    assert headshot == {
        "request_id": rids["headshot"],
        "kind": "headshot",
        "stage": "headshots",
        "scope": f"character:{CHAR}",
        "entity_id": CHAR,
        "summary": f"Review headshot for {CHAR}.",
        "state": "pending",
        "mtime": FIXED_MTIME,
        "next_command": (
            f"cd {shlex.quote(str(run_common.REPO))} && "
            f"{shlex.join([str(run_common.REPO / '.venv/bin/python'), 'scripts/gate_sign.py', '--project', PROJECT, '--request', rids['headshot']])}"
        ),
    }
    def pending(request_id: str, kind: str, stage: str, scope: str, entity_id: str,
                summary: str | None = None) -> dict:
        return {
            "request_id": request_id,
            "kind": kind,
            "stage": stage,
            "scope": scope,
            "entity_id": entity_id,
            "summary": summary or f"Review {kind} for {CHAR}.",
            "state": "pending",
            "mtime": FIXED_MTIME,
            "next_command": (
                f"cd {shlex.quote(str(run_common.REPO))} && "
                f"{shlex.join([str(run_common.REPO / '.venv/bin/python'), 'scripts/gate_sign.py', '--project', PROJECT, '--request', request_id])}"
            ),
        }

    expected = [
        pending(rids["artifact_review"], "artifact_review", "visual_bible", f"character:{CHAR}", CHAR),
        pending(rids["config"], "config", "config", "project:config", "project-config",
                "Review project configuration."),
        pending(rids["headshot_grandfather"], "headshot_grandfather", "visual_bible",
                f"character:{CHAR}", CHAR),
        pending(rids["headshot"], "headshot", "headshots", f"character:{CHAR}", CHAR),
        pending(rids["hero"], "hero", "visual_bible", f"character:{CHAR}", CHAR),
        pending(rids["location"], "location", "visual_bible", f"location:{LOC_ID}", LOC_ID),
        pending(rids["look_lock"], "look_lock", "look_lock", f"character:{CHAR}", CHAR),
        pending(rids["pipeline_migration"], "pipeline_migration", "pipeline", "pipeline:authored-film",
                "authored-film"),
        pending(rids["poster"], "poster", "visual_bible", "project:poster", POSTER_ID),
        {
            "request_id": rids["declined"], "kind": "qc_override", "stage": "visual_bible",
            "scope": f"character:{CHAR}", "entity_id": CHAR, "summary": f"Review qc_override for {CHAR}.",
            "state": "declined", "mtime": FIXED_MTIME, "declined_note": "Invented decline note.",
        },
        pending(rids["qc_override"], "qc_override", "visual_bible", f"character:{CHAR}", CHAR),
        pending(rids["reference_import"], "reference_import", "look_lock",
                f"reference:{gate_world['imported']['asset_id']}",
                f"reference-{gate_world['imported']['asset_id'][:12]}"),
        {
            "request_id": rids["done"], "kind": "sheet", "stage": "visual_bible",
            "scope": f"character:{CHAR}", "entity_id": CHAR, "summary": "Archived sheet request.",
            "state": "done", "mtime": FIXED_MTIME,
            "approval_receipt_id": gate_world["approval_row"]["receipt_id"],
        },
        pending(rids["sheet"], "sheet", "visual_bible", f"character:{CHAR}", CHAR),
        {
            "request_id": rids["abandoned"], "kind": "sheet", "stage": "visual_bible",
            "scope": f"character:{CHAR}", "entity_id": CHAR, "summary": "Abandoned sheet request.",
            "state": "abandoned", "mtime": FIXED_MTIME,
        },
        {
            "request_id": rids["conflict"], "kind": "sheet", "stage": "visual_bible",
            "scope": f"character:{CHAR}", "entity_id": CHAR, "summary": f"Review sheet for {CHAR}.",
            "state": "conflict", "mtime": FIXED_MTIME,
        },
        pending(rids["storyboard_batch"], "storyboard_batch", "assets", "project:storyboard",
                "storyboard-batch"),
    ]
    priority = {"pending": 0, "conflict": 1, "done": 2, "declined": 3, "abandoned": 4}
    assert gates["requests"] == sorted(expected, key=lambda row: (priority[row["state"]], row["request_id"]))


def test_summary_validates_request_identity_and_bounds_output(gate_world):
    project = gate_world["project"]
    base = project / ".gate-requests"
    _write_json(base / "bad id.json", {"request_id": "bad id", "project_id": PROJECT})
    _write_json(base / "valid-stem.json", {"request_id": "another-id", "project_id": PROJECT})
    _write_json(base / "wrong-project.json", {"request_id": "wrong-project", "project_id": "other-project"})
    (base / "mid-write.json").write_text('{"request_id":"mid-write",', encoding="utf-8")
    state = load_board_state(project)
    ids = {row["request_id"] for row in state["gates"]["requests"]}
    assert not {"bad id", "valid-stem", "wrong-project", "mid-write"} & ids
    detail = load_gate_detail(project, "mid-write")
    assert detail["state"] == "error" and detail["error"] == "request unavailable"
    assert detail["packet_error"] is True
    assert len(json.dumps(detail)) < 512

    for number in range(205):
        _request(project, "pending", f"bounded-{number:03d}", "config", entity_id="project-config")
    assert len(load_board_state(project)["gates"]["requests"]) == 200


def test_summary_prioritizes_pending_requests_when_archive_exceeds_row_cap(tmp_path):
    project = _minimal_governed(tmp_path)
    for number in range(200):
        _request(project, "done", f"archived-{number:03d}", "config", entity_id="project-config")
    _request(project, "pending", "z-pending", "config", entity_id="project-config")

    gates = load_board_state(project)["gates"]

    assert len(gates["requests"]) == 200
    assert gates["requests"][0]["request_id"] == "z-pending"
    assert gates["requests"][0]["state"] == "pending"
    assert gates["truncated"] is True
    assert gates["total_requests"] == 201


def test_gate_request_per_file_and_aggregate_byte_boundaries(tmp_path, monkeypatch):
    per_file = _minimal_governed(tmp_path / "per-file")
    exact = _request_bytes_of_size("r1", 256)
    (per_file / ".gate-requests" / "r1.json").write_bytes(exact)
    monkeypatch.setattr(state_mod, "_MAX_GATE_REQUEST_BYTES", len(exact))
    monkeypatch.setattr(state_mod, "_MAX_GATE_REQUEST_TOTAL_BYTES", len(exact) + 1)
    assert [row["request_id"] for row in load_board_state(per_file)["gates"]["requests"]] == ["r1"]
    monkeypatch.setattr(state_mod, "_MAX_GATE_REQUEST_BYTES", len(exact) - 1)
    assert load_board_state(per_file)["gates"] == {
        "error": "gate request exceeds byte budget", "requests": [],
    }

    aggregate = _minimal_governed(tmp_path / "aggregate")
    first = _request_bytes_of_size("r1", 192)
    second = _request_bytes_of_size("r2", 208)
    (aggregate / ".gate-requests" / "r1.json").write_bytes(first)
    (aggregate / ".gate-requests" / "r2.json").write_bytes(second)
    monkeypatch.setattr(state_mod, "_MAX_GATE_REQUEST_BYTES", 512)
    monkeypatch.setattr(state_mod, "_MAX_GATE_REQUEST_TOTAL_BYTES", len(first) + len(second))
    assert [row["request_id"] for row in load_board_state(aggregate)["gates"]["requests"]] == ["r1", "r2"]
    monkeypatch.setattr(state_mod, "_MAX_GATE_REQUEST_TOTAL_BYTES", len(first) + len(second) - 1)
    assert load_board_state(aggregate)["gates"] == {
        "error": "gate request aggregate byte budget exceeded", "requests": [],
    }


def test_gate_public_string_list_and_list_item_boundaries(tmp_path, monkeypatch):
    project = _minimal_governed(tmp_path)
    request_path = project / ".gate-requests" / "r1.json"
    monkeypatch.setattr(state_mod, "_MAX_GATE_PUBLIC_STRING_CHARS", 8)
    monkeypatch.setattr(state_mod, "_MAX_GATE_PUBLIC_LIST_ITEMS", 2)

    _write_json(request_path, _public_request("r1", "12345678"))
    assert _row(load_board_state(project), "r1")["summary"] == "12345678"
    _write_json(request_path, _public_request("r1", "123456789"))
    assert load_board_state(project)["gates"] == {
        "error": "gate request public string exceeds budget", "requests": [],
    }

    _write_json(request_path, _public_request("r1", ["a", "b"]))
    assert _row(load_board_state(project), "r1")["summary"] == ["a", "b"]
    _write_json(request_path, _public_request("r1", ["a", "b", "c"]))
    assert load_board_state(project)["gates"] == {
        "error": "gate request public list exceeds budget", "requests": [],
    }
    _write_json(request_path, _public_request("r1", ["123456789"]))
    assert load_board_state(project)["gates"] == {
        "error": "gate request public list is invalid", "requests": [],
    }


def test_gate_tree_depth_entry_and_work_boundaries(tmp_path, monkeypatch):
    depth = _minimal_governed(tmp_path / "depth")
    nested = depth / ".gate-requests" / "a"
    nested.mkdir()
    monkeypatch.setattr(state_mod, "_MAX_GATE_TREE_DEPTH", 1)
    assert load_board_state(depth)["gates"].get("error") is None
    (nested / "b").mkdir()
    assert load_board_state(depth)["gates"] == {
        "error": "gate directory scan budget exceeded", "requests": [],
    }

    entries = _minimal_governed(tmp_path / "entries")
    _write_json(entries / ".gate-requests" / "r1.json", _public_request("r1"))
    monkeypatch.setattr(state_mod, "_MAX_GATE_TREE_DEPTH", 12)
    monkeypatch.setattr(state_mod, "_MAX_GATE_TREE_ENTRIES", 1)
    assert load_board_state(entries)["gates"].get("error") is None
    _write_json(entries / ".gate-requests" / "r2.json", _public_request("r2"))
    assert load_board_state(entries)["gates"] == {
        "error": "gate directory scan budget exceeded", "requests": [],
    }

    work = _minimal_governed(tmp_path / "work")
    _write_json(work / ".gate-requests" / "r1.json", _public_request("r1"))
    monkeypatch.setattr(state_mod, "_MAX_GATE_TREE_ENTRIES", 100)
    monkeypatch.setattr(state_mod, "_MAX_GATE_TREE_WORK", 2)
    assert load_board_state(work)["gates"].get("error") is None
    _write_json(work / ".gate-requests" / "r2.json", _public_request("r2"))
    assert load_board_state(work)["gates"] == {
        "error": "gate directory scan budget exceeded", "requests": [],
    }


def test_jsonl_line_row_count_and_total_byte_boundaries_report_packet_errors(gate_world, monkeypatch):
    project = gate_world["project"]
    request_id = gate_world["rids"]["qc_override"]
    target = gate_world["qc_targets"]["override"]
    target_line = (json.dumps(target, separators=(",", ":")) + "\n").encode()
    ledger = project / "qc-receipts.jsonl"

    ledger.write_bytes(target_line)
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LINE_BYTES", len(target_line))
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LOOKUP_BYTES", len(target_line))
    packet = load_gate_detail(project, request_id)["packet"]
    assert packet["packet_error"] is False and packet["qc_row"] == target

    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LINE_BYTES", len(target_line) - 1)
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LOOKUP_BYTES", len(target_line) + 1)
    packet = load_gate_detail(project, request_id)["packet"]
    assert packet["packet_error"] is True and packet["error"] == "ledger row exceeds byte budget"

    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LINE_BYTES", len(target_line) + 1)
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LOOKUP_BYTES", len(target_line) - 1)
    packet = load_gate_detail(project, request_id)["packet"]
    assert packet["packet_error"] is True and packet["error"] == "ledger lookup exceeds byte budget"

    decoys = [
        json.dumps({"receipt_id": f"decoy-{number}", "kind": "verdict"}, separators=(",", ":"))
        for number in range(2)
    ]
    ledger.write_text("\n".join([*decoys, json.dumps(target, separators=(",", ":"))]) + "\n", encoding="utf-8")
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LINE_BYTES", 8 * 1024)
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LOOKUP_BYTES", 64 * 1024)
    monkeypatch.setattr(state_mod, "_MAX_LEDGER_LOOKUP_ROWS", 2)
    packet = load_gate_detail(project, request_id)["packet"]
    assert packet["packet_error"] is True and packet["error"] == "ledger lookup exceeds row or byte budget"


@pytest.mark.parametrize("target", ["directory", "file"])
def test_symlink_anywhere_in_gate_tree_fails_whole_section(gate_world, tmp_path, target):
    project = gate_world["project"]
    outside = tmp_path / f"outside-{target}"
    if target == "directory":
        outside.mkdir()
        link = project / ".gate-requests" / "linked-dir"
    else:
        outside.write_text("{}", encoding="utf-8")
        link = project / ".gate-requests" / "linked.json"
    link.symlink_to(outside)
    gates = load_board_state(project)["gates"]
    assert gates == {"error": "symlink in gate directory", "requests": []}


def test_canon_cost_looks_and_lease_are_raw_projections(gate_world):
    project = gate_world["project"]
    state = load_board_state(project)
    plan = plan_view(project)
    projected = []
    for link_rel, object_rel in plan:
        parts = Path(link_rel).parts
        role = Path(parts[-1]).stem
        if role in {"hero", "turnaround", "expressions", "wardrobe", "front", "three_quarter", "profile", "full_body"}:
            projected.append({"entity": parts[-2], "role": role, "object_rel": object_rel})
    assert state["gates"]["canon"] == projected
    assert [(row["entity"], row["role"]) for row in projected] == [
        (CANON_CHAR, "expressions"), (CANON_CHAR, "hero"), (CANON_CHAR, "turnaround")
    ]
    assert state["gates"]["looks"] == [{
        "entity": CHAR,
        "entity_kind": "character",
        "look_hash": "c" * 64,
        "source_ticket_ref": {"id": "wf-0badc0de"},
    }]
    assert state["gates"]["cost"] == {
        "label": "ledger", "source": "ledger", "total_spent_usd": 1.75, "total_reserved_usd": 0.75,
    }
    assert state["gates"]["run_lease"] == {
        "held": True, "pid": os.getpid(), "started": "2026-08-30T00:00:00+00:00",
    }


def test_legacy_and_governed_non_authored_cost_behavior(tmp_path):
    legacy = tmp_path / "legacy" / PROJECT
    legacy.mkdir(parents=True)
    _write_json(legacy / "project.json", {"project_id": PROJECT, "pipeline_type": "cinematic"})
    _write_json(legacy / "checkpoint_script.json", {
        "project_id": PROJECT, "pipeline_type": "cinematic", "stage": "script", "status": "in_progress",
        "cost_snapshot": {"total_spent_usd": 2.5}, "artifacts": {},
    })
    state = load_board_state(legacy)
    assert state["gates"] is None
    assert state["cost"] == {"total_spent_usd": 2.5}

    governed = tmp_path / "governed" / PROJECT
    governed.mkdir(parents=True)
    (governed / "project.yaml").write_text("pipeline: cinematic\n", encoding="utf-8")
    _write_json(governed / "checkpoint_script.json", {
        "project_id": PROJECT, "pipeline_type": "cinematic", "stage": "script", "status": "in_progress",
        "cost_snapshot": {"total_spent_usd": 2.5}, "artifacts": {},
    })
    assert load_board_state(governed)["gates"]["cost"] == {
        "total_spent_usd": 2.5, "label": "snapshot", "source": "snapshot",
    }


def test_renderer_map_and_all_pending_packet_envelopes(gate_world):
    assert set(GATE_RENDERERS) == set(APPROVAL_KINDS)
    assert len(GATE_RENDERERS) == 13
    for kind in APPROVAL_KINDS:
        detail = load_gate_detail(gate_world["project"], gate_world["rids"][kind])
        assert detail["state"] == "pending"
        assert isinstance(detail["snapshot_at"], float)
        assert detail["record_sha256"] is None
        assert detail["request_id"] == gate_world["rids"][kind]
        assert "packet" in detail


def test_central_headshot_and_sheet_artifacts_are_schema_and_publisher_real(gate_world):
    from lib.checkpoint import validate_checkpoint
    from lib.pipeline_pin import pinned_pipeline
    from schemas.artifacts import validate_artifact
    from scripts.gate_approve import _load_pending_checkpoint

    project = gate_world["project"]
    headshot_checkpoint = json.loads((project / "checkpoint_headshots.json").read_text(encoding="utf-8"))
    visual_checkpoint = json.loads((project / "checkpoint_visual_bible.json").read_text(encoding="utf-8"))
    validate_checkpoint(headshot_checkpoint)
    validate_checkpoint(visual_checkpoint)
    signed_tuple = pinned_pipeline(project, "authored-film").to_dict()
    assert headshot_checkpoint["pipeline"] == signed_tuple
    assert visual_checkpoint["pipeline"] == signed_tuple
    assert headshot_checkpoint["predecessors"] == []
    assert visual_checkpoint["predecessors"] == []
    validate_artifact("headshot_packet", headshot_checkpoint["artifacts"]["headshot_packet"])
    validate_artifact("visual_bible", visual_checkpoint["artifacts"]["visual_bible"])

    headshot_request = json.loads(
        (project / ".gate-requests" / f"{gate_world['rids']['headshot']}.json").read_text(encoding="utf-8")
    )
    assert _load_pending_checkpoint(project, headshot_request, "headshots")[1] == headshot_checkpoint
    assert headshot_request["artifact"] is None and headshot_request["approval_record"] is None
    assert headshot_request["source_checkpoint_digest"] == hashlib.sha256(
        (project / "checkpoint_headshots.json").read_bytes()
    ).hexdigest()

    sheet_request = json.loads(
        (project / ".gate-requests" / f"{gate_world['rids']['sheet']}.json").read_text(encoding="utf-8")
    )
    assert _load_pending_checkpoint(project, sheet_request)[1] == visual_checkpoint
    draft = visual_checkpoint["artifacts"]["visual_bible"]["characters"][0]
    assert draft["id"] == CHAR and draft["status"] == "draft" and draft["sheet_revision"] == 2
    assert sheet_request["approval_record"] == character_approval_record(
        draft, visual_checkpoint["artifacts"]["visual_bible"]["palette"],
    )
    assert sheet_request["source_checkpoint_digest"] == hashlib.sha256(
        (project / "checkpoint_visual_bible.json").read_bytes()
    ).hexdigest()


def test_headshot_packet_order_metadata_hash_and_checkpoint_errors(gate_world):
    project = gate_world["project"]
    detail = load_gate_detail(project, gate_world["rids"]["headshot"])
    packet = detail["packet"]
    assert packet["packet_error"] is False
    assert packet["rejected_count"] == 2
    assert [row["number"] for row in packet["candidates"]] == [1, 2, 3]
    assert [row["generator_kind"] for row in packet["candidates"]] == ["model", "local", "imported"]
    assert [row["sha_prefix"] for row in packet["candidates"]] == [
        candidate["asset_id"][:12] for candidate in gate_world["candidates"]
    ]
    assert [row["visual"]["path"] for row in packet["candidates"]] == [
        candidate["path"] for candidate in gate_world["candidates"]
    ]
    assert all(row["visual"]["sha256"] == candidate["asset_id"]
               for row, candidate in zip(packet["candidates"], gate_world["candidates"]))

    candidate_path = project / gate_world["candidates"][1]["path"]
    candidate_path.write_bytes(candidate_path.read_bytes() + b"changed")
    bad = load_gate_detail(project, gate_world["rids"]["headshot"])["packet"]
    assert bad["packet_error"] is True
    assert bad["candidates"][1]["visual"]["error"] == "hash mismatch"

    (project / "checkpoint_headshots.json").write_text('{"artifacts":', encoding="utf-8")
    truncated = load_gate_detail(project, gate_world["rids"]["headshot"])["packet"]
    assert truncated == {
        "packet_error": True,
        "error": "evidence JSON unreadable or malformed",
        "candidates": [],
    }


def test_sheet_packet_uses_matching_draft_objects_and_unverified_qc_rows(gate_world):
    ledger = (gate_world["project"] / "qc-receipts.jsonl").read_bytes()
    assert ledger.index(b"{mid-write\n") < ledger.index(
        str(gate_world["qc_targets"]["expressions"]["receipt_id"]).encode()
    )
    detail = load_gate_detail(gate_world["project"], gate_world["rids"]["sheet"])
    packet = detail["packet"]
    assert packet["packet_error"] is False
    assert [(visual["label"], visual["asset_id"], visual.get("error")) for visual in packet["visuals"]] == [
        ("expressions", gate_world["refs"]["expressions"]["asset_id"], None),
        ("turnaround", gate_world["refs"]["turnaround"]["asset_id"], None),
    ]
    assert packet["qc_rows"] == [
        {"role": "expressions", "label": "unverified QC row", "row": gate_world["qc_targets"]["expressions"]},
        {"role": "turnaround", "label": "unverified QC row", "row": gate_world["qc_targets"]["turnaround"]},
    ]
    assert packet["qc_rows"][0]["row"] != gate_world["qc_rows"][1]
    assert packet["qc_rows"][1]["row"] != gate_world["qc_rows"][0]
    assert all("must-not-be-used" not in visual["path"] for visual in packet["visuals"])


@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_sheet_packet_fails_closed_when_a_cited_qc_receipt_is_unavailable(gate_world, damage):
    project = gate_world["project"]
    target = gate_world["qc_targets"]["expressions"]
    rows = []
    for raw in (project / "qc-receipts.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            rows.append(raw)
            continue
        if row.get("receipt_id") != target["receipt_id"]:
            rows.append(raw)
        elif damage == "malformed":
            rows.append(json.dumps({"receipt_id": target["receipt_id"], "role": "expressions"}))
    (project / "qc-receipts.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    packet = load_gate_detail(project, gate_world["rids"]["sheet"])["packet"]

    assert packet["packet_error"] is True
    assert "cited QC" in packet["error"]


def test_qc_override_and_reference_import_follow_raw_receipt_and_hash(gate_world):
    qc = load_gate_detail(gate_world["project"], gate_world["rids"]["qc_override"])["packet"]
    assert qc["qc_row"] == gate_world["qc_targets"]["override"]
    assert qc["visual"]["asset_id"] == gate_world["refs"]["override"]["asset_id"]
    assert qc["item_ids"] == ["grid_2x3"]
    assert "10" in qc["reason_reminder"] and "typed reason" in qc["reason_reminder"]
    assert qc["packet_error"] is False

    imported = load_gate_detail(gate_world["project"], gate_world["rids"]["reference_import"])["packet"]
    assert imported == {
        "origin_class": ORIGIN_IMPORTED_SYNTHETIC,
        "visual": {
            "asset_id": gate_world["imported"]["asset_id"],
            "path": f"{STAGING_DIR.as_posix()}/reference-{gate_world['imported']['asset_id']}.png",
            "label": "normalized import",
            "sha256": gate_world["imported"]["asset_id"],
        },
        "packet_error": False,
    }


def test_reference_import_packet_mirrors_publisher_and_ignores_conflicting_top_level_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(tmp_path / "gates"))
    project = _minimal_governed(tmp_path / "projects")
    source = tmp_path / "invented-reference.png"
    source.write_bytes(png_bytes("publisher-real-reference"))
    staged_source = stage_reference_upload(project, source)
    normalized = normalize_reference_import(
        project,
        staged_source,
        origin_class=ORIGIN_IMPORTED_SYNTHETIC,
        origin_tool="invented-image-tool",
        entity_id=CHAR,
    )
    request_path = publish_import_request(project, PROJECT, normalized, request_id="reference-publisher-real")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["normalized_pixel_hash"] = "0" * 64
    request["origin_class"] = ORIGIN_CASTING
    _write_json(request_path, request)

    packet = load_gate_detail(project, "reference-publisher-real")["packet"]

    assert packet["origin_class"] == ORIGIN_IMPORTED_SYNTHETIC
    assert packet["visual"]["asset_id"] == normalized.normalized_pixel_hash
    assert packet["visual"]["path"] == f"{STAGING_DIR.as_posix()}/reference-{normalized.normalized_pixel_hash}.png"
    assert packet["visual"]["sha256"] == normalized.normalized_pixel_hash
    assert packet["packet_error"] is False


def test_headshot_retire_packet_shows_current_approved_hero_without_candidates(gate_world):
    from lib.headshots import headshot_record
    from scripts import gate_approve

    project = gate_world["project"]
    hero = gate_world["refs"]["hero"]
    record = headshot_record(
        entity_id=CHAR,
        look_hash="c" * 64,
        asset_id=hero["asset_id"],
        origin="generated",
        import_receipt_id=None,
        prompt_recipe_sha256=None,
        candidates_checkpoint_digest="d" * 64,
    )
    receipt = _approve(
        project,
        "headshots",
        f"character:{CHAR}",
        record,
        "headshot",
        CHAR,
        {"action": "activate", "entity_kind": "character", "look_hash": "c" * 64,
         "supersedes_receipt_id": None},
    )
    request_id = "retire-char-01-deadbeef-1"
    request = _request(
        project,
        "pending",
        request_id,
        "headshot",
        stage="headshots",
        artifact=None,
        approval_record=None,
        envelope={"action": "retire", "entity_kind": "character", "look_hash": "c" * 64,
                  "supersedes_receipt_id": receipt["receipt_id"]},
        preview_paths=[f"canon/visual/objects/{hero['asset_id']}.png"],
    )
    gate_approve.construct(project, request)

    packet = load_gate_detail(project, request_id)["packet"]

    assert packet["action"] == "retire"
    assert "candidates" not in packet
    assert packet["supersedes_receipt_id"] == receipt["receipt_id"]
    assert packet["visual"]["label"] == "current approved hero"
    assert packet["visual"]["asset_id"] == hero["asset_id"]
    assert packet["visual"]["sha256"] == hero["asset_id"]
    assert packet["packet_error"] is False


def test_lazy_gate_detail_bounds_and_malformed_shapes_fail_closed(gate_world, monkeypatch):
    project = gate_world["project"]

    monkeypatch.setattr(state_mod, "_MAX_GATE_DETAIL_JSON_BYTES", 32)
    detail = load_gate_detail(project, gate_world["rids"]["headshot"])
    assert detail["packet"]["packet_error"] is True
    assert "byte budget" in detail["packet"]["error"]
    storyboard = load_gate_detail(project, gate_world["rids"]["storyboard_batch"])
    assert storyboard["packet"]["packet_error"] is True
    assert "byte budget" in storyboard["packet"]["error"]

    monkeypatch.setattr(state_mod, "_MAX_GATE_DETAIL_JSON_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(state_mod, "_MAX_GATE_IMAGE_BYTES", 8)
    detail = load_gate_detail(project, gate_world["rids"]["headshot"])
    assert detail["packet"]["packet_error"] is True
    assert any("byte budget" in row["visual"].get("error", "") for row in detail["packet"]["candidates"])

    monkeypatch.setattr(state_mod, "_MAX_GATE_IMAGE_BYTES", 32 * 1024 * 1024)
    (project / "checkpoint_headshots.json").write_text(
        json.dumps({"artifacts": ["malformed"]}), encoding="utf-8",
    )
    detail = load_gate_detail(project, gate_world["rids"]["headshot"])
    assert detail["packet"]["packet_error"] is True
    assert isinstance(detail["packet"]["error"], str)

    monkeypatch.setattr(state_mod, "_MAX_GATE_PROJECT_YAML_BYTES", 8)
    board = load_board_state(project)
    assert board["gates"]["cost"]["error"] == "project.yaml exceeds byte budget"
    config = load_gate_detail(project, gate_world["rids"]["config"])["packet"]
    assert config["packet_error"] is True
    assert "byte budget" in config["error"]


def test_look_lock_uses_confined_wayfinder_ticket_and_exact_escape_text(gate_world, tmp_path):
    project = gate_world["project"]
    good = load_gate_detail(project, gate_world["rids"]["look_lock"])["packet"]
    assert good == {"source_ticket_ref": {"id": "wf-0badc0de"}, "look_hash": TICKET_LOOK_HASH,
                    "supersedes": "c" * 64}

    outside = tmp_path / "outside-ticket.md"
    outside.write_text("not a ticket", encoding="utf-8")
    request_path = project / ".gate-requests" / f"{gate_world['rids']['look_lock']}.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["source_ticket_path"] = str(outside)
    _write_json(request_path, request)
    escaped = load_gate_detail(project, gate_world["rids"]["look_lock"])["packet"]
    assert escaped == {"packet_error": True, "source_ticket_ref": "ticket outside wayfinder root"}


def test_config_and_pipeline_migration_are_text_packets(gate_world):
    project = gate_world["project"]
    config = load_gate_detail(project, gate_world["rids"]["config"])["packet"]
    assert config == {"project_yaml": (project / "project.yaml").read_text(encoding="utf-8"),
                      "summary": "Review project configuration."}
    migration = load_gate_detail(project, gate_world["rids"]["pipeline_migration"])["packet"]
    assert migration == {"from_version": "1.3", "to_version": "1.4"}
    assert gate_world["prior_migration"]["record"]["version"] == "1.3"


@pytest.mark.parametrize(
    ("kind", "labels", "ref_names"),
    [
        ("hero", ["hero", "expressions", "turnaround"], ["hero", "expressions", "turnaround"]),
        ("location", ["establishing", "angle_0", "angle_1"], ["location", "angle", "angle_b"]),
        ("poster", ["key_art", "title_card", "poster_final"], ["key_art", "title_card", "poster_final"]),
        ("storyboard_batch", ["shot-a", "shot-b"], ["story_a", "story_b"]),
    ],
)
def test_visual_kinds_follow_constructor_refs_and_hash_bytes(gate_world, kind, labels, ref_names):
    packet = load_gate_detail(gate_world["project"], gate_world["rids"][kind])["packet"]
    assert packet["packet_error"] is False
    assert [visual["label"] for visual in packet["visuals"]] == labels
    assert [visual["asset_id"] for visual in packet["visuals"]] == [
        gate_world["refs"][name]["asset_id"] for name in ref_names
    ]
    assert all(visual["sha256"] == visual["asset_id"] for visual in packet["visuals"])


@pytest.mark.parametrize("kind", ["headshot_grandfather", "artifact_review"])
def test_text_only_packets_exclude_record_hints_and_reduce_previews_to_filenames(gate_world, kind):
    packet = load_gate_detail(gate_world["project"], gate_world["rids"][kind])["packet"]
    assert "envelope" not in packet["request"] and "approval_record" not in packet["request"]
    assert "preview_paths" not in packet["request"]
    expected = ["hero.png"] if kind == "headshot_grandfather" else ["review-a.png", "review-b.png"]
    assert packet["preview_filenames"] == expected
    assert "visuals" not in packet


def test_archived_detail_uses_archived_marker_and_raw_matching_approval(gate_world):
    project = gate_world["project"]
    done = load_gate_detail(project, gate_world["rids"]["done"])
    assert done["state"] == "done"
    assert done["request"]["approval_record"]["sheet_revision"] == 1
    assert done["request"]["summary"] == "Archived sheet request."
    assert done["approval_receipt_id"] == gate_world["approval_row"]["receipt_id"]
    assert done["declined_note"] is None
    assert done["ledger_label"] == "unverified ledger row"
    assert done["unverified_ledger_row"] == gate_world["approval_row"]
    assert "packet" not in done

    declined = load_gate_detail(project, gate_world["rids"]["declined"])
    assert declined["state"] == "declined"
    assert declined["declined_note"] == "Invented decline note."
    abandoned = load_gate_detail(project, gate_world["rids"]["abandoned"])
    assert abandoned["state"] == "abandoned"
    assert abandoned["request"]["summary"] == "Abandoned sheet request."


def test_state_detail_and_http_reads_do_not_mutate_project_or_call_forbidden_readers(gate_world, monkeypatch):
    project = gate_world["project"]
    projects = gate_world["projects"]

    def forbidden(*args, **kwargs):
        raise AssertionError("write-capable constructor or receipt reader called")

    from lib import headshots, look_ingest
    from scripts import gate_approve

    guarded = [
        (gate_approve, "construct"),
        (gate_approve, "headshot_candidates"),
        (receipts, "verified_approvals"),
        (receipts, "find_approval"),
        (receipts, "verified_generation_receipts"),
        (receipts, "recover_pending_approvals"),
        (receipts, "recover_generation_wal"),
        (receipts, "replay_generation_entry"),
        (receipts, "chained_rows"),
        (headshots, "headshot_receipts"),
        (headshots, "active_headshots"),
        (look_ingest, "look_lock_receipts"),
        (look_ingest, "active_looks"),
        (look_ingest, "active_look_for"),
        (qc_receipts, "verified_qc_rows"),
        (qc_receipts, "find_verdict"),
        (qc_receipts, "find_verdict_by_id"),
        (qc_receipts, "chained_rows"),
    ]
    originals = [getattr(module, name) for module, name in guarded]
    for module, name in guarded:
        monkeypatch.setattr(module, name, forbidden)
    for name, value in list(vars(state_mod).items()):
        if any(value is original for original in originals):
            monkeypatch.setattr(state_mod, name, forbidden)
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", os.path.normcase(str(projects.resolve())))
    monkeypatch.setattr(server_mod, "_summary_cache", {})

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    wal_path = gate_world["external_gates"] / "generation-wal" / "state-must-not-replay.json"
    assert wal_path.is_file() and not (project / "generation-receipts.jsonl").exists()
    before_project = _fingerprint(project)
    before_external = _fingerprint(gate_world["external_gates"])
    load_board_state(project)
    for request_id in gate_world["rids"].values():
        load_gate_detail(project, request_id)
    with TestClient(server_mod.create_app()) as client:
        assert client.get(f"/api/project/{PROJECT}/state").status_code == 200
        for request_id in gate_world["rids"].values():
            expected = 409 if request_id == gate_world["rids"]["conflict"] else 200
            assert client.get(f"/api/project/{PROJECT}/gate/{request_id}").status_code == expected
    assert _fingerprint(project) == before_project
    assert _fingerprint(gate_world["external_gates"]) == before_external
    assert wal_path.is_file() and not (project / "generation-receipts.jsonl").exists()


def test_gate_detail_route_is_get_only_validated_hardened_and_project_confined(gate_world, monkeypatch, tmp_path):
    projects = gate_world["projects"]
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", os.path.normcase(str(projects.resolve())))

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)

    def assert_hardened(response, status):
        assert response.status_code == status
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["content-security-policy"]

    with TestClient(server_mod.create_app(port=8765)) as client:
        url = f"/api/project/{PROJECT}/gate/{gate_world['rids']['headshot']}"
        response = client.get(url)
        assert_hardened(response, 200)
        assert response.json()["record_sha256"] is None
        assert_hardened(client.post(url), 405)
        assert_hardened(client.get(f"/api/project/{PROJECT}/gate/bad%20id"), 400)
        assert_hardened(client.get(f"/api/project/{PROJECT}/gate/unknown-id"), 404)

        wrong = "cross-project-id"
        _write_json(gate_world["project"] / ".gate-requests" / f"{wrong}.json", {
            "request_id": wrong, "project_id": "other-project", "kind": "config",
        })
        assert_hardened(client.get(f"/api/project/{PROJECT}/gate/{wrong}"), 409)

    real = tmp_path / "real-project"
    real.mkdir()
    (real / "project.yaml").write_text("pipeline: authored-film\n", encoding="utf-8")
    symlink_projects = tmp_path / "symlink-projects"
    symlink_projects.mkdir()
    (symlink_projects / PROJECT).symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", symlink_projects)
    with TestClient(server_mod.create_app()) as client:
        assert_hardened(client.get(f"/api/project/{PROJECT}/state"), 404)
        assert_hardened(client.get(f"/api/project/{PROJECT}/gate/unknown-id"), 404)

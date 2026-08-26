"""Shared fixtures for the D10 look-lock / Slice A′ tests. Invented names only.

Every helper mints a real one-use gate token against the per-test
OPENMONTAGE_GATES_DIR and records real signed receipts, so tests exercise
the receipt chains rather than stubs of them.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.headshots import headshot_record
from lib.look_spec import look_hash as _look_hash
from lib.pipeline_loader import manifest_digest
from lib.pipeline_pin import migration_record

PROJECT = "p"
CHAR = "char-01-deadbeef"
CHAR2 = "char-02-cafef00d"
LOC = "loc-01-feedface"
IMAGE_MODEL = "fake/text-to-image"

DESCRIPTION = (
    "A wiry station engineer in her thirties with cropped ash-blond hair, a weathered grey "
    "work coat over a rust jumpsuit, and a habit of standing with one hand on any nearby rail."
)
LOC_DESCRIPTION = (
    "A salt-crusted orbital refuelling station of ribbed steel corridors, sodium-lit and humming, "
    "with frost blooming along every window seam and cargo netting slung between the pillars."
)


def character_look(cid: str = CHAR, **overrides) -> dict:
    payload = {
        "version": "1.0",
        "entity_kind": "character",
        "entity_id": cid,
        "fictional_subject_attestation": True,
        "minor": False,
        "prompt_safe_description": DESCRIPTION,
        "continuity_risks": ["hair length drifts between shots"],
        "negative_lines": ["no hat"],
        "spoiler": False,
        "depends_on": [],
        "shape_only": False,
        "age_band": "thirties",
        "build": {"kind": "lean", "note": "long limbs"},
        "hair": "cropped ash-blond",
        "distinguishing_marks": ["burn scar on left wrist"],
        "default_wardrobe": {"pieces": ["grey work coat", "rust jumpsuit"]},
        "wardrobe_variants": [{"name": "dress blues", "when": "the ceremony"}],
        "props": ["torque wrench"],
        "era_and_class_signals": "near-future working crew",
    }
    payload.update(overrides)
    return payload


def location_look(lid: str = LOC, **overrides) -> dict:
    payload = {
        "version": "1.0",
        "entity_kind": "location",
        "entity_id": lid,
        "fictional_subject_attestation": True,
        "minor": False,
        "prompt_safe_description": LOC_DESCRIPTION,
        "continuity_risks": ["frost pattern must match between angles"],
        "negative_lines": [],
        "spoiler": False,
        "depends_on": [],
        "shape_only": False,
        "establishing_view": "wide down the main corridor toward the airlock",
        "time_of_day_default": "night",
        "palette_anchors": ["sodium amber", "frost white", "gunmetal"],
        "architecture_or_terrain": "ribbed steel corridors",
        "dressing": ["cargo netting"],
        "weather_or_light_rules": "always sodium-lit, never daylight",
    }
    payload.update(overrides)
    return payload


def write_ticket(
    root: Path, payload: dict, *, ticket_id: str | None = "wf-0badc0de", resolved: bool = True,
    answer: str = "Locked as written.", extra_spec_sections: int = 0, type_: str = "grill", mode: str = "hitl",
) -> Path:
    import yaml

    folder = root / ("resolved" if resolved else "open")
    folder.mkdir(parents=True, exist_ok=True)
    front = {"type": type_, "mode": mode, "area": "look"}
    if ticket_id:
        front["id"] = ticket_id
    spec = yaml.safe_dump(payload, sort_keys=False)
    body = f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n# Look: {payload['entity_id']}\n\n## Question\nWhat does this look like?\n\n"
    if answer:
        body += f"## Answer\n{answer}\n\n"
    body += f"## Look spec\n```yaml\n{spec}```\n"
    for _ in range(extra_spec_sections):
        body += f"\n## Look spec\n```yaml\n{spec}```\n"
    path = folder / f"look-{payload['entity_kind']}-{payload['entity_id']}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _approve(project_dir: Path, stage: str, scope: str, record: dict, kind: str, entity_id: str, envelope: dict | None) -> dict:
    token = gates.mint_gate_token(PROJECT, stage, scope, record_sha256(record), user_response="approve")
    return receipts.record_human_approval(
        project_dir, PROJECT, stage, scope, record, token, kind, entity_id=entity_id, envelope=envelope,
    )


def activate_look(project_dir: Path, payload: dict, *, supersedes: str | None = None) -> dict:
    return _approve(
        project_dir, "look_lock", f"{payload['entity_kind']}:{payload['entity_id']}", payload, "look_lock",
        payload["entity_id"],
        {"action": "activate", "entity_kind": payload["entity_kind"], "look_hash": _look_hash(payload),
         "supersedes_look_hash": supersedes, "promotion_refs": [], "source_ticket_ref": None},
    )


def retire_look(project_dir: Path, payload: dict) -> dict:
    record = {"entity_kind": payload["entity_kind"], "entity_id": payload["entity_id"], "look_hash": _look_hash(payload)}
    return _approve(
        project_dir, "look_lock", f"{payload['entity_kind']}:{payload['entity_id']}", record, "look_lock",
        payload["entity_id"],
        {"action": "retire", "entity_kind": payload["entity_kind"], "look_hash": _look_hash(payload)},
    )


def pin_project(project_dir: Path, version: str, *, supersedes: str | None = None) -> dict:
    record = migration_record("authored-film", version, manifest_digest(f"authored-film@{version}"))
    return _approve(
        project_dir, "pipeline", "pipeline:authored-film", record, "pipeline_migration", "authored-film",
        {"supersedes_receipt_id": supersedes},
    )


def png_bytes(seed: str, size=(8, 8)) -> bytes:
    from PIL import Image

    h = hashlib.sha256(seed.encode()).digest()
    im = Image.new("RGB", size, (h[0], h[1], h[2]))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def image(
    project_dir: Path, seed: str, *, role: str = "hero", look_refs: list[dict] | None = None,
    headshot_ref: dict | None = None, references_applied: list[dict] | None = None,
    input_asset_ids: list[str] | None = None, subdir: str = "canon/visual/objects",
    receipt_prompt_recipe: dict | None = None,
) -> dict:
    """A real PNG under the project, receipted with look_refs/headshot_ref, as a model ImageRef.
    ``receipt_prompt_recipe`` seals a builder recipe into the generation receipt."""
    data = png_bytes(seed)
    sha = hashlib.sha256(data).hexdigest()
    rel = f"{subdir}/{sha}.png"
    path = project_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    row = receipts.record_generation(
        project_dir, execution_id=f"exec-{seed}", tool="seedream_image",
        normalized_inputs_hash="a" * 64, output_sha256=sha, cost_usd=0.01,
        started_at="2026-08-26T00:00:00+00:00", finished_at="2026-08-26T00:00:01+00:00",
        model_endpoint=IMAGE_MODEL, prompt=f"{role} of {seed}", references_applied=references_applied,
        input_asset_ids=input_asset_ids, look_refs=look_refs, headshot_ref=headshot_ref or None,
        prompt_recipe=receipt_prompt_recipe,
    )
    return {
        "asset_id": sha, "path": rel, "role": role,
        "provenance": {"generator_kind": "model", "model_endpoint": IMAGE_MODEL,
                       "prompt": f"{role} of {seed}", "generation_receipt_id": row["receipt_id"]},
    }


def look_ref(payload: dict, receipt: dict) -> dict:
    return {"entity_kind": payload["entity_kind"], "entity_id": payload["entity_id"],
            "look_hash": _look_hash(payload), "receipt_id": receipt["receipt_id"]}


def look_refs_for(payload: dict) -> list[dict]:
    return [{"entity_kind": payload["entity_kind"], "entity_id": payload["entity_id"], "look_hash": _look_hash(payload)}]


def prompt_recipe(payload: dict) -> dict:
    return {"look_hash": _look_hash(payload), "builder_version": "builder/1",
            "fields_used": ["prompt_safe_description", "hair"],
            "rendered_sha256": hashlib.sha256(payload["prompt_safe_description"].encode()).hexdigest()}


def approve_headshot(
    project_dir: Path, payload: dict, asset_id: str, candidates_digest: str, *, recipe: dict | None = None,
    supersedes: str | None = None, origin: str = "generated", import_receipt_id: str | None = None,
) -> tuple[dict, dict]:
    record = headshot_record(
        entity_id=payload["entity_id"], look_hash=_look_hash(payload), asset_id=asset_id, origin=origin,
        import_receipt_id=import_receipt_id,
        prompt_recipe_sha256=record_sha256(recipe) if recipe else None,
        candidates_checkpoint_digest=candidates_digest,
    )
    receipt = _approve(
        project_dir, "headshots", f"character:{payload['entity_id']}", record, "headshot", payload["entity_id"],
        {"action": "activate", "entity_kind": "character", "look_hash": _look_hash(payload),
         "supersedes_receipt_id": supersedes},
    )
    return record, receipt


def retire_headshot(project_dir: Path, payload: dict, receipt_id: str) -> dict:
    record = {"entity_kind": "character", "entity_id": payload["entity_id"], "look_hash": _look_hash(payload)}
    return _approve(
        project_dir, "headshots", f"character:{payload['entity_id']}", record, "headshot", payload["entity_id"],
        {"action": "retire", "entity_kind": "character", "look_hash": _look_hash(payload),
         "supersedes_receipt_id": receipt_id},
    )


def look_packet_for(project_dir: Path, *payloads: dict) -> dict:
    from lib.look_ingest import active_looks

    active = active_looks(project_dir)
    return {"version": "1.0", "looks": [
        {"entity_kind": p["entity_kind"], "entity_id": p["entity_id"], "look_spec": p,
         "look_hash": _look_hash(p), "receipt_id": active[(p["entity_kind"], p["entity_id"])].receipt_id,
         "source_ticket_ref": {"id": "wf-0badc0de"}}
        for p in payloads
    ]}


def read_checkpoint_file(pipeline_dir: Path, stage: str) -> dict:
    return json.loads((pipeline_dir / PROJECT / f"checkpoint_{stage}.json").read_text())

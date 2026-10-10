"""1.0 -> 1.1 upgraders for the versioned artifact schemas.

Rules (PLAN.md section 2):
- Canon ids are deterministic: ``<entity_type>-<source_index>-<sha256(name + source_text)[:8]>``
  where source_text is the entity's ``one_line_identity`` / ``description`` /
  ``visual_identity`` (first that exists). A collision fails the migration.
- Scene refs, proposal cast and shots are NEVER synthesized. Upgraded
  scene_plans / proposal_packets / asset_manifests are marked
  ``migration_status: needs_review`` so enforcement refuses them for paid
  stages until a human-reviewed regeneration is approved.
- Upgraders never mutate their input and only accept version "1.0".
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

_SOURCE_TEXT_FIELDS = ("one_line_identity", "description", "visual_identity")


class MigrationError(ValueError):
    """The artifact cannot be upgraded without inventing data."""


def _require_1_0(artifact: dict[str, Any], name: str) -> dict[str, Any]:
    version = artifact.get("version") if isinstance(artifact, dict) else None
    if version != "1.0":
        raise MigrationError(f"{name}: expected version '1.0', got {version!r}")
    return copy.deepcopy(artifact)


def _digest(name: str, source_text: str) -> str:
    return hashlib.sha256((name + source_text).encode("utf-8")).hexdigest()[:8]


def _entity_id(entity_type: str, index: int, name: str, source_text: str) -> str:
    return f"{entity_type}-{index}-{_digest(name, source_text)}"


def _source_text(entity: dict[str, Any]) -> str:
    for field in _SOURCE_TEXT_FIELDS:
        value = entity.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def upgrade_canon_packet_1_0_to_1_1(packet: dict[str, Any]) -> dict[str, Any]:
    out = _require_1_0(packet, "canon_packet")
    for entity_type, array_key in (("character", "characters"), ("location", "locations")):
        seen: dict[str, str] = {}
        for index, entity in enumerate(out.get(array_key, [])):
            name = entity.get("name")
            if not isinstance(name, str) or not name:
                raise MigrationError(f"canon_packet: {array_key}[{index}] has no name; cannot derive an id")
            new_id = _entity_id(entity_type, index, name, _source_text(entity))
            if new_id in seen:
                raise MigrationError(
                    f"canon_packet: id collision {new_id!r} between {array_key} "
                    f"{seen[new_id]!r} and {name!r}"
                )
            seen[new_id] = name
            entity["id"] = new_id
    out["version"] = "1.1"
    return out


def upgrade_scene_plan_1_0_to_1_1(plan: dict[str, Any]) -> dict[str, Any]:
    out = _require_1_0(plan, "scene_plan")
    for scene in out.get("scenes", []):
        scene["character_refs"] = []
        scene["location_ref"] = None
        scene["entity_free"] = False
        scene["model_endpoint"] = ""
        scene["shots"] = []
    out["version"] = "1.1"
    out["migration_status"] = "needs_review"
    return out


def upgrade_proposal_packet_1_0_to_1_1(packet: dict[str, Any]) -> dict[str, Any]:
    out = _require_1_0(packet, "proposal_packet")
    out["runtime_shape"] = {"format": "trailer"}
    out["cast"] = {"character_ids": [], "location_ids": []}
    out["version"] = "1.1"
    out["migration_status"] = "needs_review"
    return out


def upgrade_asset_manifest_1_0_to_1_1(manifest: dict[str, Any]) -> dict[str, Any]:
    """Classify assets; never mark anything selected or invent references.

    image/video assets become ``shot_visual`` candidates bound to their 1.0
    scene (shot_id = scene_id, take_id = asset id, model_endpoint = model or
    "") and force ``needs_review``. 1.0 string references cannot become
    content-addressed reference objects, so they are moved into
    ``continuity.notes`` and also force ``needs_review``.
    """
    out = _require_1_0(manifest, "asset_manifest")
    needs_review = False
    for asset in out.get("assets", []):
        continuity = asset.get("continuity")
        if isinstance(continuity, dict):
            legacy = [r for r in continuity.get("references_applied", []) if isinstance(r, str)]
            if legacy:
                needs_review = True
                note = "legacy 1.0 references (unverified, not content-addressed): " + ", ".join(legacy)
                existing = continuity.get("notes")
                continuity["notes"] = f"{existing}\n{note}" if existing else note
            continuity["references_applied"] = [
                r for r in continuity.get("references_applied", []) if isinstance(r, dict)
            ]
        if asset.get("type") in ("image", "video"):
            needs_review = True
            asset["asset_class"] = "shot_visual"
            asset["shot_id"] = asset["scene_id"]
            asset["take_id"] = asset["id"]
            asset["usage_status"] = "candidate"
            asset["model_endpoint"] = asset.get("model", "") or ""
            if not isinstance(asset.get("continuity"), dict):
                raise MigrationError(
                    f"asset_manifest: visual asset {asset.get('id')!r} has no continuity "
                    "evidence; refusing to invent canon_refs"
                )
        else:
            asset["asset_class"] = "non_shot"
    out["version"] = "1.1"
    out["migration_status"] = "needs_review" if needs_review else "ok"
    return out

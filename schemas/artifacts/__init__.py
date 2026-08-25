"""Artifact schema loading and validation utilities.

Versioned artifacts (canon_packet, proposal_packet, scene_plan, asset_manifest)
are a top-level ``oneOf`` over ``$defs.v1_0`` / ``$defs.v1_1`` keyed on the
``version`` field. ``validate_artifact`` dispatches on that field so a failure
names the version that was checked instead of the union of both branches'
errors, and then applies the Python-level checks JSON Schema cannot express
(id / shot_id uniqueness).

The project config schema (``schemas/project_config.schema.json``) is not an
artifact but is loaded from here too, via ``load_project_config_schema`` and
``validate_project_config``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_DIR = Path(__file__).parent
PROJECT_CONFIG_SCHEMA_PATH = SCHEMA_DIR.parent / "project_config.schema.json"

ARTIFACT_NAMES = [
    "research_brief",
    "proposal_packet",
    "brief",
    "script",
    "character_design",
    "rig_plan",
    "pose_library",
    "scene_plan",
    "action_timeline",
    "asset_manifest",
    "edit_decisions",
    "render_report",
    "publish_log",
    "review",
    "cost_log",
    "decision_log",
    "source_media_review",
    "final_review",
    "character_qa_report",
    "video_analysis_brief",
    "canon_packet",
    "visual_bible",
]


class ArtifactValidationError(jsonschema.ValidationError):
    """A Python-level artifact rule failed (uniqueness etc.).

    Subclasses ``jsonschema.ValidationError`` so existing callers that catch
    schema failures keep working.
    """


class ArtifactVersionError(ArtifactValidationError):
    """The artifact's ``version`` is missing or has no matching schema branch."""


def load_schema(name: str) -> dict:
    """Load a JSON schema by artifact name."""
    path = SCHEMA_DIR / f"{name}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_project_config_schema() -> dict:
    """Load schemas/project_config.schema.json (projects/<slug>/project.yaml)."""
    with open(PROJECT_CONFIG_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_project_config(data: dict[str, Any]) -> None:
    """Validate a parsed project.yaml against the project config schema."""
    jsonschema.validate(instance=data, schema=load_project_config_schema())


def artifact_version(data: dict[str, Any]) -> str:
    """Return the artifact's ``version`` string; raise if absent or not a string."""
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not version:
        raise ArtifactVersionError("artifact has no string 'version' field")
    return version


def _local_ref(schema: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local refs are supported in oneOf branches: {ref}")
    node: Any = schema
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _version_branches(schema: dict) -> dict[str, str] | None:
    """Map version const -> local $ref for a versioned schema, else None."""
    if "oneOf" not in schema:
        return None
    branches: dict[str, str] = {}
    for branch in schema["oneOf"]:
        ref = branch["$ref"]
        body = _local_ref(schema, ref)
        branches[body["properties"]["version"]["const"]] = ref
    return branches


def _validate_against_branch(name: str, version: str, schema: dict, ref: str, data: dict) -> None:
    branch_schema = {k: v for k, v in schema.items() if k not in ("oneOf", "properties", "required", "type")}
    branch_schema["$ref"] = ref
    try:
        jsonschema.validate(instance=data, schema=branch_schema)
    except jsonschema.ValidationError as exc:
        exc.message = f"[{name} v{version}] {exc.message}"
        raise


def _check_unique(name: str, version: str, items: list, key: str, where: str) -> None:
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or key not in item:
            continue
        value = item[key]
        if value in seen:
            raise ArtifactValidationError(
                f"[{name} v{version}] duplicate {key} {value!r} in {where}"
            )
        seen.add(value)


def _python_checks(name: str, version: str, data: dict) -> None:
    if name == "canon_packet" and version != "1.0":
        for arr in ("characters", "locations"):
            _check_unique(name, version, data.get(arr, []), "id", arr)
    elif name == "scene_plan" and version != "1.0":
        shots = [shot for scene in data.get("scenes", []) for shot in scene.get("shots", [])]
        _check_unique(name, version, shots, "shot_id", "scenes[].shots")
    elif name == "asset_manifest" and version != "1.0":
        assets = data.get("assets", [])
        _check_unique(name, version, assets, "id", "assets")
        seen_takes: set[tuple[str, str]] = set()
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("take_id") is None:
                continue
            key = (asset.get("shot_id"), asset["take_id"])
            if key in seen_takes:
                raise ArtifactValidationError(
                    f"[{name} v{version}] duplicate (shot_id, take_id) {key!r} in assets"
                )
            seen_takes.add(key)
    elif name == "visual_bible":
        for arr in ("characters", "locations"):
            _check_unique(name, version, data.get(arr, []), "id", arr)


def validate_artifact(name: str, data: dict[str, Any]) -> None:
    """Validate artifact data against its schema. Raises on failure.

    Versioned schemas are dispatched on ``data["version"]``; an unknown
    version raises ``ArtifactVersionError`` naming the supported versions.
    """
    schema = load_schema(name)
    branches = _version_branches(schema)
    if branches is None:
        jsonschema.validate(instance=data, schema=schema)
        version = data.get("version", "") if isinstance(data, dict) else ""
    else:
        supported = sorted(branches)
        try:
            version = artifact_version(data)
        except ArtifactVersionError as exc:
            raise ArtifactVersionError(
                f"{name}: {exc.message}; supported versions: {supported}"
            ) from None
        if version not in branches:
            raise ArtifactVersionError(
                f"{name}: unsupported version {version!r}; supported versions: {supported}"
            )
        _validate_against_branch(name, version, schema, branches[version], data)
    _python_checks(name, version, data)


def list_schemas() -> list[str]:
    """List all available artifact schema names."""
    return [p.stem.replace(".schema", "") for p in SCHEMA_DIR.glob("*.schema.json")]

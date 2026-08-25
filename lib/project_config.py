"""Verified project configuration (PLAN §0, inspection fixes #2/#13/#14).

``load_verified_project_config`` is the ONLY way runtime code (tools, enforcement,
orchestration) may learn a project's budget cap, cast cap, default video endpoint,
and provider-egress consent. It refuses to return a config that is not bound to a
human-approved ``config`` receipt for the exact file digest, so a caller can never
act on an edited-but-unapproved ``project.yaml``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_CONFIG_FILENAME = "project.yaml"
EGRESS_CLASS_PROMPTS = "prompts"
EGRESS_CLASS_REFERENCE_IMAGES = "reference_images"


class ProjectConfigError(RuntimeError):
    """Missing, malformed, or unapproved project configuration."""


@dataclass(frozen=True)
class VerifiedProjectConfig:
    project_root: Path
    digest: str
    data: dict[str, Any]

    @property
    def budget_usd_cap(self) -> float:
        return float(self.data["budget_usd_cap"])

    @property
    def default_video_endpoint(self) -> str:
        return str(self.data["default_video_endpoint"])

    @property
    def egress_provider(self) -> str:
        return str(self.data["provider_egress"]["provider"])

    @property
    def egress_classes(self) -> frozenset[str]:
        return frozenset(self.data["provider_egress"]["content_classes"])

    def require_egress(self, provider: str, *content_classes: str) -> None:
        """Raise unless every content class is approved for ``provider``."""
        if self.egress_provider != provider:
            raise ProjectConfigError(
                f"provider egress approved for {self.egress_provider!r}, not {provider!r}"
            )
        missing = [c for c in content_classes if c not in self.egress_classes]
        if missing:
            raise ProjectConfigError(
                f"provider egress for {provider!r} does not cover {missing}; "
                f"approved classes: {sorted(self.egress_classes)}"
            )


def read_project_config(project_root: Path | str) -> tuple[dict[str, Any], str]:
    """Parse and schema-validate ``project.yaml``. Returns (data, sha256 of file bytes)."""
    import yaml
    from schemas.artifacts import validate_project_config

    path = Path(project_root) / PROJECT_CONFIG_FILENAME
    if not path.is_file():
        raise ProjectConfigError(f"{path} not found")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = yaml.safe_load(raw)
    except Exception as exc:  # noqa: BLE001
        raise ProjectConfigError(f"{path} could not be parsed: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectConfigError(f"{path} must be a mapping")
    try:
        validate_project_config(data)
    except Exception as exc:  # noqa: BLE001
        raise ProjectConfigError(f"{path} failed schema validation: {exc}") from exc
    return data, digest


def load_verified_project_config(project_root: Path | str) -> VerifiedProjectConfig:
    """Load ``project.yaml`` and require a verified ``config`` approval receipt
    whose record is ``{"config_sha256": <digest>}`` (signature + ledger checked
    by ``lib.receipts.find_approval``). Raises ProjectConfigError otherwise."""
    from lib.canonical_json import record_sha256
    from lib.receipts import find_approval

    root = Path(project_root)
    data, digest = read_project_config(root)
    record = {"config_sha256": digest}
    receipt = find_approval(root, "config", record_sha256=record_sha256(record))
    if receipt is None:
        raise ProjectConfigError(
            f"{PROJECT_CONFIG_FILENAME} digest {digest[:12]}… has no verified human "
            f"'config' approval receipt; approve it with scripts/gate_approve.py "
            f"before any paid call or governed stage."
        )
    return VerifiedProjectConfig(project_root=root, digest=digest, data=data)

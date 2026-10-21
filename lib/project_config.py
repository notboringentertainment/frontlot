"""Verified project configuration (PLAN §0, inspection fixes #2/#13/#14; D19 1.1).

``load_verified_project_config`` is the ONLY way runtime code (tools, enforcement,
orchestration) may learn a project's budget cap, cast cap, default video endpoint,
provider-egress consent and (1.1) the sheet-QC judge. It refuses to return a
config that is not bound to a human-approved ``config`` receipt for the exact
file digest, so a caller can never act on an edited-but-unapproved ``project.yaml``.

Three schema versions are accepted and normalised to ONE in-memory shape
(D19 R2#2): 1.0 carries a single FAL ``provider_egress`` object; 1.1 carries a
list of per-provider entries plus a ``qc`` block; 1.2 (D20) adds the hero QC
fields ``qc.hero_policy_sha256`` and ``qc.max_hero_attempts``. Consumers read
only the normalised accessors (``egress_for``, ``require_egress``, ``qc``,
``require_qc``, ``require_hero_qc``), never the raw fields. Hero runs need
1.2: there is NO code default for the hero budget or bundle (R1#7, R5#3).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

PROJECT_CONFIG_FILENAME = "project.yaml"
EGRESS_CLASS_PROMPTS = "prompts"
EGRESS_CLASS_REFERENCE_IMAGES = "reference_images"
EGRESS_CLASS_GENERATED_SHEET_IMAGES = "generated_sheet_images"
SUPPORTED_VERSIONS = ("1.0", "1.1", "1.2")
HERO_QC_VERSION = "1.2"


class ProjectConfigError(RuntimeError):
    """Missing, malformed, or unapproved project configuration."""


@dataclass(frozen=True)
class QCConfig:
    judge_provider: str
    judge_model: str
    policy_bundle_sha256: str        # the SHEET bundle (D19 meaning)
    max_attempts_per_series: int
    hero_policy_sha256: Optional[str] = None   # 1.2 only: the HERO bundle
    max_hero_attempts: Optional[int] = None    # 1.2 only: hero budget per entity per look

    @property
    def has_hero(self) -> bool:
        return self.hero_policy_sha256 is not None and self.max_hero_attempts is not None


@dataclass(frozen=True)
class VerifiedProjectConfig:
    project_root: Path
    digest: str
    data: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.data.get("version"))

    @property
    def budget_usd_cap(self) -> float:
        return float(self.data["budget_usd_cap"])

    @property
    def default_video_endpoint(self) -> str:
        return str(self.data["default_video_endpoint"])

    # ---- egress (normalised: provider -> frozenset of classes) ----

    @property
    def egress(self) -> dict[str, frozenset[str]]:
        return normalize_egress(self.data)

    @property
    def egress_provider(self) -> str:
        """The single provider of a 1.0 config; for 1.1 the first listed.
        Kept for callers that only print it — never use it to authorise."""
        return next(iter(self.egress))

    @property
    def egress_classes(self) -> frozenset[str]:
        """Union of consented classes across providers (display only)."""
        out: set[str] = set()
        for classes in self.egress.values():
            out |= classes
        return frozenset(out)

    def egress_for(self, provider: str) -> frozenset[str]:
        return self.egress.get(provider, frozenset())

    def require_egress(self, provider: str, *content_classes: str) -> None:
        """Raise unless every content class is approved for ``provider``."""
        approved = self.egress.get(provider)
        if approved is None:
            raise ProjectConfigError(
                f"provider egress approved for {sorted(self.egress)}, not {provider!r}"
            )
        missing = [c for c in content_classes if c not in approved]
        if missing:
            raise ProjectConfigError(
                f"provider egress for {provider!r} does not cover {missing}; "
                f"approved classes: {sorted(approved)}"
            )

    # ---- D19 QC ----

    @property
    def qc(self) -> Optional[QCConfig]:
        block = self.data.get("qc")
        if not isinstance(block, dict):
            return None
        hero_hash = block.get("hero_policy_sha256")
        hero_cap = block.get("max_hero_attempts")
        return QCConfig(
            judge_provider=str(block["judge_provider"]),
            judge_model=str(block["judge_model"]),
            policy_bundle_sha256=str(block["policy_bundle_sha256"]),
            max_attempts_per_series=int(block["max_attempts_per_series"]),
            hero_policy_sha256=str(hero_hash) if hero_hash is not None else None,
            max_hero_attempts=int(hero_cap) if hero_cap is not None else None,
        )

    def require_qc(self) -> QCConfig:
        qc = self.qc
        if qc is None:
            raise ProjectConfigError(
                f"{PROJECT_CONFIG_FILENAME} {self.version} has no qc block; sheet QC needs a "
                f"1.1 config naming the judge (judge_provider, judge_model, policy_bundle_sha256, "
                f"max_attempts_per_series) approved through the human gate."
            )
        return qc

    def require_hero_qc(self) -> QCConfig:
        """D20: hero judging needs a 1.2 config naming the hero bundle and the
        hero budget. A 1.1 config is sheet-only; nothing about it changes."""
        qc = self.require_qc()
        if not qc.has_hero or self.version != HERO_QC_VERSION:
            raise ProjectConfigError(
                f"{PROJECT_CONFIG_FILENAME} {self.version} carries no hero QC policy; headshot judging needs a "
                f"{HERO_QC_VERSION} config with qc.hero_policy_sha256 and qc.max_hero_attempts approved through the human gate."
            )
        return qc


def normalize_egress(data: dict[str, Any]) -> dict[str, frozenset[str]]:
    """``provider -> classes`` for either schema version. A 1.1 list with two
    entries for one provider is rejected here (R3 nb#2): ``uniqueItems`` alone
    would let conflicting entries through."""
    raw = data.get("provider_egress")
    if isinstance(raw, dict):
        return {str(raw["provider"]): frozenset(raw["content_classes"])}
    if isinstance(raw, list):
        out: dict[str, frozenset[str]] = {}
        for entry in raw:
            provider = str(entry["provider"])
            if provider in out:
                raise ProjectConfigError(f"provider_egress lists {provider!r} more than once")
            out[provider] = frozenset(entry["content_classes"])
        return out
    raise ProjectConfigError("provider_egress must be an object (1.0) or a list (1.1)")


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
    if str(data.get("version")) not in SUPPORTED_VERSIONS:
        raise ProjectConfigError(
            f"{path} version {data.get('version')!r} is not one of {SUPPORTED_VERSIONS}"
        )
    try:
        validate_project_config(data)
    except Exception as exc:  # noqa: BLE001
        raise ProjectConfigError(f"{path} failed schema validation: {exc}") from exc
    normalize_egress(data)  # duplicate-provider check runs at read time too
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

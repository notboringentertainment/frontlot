"""Ledger-derived checkpoint invalidation (plan D10, R4#3 / R5#3 / R5#4 / R6#7).

Checkpoints never mutate. When a ratified look or an approved headshot is
retired or replaced, the completed work that consumed it is *invalidated*:
derived from the approval ledger on EVERY validation, never from a file that
could be deleted. The rule is conservative:

- a ``look_lock`` receipt with ``action: retire``, or an ``activate`` that
  names ``supersedes_look_hash`` (a replacement), invalidates every completed
  checkpoint from ``headshots`` onward (``visual_bible`` onward on manifests
  that have no headshots stage) that was written before the receipt;
- a ``headshot`` receipt with ``action: retire``, or an ``activate`` that
  names ``supersedes_receipt_id``, invalidates every completed checkpoint
  from ``visual_bible`` onward written before the receipt.

A checkpoint written AFTER the receipt is a re-approval and stands.
``projects/<slug>/invalidations.jsonl`` is only a rebuildable cache: it is
rewritten from the ledger by ``rebuild_cache`` and never read to decide
anything (a missing or truncated cache cannot restore retired canon).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

CACHE_FILENAME = "invalidations.jsonl"
LOOK_FROM_STAGES = ("headshots", "visual_bible")
HEADSHOT_FROM_STAGES = ("visual_bible",)


@dataclass(frozen=True)
class Invalidation:
    stage: str
    checkpoint_timestamp: str
    receipt_id: str
    receipt_kind: str
    action: str
    entity_kind: Optional[str]
    entity_id: Optional[str]
    approved_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "checkpoint_timestamp": self.checkpoint_timestamp,
            "receipt_id": self.receipt_id,
            "receipt_kind": self.receipt_kind,
            "action": self.action,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "approved_at": self.approved_at,
        }


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _read_checkpoint_raw(pipeline_dir: Path, project_id: str, stage: str) -> Optional[dict[str, Any]]:
    path = pipeline_dir / project_id / f"checkpoint_{stage}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _invalidating_receipts(project_dir: Path) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    """(receipt, from_stages) for every verified receipt that invalidates."""
    from lib.receipts import verified_approvals

    out: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    for row in verified_approvals(project_dir, "look_lock"):
        if row.get("action") == "retire" or row.get("supersedes_look_hash"):
            out.append((row, LOOK_FROM_STAGES))
    for row in verified_approvals(project_dir, "headshot"):
        if row.get("action") == "retire" or row.get("supersedes_receipt_id"):
            out.append((row, HEADSHOT_FROM_STAGES))
    return out


def invalidated_checkpoints(
    pipeline_dir: Path | str, project_id: str, stages: list[str]
) -> dict[str, Invalidation]:
    """``stage -> Invalidation`` for every completed checkpoint of ``stages``
    (the pinned manifest's order) that a retire/replacement receipt
    invalidates. Computed from the ledger; the cache is refreshed as a side
    effect but never consulted."""
    pipeline_dir = Path(pipeline_dir)
    project_dir = pipeline_dir / project_id
    if not project_dir.is_dir():
        return {}
    receipts = _invalidating_receipts(project_dir)
    result: dict[str, Invalidation] = {}
    if receipts:
        for receipt, from_stages in receipts:
            approved_at = _parse_ts(receipt.get("approved_at"))
            if approved_at is None:
                continue
            start = next((s for s in from_stages if s in stages), None)
            if start is None:
                continue
            for stage in stages[stages.index(start):]:
                if stage in result:
                    continue
                checkpoint = _read_checkpoint_raw(pipeline_dir, project_id, stage)
                if not checkpoint or checkpoint.get("status") != "completed":
                    continue
                written = _parse_ts(checkpoint.get("timestamp"))
                if written is None or written > approved_at:
                    continue
                result[stage] = Invalidation(
                    stage=stage,
                    checkpoint_timestamp=str(checkpoint.get("timestamp")),
                    receipt_id=str(receipt.get("receipt_id")),
                    receipt_kind=str(receipt.get("kind")),
                    action=str(receipt.get("action")),
                    entity_kind=receipt.get("entity_kind"),
                    entity_id=receipt.get("entity_id"),
                    approved_at=str(receipt.get("approved_at")),
                )
    rebuild_cache(project_dir, result)
    return result


def rebuild_cache(project_dir: Path | str, invalidations: dict[str, Invalidation]) -> Path:
    """Rewrite the cache from a freshly derived set (fail-closed: a cache that
    disagrees with the ledger is simply replaced)."""
    from lib.state_io import atomic_write_bytes

    path = Path(project_dir) / CACHE_FILENAME
    rows = [json.dumps(inv.to_dict(), sort_keys=True) for _, inv in sorted(invalidations.items())]
    payload = ("\n".join(rows) + ("\n" if rows else "")).encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return path
    if not rows and not path.exists():
        return path
    atomic_write_bytes(path, payload)
    return path


def is_invalidated(
    pipeline_dir: Path | str, project_id: str, stages: list[str], stage: str
) -> Optional[Invalidation]:
    return invalidated_checkpoints(pipeline_dir, project_id, stages).get(stage)

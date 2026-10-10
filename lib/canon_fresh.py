"""Read today's canon from the Story-drive folder and compare it with the
canon snapshot Front Lot approved.

The approved ``canon_packet`` (``checkpoint_canon_ingest.json``) is a receipt:
it proves which canon a paid asset came from. It is not where Front Lot looks
for current canon. This module reads the film's Story-drive folder
(``project.yaml: wayfinder_root``) live and reports, read-only:

- source files the snapshot pinned that have since changed or gone missing,
- decisions resolved in Story-drive since the snapshot,
- approved work that no longer matches canon (an approved trailer-cast entry
  that Story-drive has since scoped out).

It never writes anything: not the Story-drive folder, not the snapshot, not
any signed checkpoint. Mismatches are shown to Ben; changing canon or approved
work is his step in his own tools.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.look_ingest import RESOLVED_SUBDIR, WAYFINDER_ROOT_FIELD, LookIngestError, wayfinder_root_for
from lib.pathsafe import PathSafetyError, resolve_input

_HEADER_RE = re.compile(r"^([a-z][a-z0-9-]*):[ \t]*(.*)$")
_ANSWER_RE = re.compile(r"^##\s+Answer\b(.*)$")
_ENTITY_RE = re.compile(r"^entity_id:[ \t]*['\"]?([A-Za-z0-9][A-Za-z0-9_-]*)['\"]?[ \t]*$", re.MULTILINE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# What Ben can do about an approved-cast entry Story-drive scoped out. Front Lot
# has no step to change an approved cast yet, so this says what is true today.
SCOPED_OUT_NEXT_STEP = (
    "Nothing is blocked: shots don't depend on the trailer cast, and Front Lot warns before any job that uses it. "
    "Front Lot can't change an approved trailer cast yet, so this stays listed until it can."
)


class CanonReadError(RuntimeError):
    """Today's canon could not be read; the message is a plain sentence for Ben."""


@dataclass(frozen=True)
class SourceChange:
    path: str  # relative to the Story-drive folder
    state: str  # "changed" | "missing" | "not downloaded"


@dataclass(frozen=True)
class Decision:
    title: str
    file: str
    resolved: str
    scoped_out: bool


@dataclass(frozen=True)
class Mismatch:
    kind: str  # "character" | "location"
    entity_id: str
    approved_in: str
    ticket: str
    resolved: str
    message: str
    next_step: str


@dataclass(frozen=True)
class CanonChanges:
    snapshot_date: str
    changed_sources: list[SourceChange] = field(default_factory=list)
    new_decisions: list[Decision] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    scoped_out: dict[str, Decision] = field(default_factory=dict)  # entity id -> the ticket that scoped it out

    @property
    def unchanged(self) -> bool:
        return not (self.changed_sources or self.new_decisions or self.mismatches)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["unchanged"] = self.unchanged
        return out


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _snapshot(project_root: Path) -> tuple[dict[str, Any], str]:
    """The approved canon packet and the date it was taken."""
    checkpoint = _read_json(project_root / "checkpoint_canon_ingest.json") or {}
    artifacts = checkpoint.get("artifacts")
    packet = artifacts.get("canon_packet") if isinstance(artifacts, dict) else None
    if not isinstance(packet, dict):
        packet = _read_json(project_root / "artifacts" / "canon_packet.json")
    if not isinstance(packet, dict):
        raise CanonReadError("This film has no approved canon snapshot yet, so there is nothing to compare today's canon with.")
    provenance = packet.get("provenance") if isinstance(packet.get("provenance"), dict) else {}
    for value in (provenance.get("ingested_at"), checkpoint.get("timestamp")):
        m = _DATE_RE.match(value) if isinstance(value, str) else None
        if m:
            return packet, m.group(0)
    raise CanonReadError("The approved canon snapshot has no date, so Front Lot can't tell what changed since.")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _changed_sources(packet: dict[str, Any], root: Path) -> tuple[list[SourceChange], set[Path]]:
    changes: list[SourceChange] = []
    pinned: set[Path] = set()
    for doc in packet.get("source_documents") or []:
        if not isinstance(doc, dict) or not isinstance(doc.get("path"), str):
            continue
        path = Path(doc["path"])
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue  # outside the Story-drive folder: not today's canon
        pinned.add(rel)
        placeholder = path.with_name(f".{path.name}.icloud")
        if not path.exists():
            changes.append(SourceChange(str(rel), "not downloaded" if placeholder.exists() else "missing"))
            continue
        expected = doc.get("sha256")
        if not isinstance(expected, str):
            continue
        try:
            actual = _sha256(resolve_input(path, root))
        except (PathSafetyError, OSError):
            changes.append(SourceChange(str(rel), "missing"))
            continue
        if actual != expected:
            changes.append(SourceChange(str(rel), "changed"))
    return changes, pinned


def _read_ticket(path: Path, root: Path) -> tuple[str, str, bool, str | None] | None:
    """(title, resolved date, scoped out, look entity id) of one resolved ticket."""
    try:
        text = resolve_input(path, root).read_text(encoding="utf-8")
    except (PathSafetyError, OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith("# ") else path.stem
    header: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("## "):
            break
        m = _HEADER_RE.match(line)
        if m:
            header.setdefault(m.group(1), m.group(2).strip())
    resolved_m = _DATE_RE.search(header.get("resolved", ""))
    if resolved_m:
        resolved = resolved_m.group(0)
    else:
        resolved = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    scoped_out = any(
        "scoped out" in m.group(1).lower() for m in (_ANSWER_RE.match(line) for line in lines) if m
    )
    entity = _ENTITY_RE.search(text)
    return title, resolved, scoped_out, entity.group(1) if entity else None


def _names(packet: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("characters", "locations"):
        for entry in packet.get(key) or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("name"), str):
                out[entry["id"]] = entry["name"]
    return out


def _approved_cast(project_root: Path) -> list[tuple[str, str]]:
    checkpoint = _read_json(project_root / "checkpoint_proposal.json") or {}
    artifacts = checkpoint.get("artifacts")
    proposal = artifacts.get("proposal_packet") if isinstance(artifacts, dict) else None
    cast = proposal.get("cast") if isinstance(proposal, dict) else None
    if not isinstance(cast, dict):
        return []
    out: list[tuple[str, str]] = []
    for kind, key in (("character", "character_ids"), ("location", "location_ids")):
        ids = cast.get(key)
        if isinstance(ids, list):
            out += [(kind, i) for i in ids if isinstance(i, str)]
    return out


def read_canon_changes(project_root: Path | str) -> CanonChanges | None:
    """What changed in the film's Story-drive canon since the approved snapshot.

    Returns None for a film with no ``wayfinder_root`` (no Story-drive folder).
    Raises :class:`CanonReadError` when the folder or the snapshot can't be read.
    """
    import yaml

    project_root = Path(project_root)
    try:
        data = yaml.safe_load((project_root / "project.yaml").read_bytes())
    except (OSError, yaml.YAMLError) as exc:
        raise CanonReadError(f"Front Lot can't read this film's settings, so it can't read today's canon: {exc}") from exc
    value = data.get(WAYFINDER_ROOT_FIELD) if isinstance(data, dict) else None
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        root = wayfinder_root_for(project_root)
    except LookIngestError as exc:
        raise CanonReadError(f"Front Lot can't read your Story-drive folder: {exc}") from exc

    packet, snapshot_date = _snapshot(project_root)
    changed, pinned = _changed_sources(packet, root)

    resolved_dir = root / RESOLVED_SUBDIR
    try:
        names = sorted(p.name for p in resolved_dir.iterdir() if p.name.endswith(".md") and not p.name.startswith("."))
    except OSError as exc:
        raise CanonReadError(f"Front Lot can't read Story-drive's resolved decisions: {exc}") from exc

    new_decisions: list[Decision] = []
    scoped_out: dict[str, Decision] = {}
    for name in names:
        ticket = _read_ticket(resolved_dir / name, root)
        if ticket is None:
            continue
        title, resolved, is_scoped_out, entity = ticket
        decision = Decision(title, name, resolved, is_scoped_out)
        if is_scoped_out and entity:
            scoped_out[entity] = decision
        if RESOLVED_SUBDIR / name not in pinned and resolved >= snapshot_date:
            new_decisions.append(decision)
    new_decisions.sort(key=lambda d: (d.resolved, d.file))

    names_by_id = _names(packet)
    mismatches = [
        Mismatch(
            kind, entity_id, "trailer cast", scoped_out[entity_id].file, scoped_out[entity_id].resolved,
            f"The approved trailer cast lists \"{names_by_id.get(entity_id, entity_id)}\", which Story-drive "
            f"scoped out on {scoped_out[entity_id].resolved}.",
            SCOPED_OUT_NEXT_STEP,
        )
        for kind, entity_id in _approved_cast(project_root)
        if entity_id in scoped_out
    ]
    return CanonChanges(snapshot_date, changed, new_decisions, mismatches, scoped_out)


def warn_scoped_out(project_root: Path | str, names: set[str], *, stream=None) -> list[str]:
    """Print one warning line per job name Story-drive has scoped out. Warns only:
    never raises and never stops the job (Ben's ruling, 2026-10-10)."""
    try:
        changes = read_canon_changes(project_root)
    except Exception:
        return []
    if changes is None:
        return []
    from lib.appeals import _name_key

    wanted = {_name_key(n) for n in names}
    lines = [
        f"Heads up: this job uses {entity}, which Story-drive scoped out on {d.resolved} (\"{d.title}\"). "
        f"Front Lot is not stopping it; tell Ben."
        for entity, d in sorted(changes.scoped_out.items())
        if _name_key(entity) in wanted
    ]
    for line in lines:
        print(line, file=stream or sys.stderr)
    return lines

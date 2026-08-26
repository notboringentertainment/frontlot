"""The single look ingestion routine (plan D10, Slice A step 3) and the
look_lock receipt chain.

Every source of looks — direct story-wayfinder tickets today, the WriterOS
export in Slice B — goes through ``ingest_look_ticket``:

1. ticket authority: YAML front matter ``type: grill`` and ``mode: hitl``,
   the ticket is resolved (a non-empty ``## Answer`` section in a file under
   a ``resolved/`` directory), and EXACTLY ONE ``## Look spec`` section
   carrying exactly one fenced ```yaml block;
2. the block validates against ``schemas/look_spec.schema.json`` (plus the
   Python rules and the prompt-injection scan in ``lib.look_spec``);
3. ``look_hash`` = sha256 of the canonical JSON of the validated payload;
4. the source ticket is referenced by ``{id}`` when the ticket carries an
   immutable ``id: wf-<8hex>``, else (legacy tickets, never edited) by
   ``{path, content_sha256}``.

Ratification is NOT read from the ticket (D12): ``active_looks`` replays the
project's verified ``look_lock`` receipts into a per-key monotonic
supersession chain, and ``build_look_packet`` refuses any entity whose
ingested hash is not the active tip. ``verify_look_refs`` is the boundary
check governed visual tools call before any upload.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import yaml

from lib.canonical_json import record_sha256
from lib.look_spec import (
    LookSpecError,
    generation_sufficient,
    look_hash as _look_hash,
    validate_look_spec,
)

LOOK_LOCK_KIND = "look_lock"
TICKET_ID_RE = re.compile(r"^wf-[0-9a-f]{8}$")
_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```(?:yaml|yml)[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)


class LookIngestError(RuntimeError):
    """A ticket, receipt, or look reference fails the ingestion contract."""


@dataclass(frozen=True)
class IngestedLook:
    entity_kind: str
    entity_id: str
    payload: dict[str, Any]
    look_hash: str
    source_ticket_ref: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity_kind, self.entity_id)


@dataclass(frozen=True)
class ActiveLook:
    entity_kind: str
    entity_id: str
    look_hash: str
    receipt_id: str
    payload: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity_kind, self.entity_id)


# ---- ticket parsing ----


def _sections(body: str) -> dict[str, list[str]]:
    """heading text -> list of section bodies (a heading may repeat)."""
    out: dict[str, list[str]] = {}
    matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.setdefault(m.group(1).strip().lower(), []).append(body[m.end():end])
    return out


def parse_look_ticket(path: Path | str) -> IngestedLook:
    """Read one wayfinder look ticket and return its validated look.

    Raises LookIngestError for anything short of the authority contract.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LookIngestError(f"cannot read look ticket {path}: {exc}") from exc
    text = raw.decode("utf-8", errors="strict") if _is_utf8(raw) else None
    if text is None:
        raise LookIngestError(f"look ticket {path} is not UTF-8")

    fm = _FRONT_MATTER_RE.match(text)
    if not fm:
        raise LookIngestError(f"look ticket {path.name} has no YAML front matter")
    try:
        meta = yaml.safe_load(fm.group(1)) or {}
    except yaml.YAMLError as exc:
        raise LookIngestError(f"look ticket {path.name}: front matter is not YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise LookIngestError(f"look ticket {path.name}: front matter must be a mapping")
    if meta.get("type") != "grill" or meta.get("mode") != "hitl":
        raise LookIngestError(
            f"look ticket {path.name} is type={meta.get('type')!r} mode={meta.get('mode')!r}; "
            f"only type: grill, mode: hitl tickets can carry canon"
        )
    body = text[fm.end():]
    sections = _sections(body)
    answer = "".join(sections.get("answer", [])).strip()
    if not sections.get("answer") or not answer:
        raise LookIngestError(f"look ticket {path.name} has no resolved '## Answer' section")
    if path.parent.name != "resolved":
        raise LookIngestError(
            f"look ticket {path.name} is not under a resolved/ directory — only resolved tickets are ingested"
        )
    spec_sections = sections.get("look spec", [])
    if len(spec_sections) != 1:
        raise LookIngestError(
            f"look ticket {path.name} must have exactly one '## Look spec' section, found {len(spec_sections)}"
        )
    fences = _FENCE_RE.findall(spec_sections[0])
    if len(fences) != 1:
        raise LookIngestError(
            f"look ticket {path.name}: '## Look spec' must contain exactly one fenced yaml block, found {len(fences)}"
        )
    try:
        payload = yaml.safe_load(fences[0])
    except yaml.YAMLError as exc:
        raise LookIngestError(f"look ticket {path.name}: look spec block is not YAML: {exc}") from exc
    try:
        validate_look_spec(payload)
    except LookSpecError as exc:
        raise LookIngestError(f"look ticket {path.name}: {exc}") from exc

    ticket_id = meta.get("id")
    if ticket_id is not None:
        if not isinstance(ticket_id, str) or not TICKET_ID_RE.match(ticket_id):
            raise LookIngestError(f"look ticket {path.name}: id {ticket_id!r} must match wf-<8 hex>")
        ref: dict[str, Any] = {"id": ticket_id}
        declared = payload.get("source_ticket_ref")
        if declared is not None and declared != ref:
            raise LookIngestError(
                f"look ticket {path.name}: source_ticket_ref {declared!r} does not name this ticket's id {ticket_id!r}"
            )
    else:
        if payload.get("source_ticket_ref") is not None:
            raise LookIngestError(
                f"look ticket {path.name}: a legacy ticket (no id:) cannot declare source_ticket_ref "
                f"(its content hash is the reference and would be self-referential)"
            )
        ref = {"path": str(path), "content_sha256": hashlib.sha256(raw).hexdigest()}

    return IngestedLook(
        entity_kind=str(payload["entity_kind"]),
        entity_id=str(payload["entity_id"]),
        payload=payload,
        look_hash=_look_hash(payload),
        source_ticket_ref=ref,
    )


def _is_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# ---- look_lock receipt chain ----


def look_lock_receipts(project_dir: Path | str, *, project_id: Optional[str] = None) -> list[dict]:
    from lib.receipts import verified_approvals

    return verified_approvals(project_dir, LOOK_LOCK_KIND, project_id=project_id)


def active_looks(project_dir: Path | str, *, project_id: Optional[str] = None) -> dict[tuple[str, str], ActiveLook]:
    """Replay the verified look_lock receipts into ``key -> ActiveLook``.

    Chain rules (unique ledger tip per key): the first ``activate`` for a key
    opens it; a later ``activate`` must name ``supersedes_look_hash`` equal
    to the currently active hash; ``retire`` must name the active hash and
    closes it. Any other sequence is a chain violation and fails closed.
    """
    active: dict[tuple[str, str], ActiveLook] = {}
    for row in look_lock_receipts(project_dir, project_id=project_id):
        record = row.get("record") or {}
        key = (str(row.get("entity_kind")), str(row.get("entity_id")))
        action = row.get("action")
        look_hash = row.get("look_hash")
        current = active.get(key)
        if action == "activate":
            if record_sha256(record) != look_hash:
                raise LookIngestError(
                    f"look_lock receipt {row.get('receipt_id')} look_hash does not hash its record"
                )
            supersedes = row.get("supersedes_look_hash")
            if current is None:
                if supersedes is not None:
                    raise LookIngestError(
                        f"look_lock receipt {row.get('receipt_id')} supersedes {supersedes} "
                        f"but no look is active for {key}"
                    )
            elif supersedes != current.look_hash:
                raise LookIngestError(
                    f"look_lock receipt {row.get('receipt_id')} activates a second look for {key} "
                    f"without superseding the active hash {current.look_hash} (got {supersedes!r}) "
                    f"— the chain is monotonic; retire or supersede first"
                )
            active[key] = ActiveLook(key[0], key[1], str(look_hash), str(row.get("receipt_id")), dict(record))
        elif action == "retire":
            if current is None or current.look_hash != look_hash:
                raise LookIngestError(
                    f"look_lock receipt {row.get('receipt_id')} retires {look_hash} for {key} "
                    f"but the active look is {current.look_hash if current else None}"
                )
            del active[key]
        else:
            raise LookIngestError(f"look_lock receipt {row.get('receipt_id')} has action {action!r}")
    return active


def active_look_for(
    project_dir: Path | str, entity_kind: str, entity_id: str, *, project_id: Optional[str] = None
) -> Optional[ActiveLook]:
    return active_looks(project_dir, project_id=project_id).get((entity_kind, entity_id))


def verify_look_refs(
    project_dir: Path | str, look_refs: Iterable[dict], *, project_id: Optional[str] = None
) -> list[dict]:
    """Boundary check for governed visual tools: every ``look_ref``
    ``{entity_kind, entity_id, look_hash}`` must name the ACTIVE look of its
    key, and that look must be generation-sufficient (not shape_only, not a
    minor, attested fictional). Returns the normalized refs; raises
    LookIngestError before any upload otherwise."""
    from lib.receipts import normalize_look_ref

    refs = [normalize_look_ref(r) for r in look_refs]
    if not refs:
        raise LookIngestError("look_refs is empty — a governed visual call names every entity it depicts")
    active = active_looks(project_dir, project_id=project_id)
    for ref in refs:
        key = (ref["entity_kind"], ref["entity_id"])
        current = active.get(key)
        if current is None:
            raise LookIngestError(f"no active look_lock receipt for {key}; ratify the look first")
        if current.look_hash != ref["look_hash"]:
            raise LookIngestError(
                f"look_ref for {key} names look_hash {ref['look_hash']} but the active look is "
                f"{current.look_hash} — the look was superseded; rebuild from the current recipe"
            )
        ok, why = generation_sufficient(current.payload)
        if not ok:
            raise LookIngestError(f"look for {key} cannot drive generation: {why}")
    return refs


LOOK_GOVERNED_STAGE = "look_lock"
DEFAULT_PIPELINE_TYPE = "authored-film"


def project_look_governed(project_root: Path | str, *, project_id: Optional[str] = None) -> bool:
    """True iff the manifest the project is pinned to declares a ``look_lock``
    stage (D13). The pin is ``lib.pipeline_pin.pinned_pipeline`` — the unique
    tip of the signed pipeline_migration chain, falling back to the default
    (1.1) when unpinned — so a legacy project is never governed and a marker
    file can never promote or demote a project on its own. The pipeline name
    comes from ``project.json`` (``pipeline_type``), else ``authored-film``.
    """
    from lib.pipeline_loader import get_stage_order, load_pipeline_readonly
    from lib.pipeline_pin import _read_marker, pinned_pipeline

    root = Path(project_root)
    pipeline_type = str(_read_marker(root).get("pipeline_type") or DEFAULT_PIPELINE_TYPE)
    pin = pinned_pipeline(root, pipeline_type, project_id=project_id)
    try:
        manifest = load_pipeline_readonly(pin.ref)
    except FileNotFoundError:
        # An unversioned pipeline (no <name>@<version>.yaml): its single manifest.
        manifest = load_pipeline_readonly(pin.name)
    return LOOK_GOVERNED_STAGE in get_stage_order(manifest)


# ---- look_packet ----


def ingest_look_tickets(tickets: Mapping[tuple[str, str], Path | str]) -> dict[tuple[str, str], IngestedLook]:
    """Parse one ticket per (entity_kind, entity_id) and require each ticket
    to describe exactly the key it was given."""
    out: dict[tuple[str, str], IngestedLook] = {}
    for key, path in tickets.items():
        look = parse_look_ticket(path)
        if look.key != tuple(key):
            raise LookIngestError(
                f"ticket {Path(path).name} describes {look.key} but was given for {tuple(key)}"
            )
        out[look.key] = look
    return out


def look_lock_request(
    project_dir: Path | str,
    project_id: str,
    look: IngestedLook,
    *,
    request_id: Optional[str] = None,
    promotion_refs: Optional[list[dict]] = None,
    summary: Optional[str] = None,
) -> Path:
    """Write the gate request for a look_lock activate receipt (mints nothing).
    Supersedes the active look for the key when one exists."""
    import json

    project_dir = Path(project_dir)
    current = active_look_for(project_dir, look.entity_kind, look.entity_id)
    if current is not None and current.look_hash == look.look_hash:
        raise LookIngestError(f"look {look.key} with hash {look.look_hash} is already active")
    request_id = request_id or f"look-lock-{look.entity_kind}-{look.entity_id}"[:64]
    request = {
        "request_id": request_id,
        "project_id": project_id,
        "stage": "look_lock",
        "scope": f"{look.entity_kind}:{look.entity_id}",
        "kind": LOOK_LOCK_KIND,
        "entity_id": look.entity_id,
        "artifact": None,
        "approval_record": look.payload,
        "envelope": {
            "action": "activate",
            "entity_kind": look.entity_kind,
            "look_hash": look.look_hash,
            "supersedes_look_hash": current.look_hash if current else None,
            "promotion_refs": list(promotion_refs or []),
            "source_ticket_ref": look.source_ticket_ref,
        },
        "source_checkpoint_digest": None,
        "summary": summary or (
            f"Ratify the look for {look.entity_kind} {look.entity_id!r} (look_hash {look.look_hash}). "
            f"The writer attested a fictional subject: {look.payload.get('fictional_subject_attestation')}. "
            f"Refusal rule: a reference with no receipted lineage, a real person, or a minor is refused."
            + (f" Supersedes active look {current.look_hash}." if current else "")
        ),
        "preview_paths": [],
    }
    req_dir = project_dir / ".gate-requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / f"{request_id}.json"
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return path


def build_look_packet(
    project_dir: Path | str,
    looks: Mapping[tuple[str, str], IngestedLook],
    *,
    project_id: Optional[str] = None,
) -> dict[str, Any]:
    """Emit the ``look_packet`` artifact: one entry per ingested look whose
    hash is the ACTIVE look_lock tip for its key. Any look without an active
    receipt fails — the packet never carries an unratified look."""
    active = active_looks(project_dir, project_id=project_id)
    entries = []
    for key in sorted(looks):
        look = looks[key]
        current = active.get(key)
        if current is None:
            raise LookIngestError(f"look {key} has no active look_lock receipt")
        if current.look_hash != look.look_hash:
            raise LookIngestError(
                f"look {key}: ticket hashes to {look.look_hash} but the active receipt binds "
                f"{current.look_hash} — re-ratify the ticket as resolved today"
            )
        entries.append({
            "entity_kind": look.entity_kind,
            "entity_id": look.entity_id,
            "look_spec": look.payload,
            "look_hash": look.look_hash,
            "receipt_id": current.receipt_id,
            "source_ticket_ref": look.source_ticket_ref,
        })
    return {"version": "1.0", "looks": entries}

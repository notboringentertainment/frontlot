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
   ``{path (relative to the wayfinder root), content_sha256}``.

Ticket authority is confined (Slice A #4): the ticket path is resolved
strictly under the project's configured ``project.yaml: wayfinder_root``
(symlinks rejected) and must sit under ``<root>/wayfinder/resolved/``; the
front matter must carry ``area: look``, ``type: grill``, ``mode: hitl`` and
a non-empty ``resolved:`` claim; and ``depends_on`` is DERIVED from the
ticket's ``blocked-by`` (ids or titles, each proven to be exactly one
resolved ticket under the root; refs carry ``{id?, path, content_sha256}``)
— a block that declares a different ``depends_on`` is rejected.

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


WAYFINDER_ROOT_FIELD = "wayfinder_root"
RESOLVED_SUBDIR = Path("wayfinder") / "resolved"


def wayfinder_root_for(project_dir: Path | str) -> Path:
    """The configured wayfinder root of a project (``project.yaml:
    wayfinder_root``), resolved strictly. Required for every 1.2 project
    that ingests looks — there is no default and no fallback."""
    import yaml

    config = Path(project_dir) / "project.yaml"
    try:
        data = yaml.safe_load(config.read_bytes())
    except OSError as exc:
        raise LookIngestError(f"{config} is required to locate the wayfinder root: {exc}") from exc
    except yaml.YAMLError as exc:
        raise LookIngestError(f"{config} is not YAML: {exc}") from exc
    value = (data or {}).get(WAYFINDER_ROOT_FIELD) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise LookIngestError(
            f"{config} has no {WAYFINDER_ROOT_FIELD} — a 1.2 project must name the story-wayfinder "
            f"project directory whose resolved tickets carry look canon"
        )
    return _resolved_wayfinder_root(value)


def _resolved_wayfinder_root(root: Path | str) -> Path:
    path = Path(root).expanduser()
    if not path.is_absolute():
        raise LookIngestError(f"wayfinder_root must be an absolute path, got {root!r}")
    if path.is_symlink():
        raise LookIngestError(f"wayfinder_root {path} is a symlink — refused")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise LookIngestError(f"wayfinder_root {path} does not exist: {exc}") from exc
    if resolved != path or not resolved.is_dir():
        raise LookIngestError(f"wayfinder_root {path} must be an existing directory reached without symlinks")
    return resolved


def confine_ticket_path(path: Path | str, wayfinder_root: Path | str) -> Path:
    """Strictly resolve ``path`` and require it to be a regular file under
    ``<wayfinder_root>/wayfinder/resolved/`` with no symlink in any component."""
    from lib.pathsafe import PathSafetyError, resolve_input

    root = _resolved_wayfinder_root(wayfinder_root)
    try:
        resolved = resolve_input(path, root)
    except PathSafetyError as exc:
        raise LookIngestError(f"look ticket {path} is not confined under wayfinder root {root}: {exc}") from exc
    resolved_dir = root / RESOLVED_SUBDIR
    try:
        resolved.relative_to(resolved_dir)
    except ValueError:
        raise LookIngestError(
            f"look ticket {resolved} is not under {resolved_dir} — only tickets the wayfinder moved to "
            f"resolved/ are ingested"
        ) from None
    if not resolved.is_file():
        raise LookIngestError(f"look ticket {resolved} is not a regular file")
    return resolved


_HEADER_LINE_RE = re.compile(r"^([a-z][a-z0-9-]*):[ \t]*(.*)$")


def _known_ticket_titles(wayfinder_root: Optional[Path]) -> list[str]:
    """H1 titles of every ticket under ``wayfinder/{resolved,tickets}`` (longest first)."""
    titles: set[str] = set()
    if wayfinder_root is None:
        return []
    for sub in ("resolved", "tickets"):
        d = Path(wayfinder_root) / "wayfinder" / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            try:
                first = f.read_text(encoding="utf-8").splitlines()[0]
            except (OSError, IndexError, UnicodeDecodeError):
                continue
            if first.startswith("# "):
                titles.add(first[2:].strip())
    return sorted(titles, key=len, reverse=True)


def _split_bracket_list(raw: str, path: Path, wayfinder_root: Optional[Path]) -> list[str]:
    """Parse ``[a, b, c]`` where items are ticket titles that may themselves
    contain commas: match known ticket titles greedily (longest first) and
    require them to reconstruct the whole list exactly; fall back to a plain
    comma split when no wayfinder root is known."""
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    inner = inner.strip()
    if not inner:
        return []
    titles = _known_ticket_titles(wayfinder_root)
    if not titles:
        return [x.strip() for x in inner.split(",") if x.strip()]
    found: list[str] = []
    remaining = inner
    progress = True
    while remaining and progress:
        progress = False
        for t in titles:
            if remaining.startswith(t):
                found.append(t)
                remaining = remaining[len(t):].lstrip()
                if remaining.startswith(","):
                    remaining = remaining[1:].lstrip()
                progress = True
                break
    if remaining:
        raise LookIngestError(
            f"look ticket {path.name}: blocked-by entry {remaining[:60]!r} does not match any ticket title "
            f"under the wayfinder root (titles must be exact)"
        )
    return found


def _heading_header(path: Path, text: str, wayfinder_root: Optional[Path]) -> Optional[tuple[dict[str, Any], int]]:
    """The story-wayfinder ticket format: an H1 title line followed by
    ``key: value`` lines until the first blank line or ``## `` heading. No
    ``---`` fences. Returns (meta, body_start) or None if the text does not
    start with an H1."""
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith("# "):
        return None
    meta: dict[str, Any] = {"title": lines[0][2:].strip()}
    pos = len(lines[0])
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("## "):
            break
        m = _HEADER_LINE_RE.match(stripped)
        if not m:
            raise LookIngestError(f"look ticket {path.name}: header line {stripped[:50]!r} is not key: value")
        key, value = m.group(1), m.group(2).strip()
        if key in ("blocked-by", "blocked-by-ids"):
            meta[key] = _split_bracket_list(value, path, wayfinder_root)
        else:
            meta[key] = value if value != "" else None
        pos += len(line)
    return meta, pos


def _front_matter(path: Path, text: str, wayfinder_root: Optional[Path] = None) -> tuple[dict[str, Any], int]:
    fm = _FRONT_MATTER_RE.match(text)
    if not fm:
        heading = _heading_header(path, text, wayfinder_root)
        if heading is not None:
            return heading
        raise LookIngestError(f"look ticket {path.name} has no YAML front matter and no '# Title' header")
    try:
        meta = yaml.safe_load(fm.group(1)) or {}
    except yaml.YAMLError as exc:
        raise LookIngestError(f"look ticket {path.name}: front matter is not YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise LookIngestError(f"look ticket {path.name}: front matter must be a mapping")
    return meta, fm.end()


def _read_ticket_text(path: Path) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LookIngestError(f"cannot read look ticket {path}: {exc}") from exc
    if not _is_utf8(raw):
        raise LookIngestError(f"look ticket {path} is not UTF-8")
    return raw, raw.decode("utf-8")


def _ticket_ref(path: Path, meta: dict[str, Any], raw: bytes, wayfinder_root: Path) -> dict[str, Any]:
    """``{id}`` for a ticket with an immutable id, else ``{path (relative to
    the wayfinder root), content_sha256}`` — the ``source_ticket_ref`` form."""
    ticket_id = meta.get("id")
    if ticket_id is not None:
        if not isinstance(ticket_id, str) or not TICKET_ID_RE.match(ticket_id):
            raise LookIngestError(f"look ticket {path.name}: id {ticket_id!r} must match wf-<8 hex>")
        return {"id": ticket_id}
    return {"path": str(path.relative_to(wayfinder_root)), "content_sha256": hashlib.sha256(raw).hexdigest()}


def _dependency_ref(path: Path, meta: dict[str, Any], raw: bytes, wayfinder_root: Path) -> dict[str, Any]:
    """A ``depends_on`` entry (round 2 #5): always the exact resolved file
    (``path`` relative to the wayfinder root + ``content_sha256``), plus
    ``id`` when the ticket carries an immutable one."""
    ref = _ticket_ref(path, meta, raw, wayfinder_root)
    ref["path"] = str(path.relative_to(wayfinder_root))
    ref["content_sha256"] = hashlib.sha256(raw).hexdigest()
    return {k: ref[k] for k in ("id", "path", "content_sha256") if k in ref}


def _resolved_tickets(resolved_dir: Path, exclude: Path) -> list[tuple[Path, dict[str, Any], bytes]]:
    """Every parseable ticket under ``resolved_dir`` (regular files only,
    symlinks skipped, ``exclude`` skipped) as ``(path, front matter, raw)``."""
    out: list[tuple[Path, dict[str, Any], bytes]] = []
    for candidate in sorted(resolved_dir.rglob("*.md")):
        if candidate == exclude or candidate.is_symlink() or not candidate.is_file():
            continue
        c_raw, c_text = _read_ticket_text(candidate)
        try:
            c_meta, _ = _front_matter(candidate, c_text)
        except LookIngestError:
            continue
        out.append((candidate, c_meta, c_raw))
    return out


def derive_depends_on(path: Path, meta: dict[str, Any], wayfinder_root: Path) -> list[dict[str, Any]]:
    """The ``depends_on`` refs a look block MUST carry, derived from the
    ticket's ``blocked-by`` front matter (Slice A #4, round 2 #5): each
    entry is a ticket id (``wf-<8hex>``) or the exact title of a ticket, and
    EITHER form must resolve to exactly one RESOLVED ticket under
    ``<wayfinder_root>/wayfinder/resolved/`` — an id is matched against the
    ``id:`` front-matter line, a title against ``title:``. The ref carries
    ``{id?, path, content_sha256}`` of that resolved file. A blocker that is
    not exactly one resolved ticket makes this ticket un-ingestible: canon
    cannot depend on an open or unknown decision."""
    blockers = meta.get("blocked-by")
    if blockers is None:
        blockers = []
    if isinstance(blockers, str):
        blockers = [blockers]
    if not isinstance(blockers, list) or not all(isinstance(b, str) and b.strip() for b in blockers):
        raise LookIngestError(f"look ticket {path.name}: blocked-by must be a list of ticket ids or titles")
    refs: list[dict[str, Any]] = []
    resolved_dir = wayfinder_root / RESOLVED_SUBDIR
    tickets = _resolved_tickets(resolved_dir, path) if blockers else []
    for blocker in blockers:
        blocker = blocker.strip()
        by_id = bool(TICKET_ID_RE.match(blocker))
        matches = [
            t for t in tickets
            if (t[1].get("id") == blocker if by_id else str(t[1].get("title", "")).strip() == blocker)
        ]
        if len(matches) != 1:
            raise LookIngestError(
                f"look ticket {path.name}: blocked-by {blocker!r} matches {len(matches)} resolved ticket(s) "
                f"under {resolved_dir} — every blocker must be exactly one resolved ticket"
            )
        c_path, c_meta, c_raw = matches[0]
        refs.append(_dependency_ref(c_path, c_meta, c_raw, wayfinder_root))
    return refs


def parse_look_ticket(path: Path | str, *, wayfinder_root: Path | str) -> IngestedLook:
    """Read one wayfinder look ticket and return its validated look.

    The ticket is confined under ``<wayfinder_root>/wayfinder/resolved/``
    (strict resolution, symlinks rejected), must carry ``area: look``,
    ``type: grill``, ``mode: hitl`` and a non-empty ``resolved:`` claim, and
    its ``depends_on`` is derived from ``blocked-by`` (a declared value that
    disagrees is rejected). Raises LookIngestError for anything short of
    the authority contract.
    """
    root = _resolved_wayfinder_root(wayfinder_root)
    path = confine_ticket_path(path, root)
    raw, text = _read_ticket_text(path)
    text = raw.decode("utf-8", errors="strict") if _is_utf8(raw) else None
    if text is None:
        raise LookIngestError(f"look ticket {path} is not UTF-8")

    meta, body_start = _front_matter(path, text, Path(wayfinder_root) if wayfinder_root is not None else None)
    if meta.get("type") != "grill" or meta.get("mode") != "hitl":
        raise LookIngestError(
            f"look ticket {path.name} is type={meta.get('type')!r} mode={meta.get('mode')!r}; "
            f"only type: grill, mode: hitl tickets can carry canon"
        )
    if meta.get("area") != "look":
        raise LookIngestError(
            f"look ticket {path.name} is area={meta.get('area')!r}; only area: look tickets carry look canon"
        )
    resolved_claim = meta.get("resolved")
    if resolved_claim is None or (isinstance(resolved_claim, str) and not resolved_claim.strip()) or resolved_claim is False:
        raise LookIngestError(
            f"look ticket {path.name} has no non-empty 'resolved:' front matter — the wayfinder marks a "
            f"ticket resolved; an unresolved ticket carries no canon"
        )
    body = text[body_start:]
    sections = _sections(body)
    answer = "".join(sections.get("answer", [])).strip()
    if not sections.get("answer") or not answer:
        raise LookIngestError(f"look ticket {path.name} has no resolved '## Answer' section")
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
    if not isinstance(payload, dict):
        raise LookIngestError(f"look ticket {path.name}: look spec block must be a mapping")
    derived = derive_depends_on(path, meta, root)
    declared_deps = payload.get("depends_on")
    if declared_deps is not None and declared_deps != derived:
        raise LookIngestError(
            f"look ticket {path.name}: the block declares depends_on {declared_deps!r} but the ticket's "
            f"blocked-by derives {derived!r} — depends_on is derived from the wayfinder, never authored"
        )
    payload = dict(payload)
    payload["depends_on"] = derived
    try:
        validate_look_spec(payload)
    except LookSpecError as exc:
        raise LookIngestError(f"look ticket {path.name}: {exc}") from exc

    ref = _ticket_ref(path, meta, raw, root)
    declared = payload.get("source_ticket_ref")
    if "id" in ref:
        if declared is not None and declared != ref:
            raise LookIngestError(
                f"look ticket {path.name}: source_ticket_ref {declared!r} does not name this ticket's id {ref['id']!r}"
            )
    elif declared is not None:
        raise LookIngestError(
            f"look ticket {path.name}: a legacy ticket (no id:) cannot declare source_ticket_ref "
            f"(its content hash is the reference and would be self-referential)"
        )

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


def ingest_look_tickets(
    tickets: Mapping[tuple[str, str], Path | str], *, wayfinder_root: Path | str
) -> dict[tuple[str, str], IngestedLook]:
    """Parse one ticket per (entity_kind, entity_id) under ``wayfinder_root``
    and require each ticket to describe exactly the key it was given."""
    out: dict[tuple[str, str], IngestedLook] = {}
    for key, path in tickets.items():
        look = parse_look_ticket(path, wayfinder_root=wayfinder_root)
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
    ticket_path: Optional[Path | str] = None,
    source_checkpoint_digest: Optional[str] = None,
) -> Path:
    """Write the gate request for a look_lock activate receipt (mints nothing).
    Supersedes the active look for the key when one exists. ``ticket_path``
    (relative to the wayfinder root) lets the gate handler re-ingest the
    ticket itself; the record in the request is only a hint it must match."""
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
        "source_checkpoint_digest": source_checkpoint_digest,
        "source_ticket_path": str(ticket_path) if ticket_path is not None else look.source_ticket_ref.get("path"),
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
    proposal: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Emit the ``look_packet`` artifact: one entry per ingested look whose
    hash is the ACTIVE look_lock tip for its key. Any look without an active
    receipt fails — the packet never carries an unratified look.

    D18: the packet may be PARTIAL (any subset of the cast). When
    ``proposal`` (the proposal_packet) is given, ``complete`` is set to
    whether every ``proposal.cast`` entity has an entry; a partial packet is
    checkpointed ``in_progress`` and can never complete the stage."""
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
    packet: dict[str, Any] = {"version": "1.0", "looks": entries}
    if proposal is not None:
        cast = proposal.get("cast") or {}
        wanted = {("character", c) for c in cast.get("character_ids") or []}
        wanted |= {("location", l) for l in cast.get("location_ids") or []}
        packet["complete"] = wanted <= set(looks)
    return packet

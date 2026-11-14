"""Signed changes to the approved trailer cast.

The trailer cast lives in ``checkpoint_proposal.json`` →
``artifacts.proposal_packet.cast.{character_ids,location_ids}``. That file's
bytes are fingerprinted by signed ``pipeline_migration`` receipts, so it is
never edited. A human-signed ``cast_change`` receipt layers on top instead:

- its record carries the WHOLE resulting cast plus the sha256 of the
  proposal bytes it was signed against (``base_proposal_sha256``), and what
  was taken out / put in;
- the receipts form a supersession chain (envelope ``supersedes_receipt_id``,
  the ``pipeline_migration`` precedent); the unique tip alone defines the cast;
- the tip applies only while the proposal bytes are the ones it was signed
  against. When the proposal itself changes, old amendments no longer apply
  and the cast is the proposal's own.

Every cast reader goes through :func:`effective_cast` /
:func:`with_effective_cast`. Agents only ever *file* a request
(:func:`prepare_cast_change_request`); Ben signs it under Needs you.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

KIND = "cast_change"
ENTITY_ID = "trailer_cast"
SCOPE = "cast:trailer"
STAGE = "proposal"
PROPOSAL_FILE = "checkpoint_proposal.json"
REQUEST_DIRNAME = ".gate-requests"
REQUEST_PREFIX = "cast-change-"
CAST_KEYS = (("character", "character_ids"), ("location", "location_ids"))
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_DECIDED_DIRS = ("done", "declined", "abandoned")


class CastChangeError(RuntimeError):
    """A cast change can't be filed or applied; the message is a plain sentence."""


# ---- the proposal on disk ----


def proposal_sha256(root: Path | str) -> Optional[str]:
    path = Path(root) / PROPOSAL_FILE
    if path.is_symlink() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _disk_packet(root: Path) -> Optional[dict]:
    path = root / PROPOSAL_FILE
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    packet = ((data or {}).get("artifacts") or {}).get("proposal_packet") if isinstance(data, dict) else None
    return packet if isinstance(packet, dict) else None


def base_cast(packet: Any) -> dict[str, list[str]]:
    cast = packet.get("cast") if isinstance(packet, dict) else None
    cast = cast if isinstance(cast, dict) else {}
    return {key: [str(i) for i in cast.get(key) or [] if isinstance(i, str)] for _, key in CAST_KEYS}


# ---- the receipt chain ----


def _may_hold_cast_changes(root: Path) -> bool:
    """Cheap pre-check: a project whose local ledger never mentions the kind
    has no amendment to apply (and no verification cost to pay)."""
    path = root / "approvals.jsonl"
    try:
        return path.is_file() and KIND.encode() in path.read_bytes()
    except OSError:
        return False


def chain_tip(root: Path | str, *, project_id: Optional[str] = None) -> Optional[dict]:
    """The unique tip of the verified cast_change chain (None when empty).
    Fails closed on a fork, a cycle, an unknown predecessor or a receipt off
    the root-to-tip path."""
    from lib.receipts import verified_approvals

    root = Path(root)
    if not _may_hold_cast_changes(root):
        return None
    rows = verified_approvals(root, KIND, entity_id=ENTITY_ID, project_id=project_id)
    if not rows:
        return None
    by_id: dict[str, dict] = {}
    for r in rows:
        rid = r.get("receipt_id")
        if not isinstance(rid, str) or not rid or rid in by_id:
            raise CastChangeError(f"the trailer cast change chain has a duplicate or missing receipt id {rid!r}")
        by_id[rid] = r
    superseded = {r.get("supersedes_receipt_id") for r in rows if r.get("supersedes_receipt_id") is not None}
    tips = [r for r in rows if r["receipt_id"] not in superseded]
    if len(tips) != 1:
        raise CastChangeError(f"the trailer cast change chain has {len(tips)} tips; exactly one is required")
    seen: set[str] = set()
    current: Optional[dict] = tips[0]
    while current is not None:
        rid = current["receipt_id"]
        if rid in seen:
            raise CastChangeError(f"the trailer cast change chain cycles at {rid}")
        seen.add(rid)
        prev = current.get("supersedes_receipt_id")
        if prev is None:
            break
        if prev not in by_id:
            raise CastChangeError(f"cast change receipt {rid} supersedes unknown receipt {prev!r}")
        current = by_id[prev]
    if set(by_id) - seen:
        raise CastChangeError("the trailer cast change chain has receipts off its root-to-tip path")
    return tips[0]


def cast_from_record(record: Any, current_sha: Optional[str]) -> Optional[dict[str, list[str]]]:
    """The cast a cast_change record defines, or None when it does not apply
    (signed against other proposal bytes, or malformed)."""
    if not isinstance(record, dict) or current_sha is None or record.get("base_proposal_sha256") != current_sha:
        return None
    out: dict[str, list[str]] = {}
    for _, key in CAST_KEYS:
        ids = record.get(key)
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return None
        out[key] = list(ids)
    return out


def effective_cast(
    root: Path | str, proposal_packet: Optional[dict] = None, *, project_id: Optional[str] = None
) -> dict[str, list[str]]:
    """The approved trailer cast: the proposal's cast, amended by the tip of
    the signed cast_change chain when that tip was signed against the current
    proposal bytes. ``proposal_packet`` defaults to the one on disk; a packet
    that is not the one on disk (a checkpoint being written) gets no amendment."""
    root = Path(root)
    disk = _disk_packet(root)
    packet = disk if proposal_packet is None else proposal_packet
    base = base_cast(packet)
    if disk is None or packet != disk:
        return base
    tip = chain_tip(root, project_id=project_id)
    if tip is None:
        return base
    return cast_from_record(tip.get("record"), proposal_sha256(root)) or base


def with_effective_cast(
    root: Path | str, proposal_packet: Optional[dict], *, project_id: Optional[str] = None
) -> Optional[dict]:
    """A shallow copy of ``proposal_packet`` whose ``cast`` is the effective cast."""
    if not isinstance(proposal_packet, dict):
        return proposal_packet
    cast = effective_cast(root, proposal_packet, project_id=project_id)
    if cast == base_cast(proposal_packet):
        return proposal_packet
    return dict(proposal_packet, cast=dict(proposal_packet.get("cast") or {}, **cast))


# ---- names and known entities ----


def _canon_packet(root: Path) -> dict:
    path = root / "checkpoint_canon_ingest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    packet = ((data or {}).get("artifacts") or {}).get("canon_packet") if isinstance(data, dict) else None
    return packet if isinstance(packet, dict) else {}


def canon_entities(root: Path | str) -> dict[str, tuple[str, str]]:
    """id -> (kind, name) for every character and place in the approved canon packet."""
    packet = _canon_packet(Path(root))
    out: dict[str, tuple[str, str]] = {}
    for kind, key in (("character", "characters"), ("location", "locations")):
        for entry in packet.get(key) or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                name = entry.get("name") if isinstance(entry.get("name"), str) and entry["name"].strip() else None
                out[entry["id"]] = (kind, name or title_case(entry["id"]))
    return out


def title_case(entity_id: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in re.split(r"[-_]", str(entity_id)) if w)


def _locked_kinds(root: Path, project_id: Optional[str]) -> dict[str, str]:
    """id -> kind for every entity with a verified look_lock activation."""
    from lib.receipts import verified_approvals

    out: dict[str, str] = {}
    for row in verified_approvals(root, "look_lock", project_id=project_id):
        if row.get("action") == "activate" and row.get("entity_kind") in ("character", "location"):
            out[str(row.get("entity_id"))] = str(row["entity_kind"])
    return out


def name_of(entity_id: str, names: dict[str, tuple[str, str]]) -> str:
    return names[entity_id][1] if entity_id in names else title_case(entity_id)


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def summary_sentence(removed: Iterable[str], added: Iterable[str], names: dict[str, tuple[str, str]]) -> str:
    out_names = [name_of(i, names) for i in removed]
    in_names = [name_of(i, names) for i in added]
    if out_names and in_names:
        return f"Take {_join(out_names)} out of the approved trailer cast and put {_join(in_names)} in."
    if out_names:
        return f"Take {_join(out_names)} out of the approved trailer cast."
    return f"Put {_join(in_names)} in the approved trailer cast."


def describe_cast(cast: dict[str, list[str]], names: dict[str, tuple[str, str]]) -> str:
    chars = [name_of(i, names) for i in cast.get("character_ids") or []]
    places = [name_of(i, names) for i in cast.get("location_ids") or []]
    return f"Characters: {', '.join(chars) or 'none'}. Places: {', '.join(places) or 'none'}."


# ---- building a change ----


@dataclass(frozen=True)
class CastChange:
    record: dict[str, Any]
    envelope: dict[str, Any]
    before: dict[str, list[str]]
    tip_receipt_id: Optional[str]
    summary: str
    names: dict[str, tuple[str, str]]


def apply_change(before: dict[str, list[str]], removed: list[dict], added: list[dict]) -> dict[str, list[str]]:
    gone = {(r["kind"], r["id"]) for r in removed}
    after = {key: [i for i in before.get(key) or [] if (kind, i) not in gone] for kind, key in CAST_KEYS}
    keys = dict(CAST_KEYS)
    for a in added:
        after[keys[a["kind"]]].append(a["id"])
    return after


def _ids(values: Any, label: str) -> list[str]:
    values = list(values or [])
    for v in values:
        if not isinstance(v, str) or not _ID_RE.match(v):
            raise CastChangeError(f"{v!r} is not a cast id (lowercase letters, numbers and hyphens)")
    if len(set(values)) != len(values):
        raise CastChangeError(f"the same id is listed twice in {label}")
    return values


def build_cast_change(
    root: Path | str, project_id: str, *, remove: Iterable[str] = (), add: Iterable[str] = ()
) -> CastChange:
    """Validate a change against the CURRENT effective cast and build the
    exact record and envelope that would be signed. Used both when the
    request is filed and by the gate when it rebuilds the record from disk."""
    root = Path(root)
    remove, add = _ids(remove, "the removals"), _ids(add, "the additions")
    if not remove and not add:
        raise CastChangeError("a cast change needs at least one place or character to take out or put in")
    both = sorted(set(remove) & set(add))
    if both:
        raise CastChangeError(f"{both} is listed to take out and to put in, both")
    sha = proposal_sha256(root)
    if sha is None or _disk_packet(root) is None:
        raise CastChangeError("this film has no approved proposal, so it has no trailer cast to change")
    tip = chain_tip(root, project_id=project_id)
    before = effective_cast(root, project_id=project_id)
    names = canon_entities(root)
    in_cast = {i: kind for kind, key in CAST_KEYS for i in before[key]}
    removed: list[dict] = []
    for i in remove:
        if i not in in_cast:
            raise CastChangeError(f"{name_of(i, names)} is not in the approved trailer cast, so it can't be taken out")
        removed.append({"kind": in_cast[i], "id": i})
    added: list[dict] = []
    locked = _locked_kinds(root, project_id) if add else {}
    for i in add:
        if i in in_cast:
            raise CastChangeError(f"{name_of(i, names)} is already in the approved trailer cast")
        canon_kind = names[i][0] if i in names else None
        look_kind = locked.get(i)
        if canon_kind and look_kind and canon_kind != look_kind:
            raise CastChangeError(f"{name_of(i, names)} is a {canon_kind} in canon but its locked look is a {look_kind}")
        kind = canon_kind or look_kind
        if kind is None:
            raise CastChangeError(
                f"Front Lot doesn't know {i!r}: it is not in the approved canon and has no locked look"
            )
        added.append({"kind": kind, "id": i})
    after = apply_change(before, removed, added)
    record = {"base_proposal_sha256": sha, **after, "removed": removed, "added": added}
    tip_id = tip["receipt_id"] if tip else None
    return CastChange(record, {"supersedes_receipt_id": tip_id}, before, tip_id,
                      summary_sentence(remove, add, names), names)


# ---- filing the request ----


def _requests(root: Path) -> tuple[list[Path], list[Path]]:
    base = root / REQUEST_DIRNAME
    pending = sorted(base.glob("*.json")) if base.is_dir() else []
    decided = [p for d in _DECIDED_DIRS if (base / d).is_dir() for p in (base / d).glob("*.json")]
    return pending, decided


def pending_cast_change(root: Path | str) -> Optional[Path]:
    pending, _ = _requests(Path(root))
    for path in pending:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("kind") == KIND:
            return path
    return None


def _next_request_id(root: Path) -> str:
    pending, decided = _requests(root)
    numbers = [0]
    for p in pending + decided:
        if p.stem.startswith(REQUEST_PREFIX) and p.stem[len(REQUEST_PREFIX):].isdigit():
            numbers.append(int(p.stem[len(REQUEST_PREFIX):]))
    return f"{REQUEST_PREFIX}{max(numbers) + 1}"


def prepare_cast_change_request(
    root: Path | str, project_id: str, *, remove: Iterable[str] = (), add: Iterable[str] = ()
) -> Path:
    """Write ``.gate-requests/cast-change-<n>.json`` (mints nothing, signs
    nothing). Ben signs it under Needs you; the gate rebuilds the record from
    disk and refuses if the proposal or the chain moved meanwhile."""
    root = Path(root)
    waiting = pending_cast_change(root)
    if waiting is not None:
        raise CastChangeError(
            "a trailer cast change is already waiting for Ben's signature under Needs you; "
            "it has to be signed or declined first"
        )
    remove, add = list(remove or []), list(add or [])
    built = build_cast_change(root, project_id, remove=remove, add=add)
    request_id = _next_request_id(root)
    request = {
        "request_id": request_id,
        "project_id": project_id,
        "stage": STAGE,
        "scope": SCOPE,
        "kind": KIND,
        "entity_id": ENTITY_ID,
        "artifact": None,
        "cast_change": {"remove": remove, "add": add},
        "approval_record": built.record,
        "envelope": built.envelope,
        "source_checkpoint_digest": None,
        "summary": built.summary,
        "preview_paths": [],
    }
    req_dir = root / REQUEST_DIRNAME
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / f"{request_id}.json"
    from lib.state_io import atomic_write_json

    atomic_write_json(path, request)
    return path

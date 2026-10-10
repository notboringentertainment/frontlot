"""Strict, read-only reader for canon-appeal tickets in a Story-drive project.

WriterOS writes one appeal ticket per changed canon record into the project's
``wayfinder/tickets/`` folder (open) and Front Lot closes it by moving it to
``wayfinder/resolved/``. Front Lot reads them live at spend time, so this
reader never guesses: any folder or file it cannot read, or any appeal it
cannot parse, raises :class:`AppealReadError` with a plain sentence naming the
problem, and the caller stops spending.

Open versus closed is decided by the FOLDER, never by the Answer heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lib.look_ingest import LookIngestError, _resolved_wayfinder_root
from lib.pathsafe import PathSafetyError, resolve_input

CANT_READ_FOLDER = "Front Lot can't read your Story-drive folder, so it can't check for open appeals"
_KINDS = ("overrule", "new")
_HEADER_RE = re.compile(r"^([a-z][a-z0-9-]*):[ \t]*(.*)$")
_ANSWER_RE = re.compile(r"^Answer(?:\s*[—–-]+\s*(.*))?$")


class AppealReadError(RuntimeError):
    """An appeal (or the folder holding appeals) could not be read safely."""


@dataclass(frozen=True)
class Appeal:
    path: Path
    title: str
    kind: str  # "overrule" | "new"
    writeros_record: str
    affects: frozenset[str]  # lower-cased, trimmed
    story_drive_says: str
    writeros_says: str
    outcome: str | None  # None while open; "applied" (or the word found) once closed


def read_appeals(wayfinder_root: Path | str) -> list[Appeal]:
    """Every appeal under ``<root>/wayfinder/{tickets,resolved}``, open and closed."""
    try:
        root = _resolved_wayfinder_root(wayfinder_root)
    except LookIngestError as exc:
        raise AppealReadError(f"{CANT_READ_FOLDER}: {exc}") from exc
    wayfinder = root / "wayfinder"
    try:
        if wayfinder.is_symlink() or not wayfinder.is_dir():
            raise AppealReadError(f"{CANT_READ_FOLDER}: {wayfinder} is missing or is not a plain folder.")
    except OSError as exc:
        raise AppealReadError(f"{CANT_READ_FOLDER}: {exc}") from exc

    appeals: list[Appeal] = []
    for sub, closed in (("tickets", False), ("resolved", True)):
        folder = wayfinder / sub
        try:
            if folder.is_symlink():
                raise AppealReadError(f"{CANT_READ_FOLDER}: {folder} is a link, which Front Lot will not follow.")
            if not folder.exists():
                continue
            if not folder.is_dir():
                raise AppealReadError(f"{CANT_READ_FOLDER}: {folder} is not a folder.")
            names = sorted(p.name for p in folder.iterdir() if p.name.endswith(".md"))
        except OSError as exc:
            raise AppealReadError(f"{CANT_READ_FOLDER}: {exc}") from exc
        for name in names:
            appeal = _read_one(folder / name, root, folder, closed)
            if appeal is not None:
                appeals.append(appeal)
    return appeals


def _read_one(path: Path, root: Path, folder: Path, closed: bool) -> Appeal | None:
    try:
        safe = resolve_input(path, root)
    except PathSafetyError as exc:
        raise AppealReadError(f"Front Lot will not read {path}, so it can't check for open appeals: {exc}") from exc
    if safe.parent != folder or not safe.is_file():
        raise AppealReadError(
            f"Front Lot will not read {path} because it is not a plain file in {folder.name}/, "
            f"so it can't check for open appeals."
        )
    try:
        text = safe.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AppealReadError(f"Front Lot can't read the ticket {safe}, so it can't check for open appeals: {exc}") from exc

    lines = text.splitlines()
    header: dict[str, str] = {}
    title = ""
    idx = 0
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        idx = 1
    while idx < len(lines) and not lines[idx].startswith("## "):
        m = _HEADER_RE.match(lines[idx])
        if m:
            header.setdefault(m.group(1), m.group(2).strip())
        idx += 1
    if header.get("type") != "appeal":
        return None

    def bad(why: str) -> AppealReadError:
        return AppealReadError(f"The appeal {safe} is malformed ({why}), so Front Lot can't check for open appeals.")

    if not title:
        raise bad("it has no title line")
    kind = header.get("appeal", "")
    if kind not in _KINDS:
        raise bad("its 'appeal:' line must say overrule or new")
    record = header.get("writeros-record", "")
    if not record:
        raise bad("it has no 'writeros-record:' line")
    raw_affects = header.get("affects")
    if raw_affects is None or not (raw_affects.startswith("[") and raw_affects.endswith("]")):
        raise bad("its 'affects:' line must be a bracketed list")
    affects = frozenset(a.strip().lower() for a in raw_affects[1:-1].split(",") if a.strip())

    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    answer_word: str | None = None
    for line in lines[idx:]:
        if line.startswith("## "):
            heading = line[3:].strip()
            current = sections.setdefault(heading if not _ANSWER_RE.match(heading) else "Answer", [])
            m = _ANSWER_RE.match(heading)
            if m and m.group(1):
                words = m.group(1).split()
                answer_word = re.sub(r"\W+$", "", words[0]).lower() if words else None
        elif current is not None:
            current.append(line[1:] if line.startswith("\\#") else line)

    def section(name: str) -> str:
        if name not in sections:
            raise bad(f"it has no '## {name}' section")
        return "\n".join(sections[name]).strip()

    outcome: str | None = None
    if closed:
        outcome = answer_word or "closed"
    return Appeal(
        path=safe,
        title=title[len("Appeal:"):].strip() if title.startswith("Appeal:") else title,
        kind=kind,
        writeros_record=record,
        affects=affects,
        story_drive_says=section("Story-drive says"),
        writeros_says=section("WriterOS now says"),
        outcome=outcome,
    )

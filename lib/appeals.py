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

import os
import re
from dataclasses import dataclass
from pathlib import Path

from lib.look_ingest import WAYFINDER_ROOT_FIELD, LookIngestError, _resolved_wayfinder_root, wayfinder_root_for
from lib.pathsafe import PathSafetyError, resolve_input

CANT_READ_FOLDER = "Front Lot can't read your Story-drive folder, so it can't check for open appeals"
_KINDS = ("overrule", "new")
_HEADER_RE = re.compile(r"^([a-z][a-z0-9-]*):[ \t]*(.*)$")
_ANSWER_RE = re.compile(r"^Answer(?:\s*[—–-]+\s*(.*))?$")
# iCloud keeps an evicted file as ".<name>.icloud"; only an evicted ticket
# (``.<name>.md.icloud``) hides an appeal. Other placeholders (an evicted
# backup, say) can't be appeals and are ignored.
_ICLOUD_TICKET_RE = re.compile(r"^\..+\.md\.icloud$")
# WriterOS escapes a line starting with optional indent and "#" as "<indent>\#".
_ESCAPED_HASH_RE = re.compile(r"^([ \t]*)\\#")


class AppealReadError(RuntimeError):
    """An appeal (or the folder holding appeals) could not be read safely."""


class OpenAppealError(RuntimeError):
    """An open appeal touches this paid job; nothing is spent until Ben answers."""

    def __init__(self, message: str, appeal: "Appeal") -> None:
        super().__init__(message)
        self.appeal = appeal


@dataclass(frozen=True)
class Appeal:
    path: Path
    title: str
    kind: str  # "overrule" | "new"
    writeros_record: str
    affects: frozenset[str]  # lower-cased, trimmed
    story_drive_says: str
    writeros_says: str
    outcome: str | None  # None while open; "applied" or "closed" once closed


def read_appeals(wayfinder_root: Path | str, *, include_resolved: bool = True) -> list[Appeal]:
    """Every appeal under ``<root>/wayfinder/{tickets,resolved}``, open and closed.

    With ``include_resolved=False`` only ``tickets/`` (the open appeals) is read;
    ``resolved/`` is never opened, so a closed ticket can't block the caller."""
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
    subs = (("tickets", False), ("resolved", True)) if include_resolved else (("tickets", False),)
    for sub, closed in subs:
        folder = wayfinder / sub
        try:
            if folder.is_symlink():
                raise AppealReadError(f"{CANT_READ_FOLDER}: {folder} is a link, which Front Lot will not follow.")
            if not folder.exists():
                if closed:
                    continue
                raise AppealReadError(
                    f"{CANT_READ_FOLDER}: the folder {folder} is missing, so Front Lot can't confirm there are no open appeals."
                )
            if not folder.is_dir():
                raise AppealReadError(f"{CANT_READ_FOLDER}: {folder} is not a folder.")
            entries = [p.name for p in folder.iterdir()]
            for entry in entries:
                if _ICLOUD_TICKET_RE.match(entry):
                    raise AppealReadError(
                        f"A Story-drive ticket hasn't downloaded from iCloud yet ({entry}), so Front Lot "
                        f"can't check for open appeals. In Finder, right-click the Story-drive folder and "
                        f"choose Download Now."
                    )
            names = sorted(n for n in entries if n.endswith(".md"))
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
            current.append(_ESCAPED_HASH_RE.sub(r"\1#", line))

    def section(name: str) -> str:
        if name not in sections:
            raise bad(f"it has no '## {name}' section")
        return "\n".join(sections[name]).strip()

    outcome: str | None = None
    if closed:
        outcome = "applied" if answer_word == "applied" else "closed"
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


def _name_key(name: str) -> str:
    """One spelling per name: case, spacing, hyphens and underscores don't matter
    (``affects`` carries both look ids like ``vector-dock`` and display names)."""
    return " ".join(re.sub(r"[-_]+", " ", name).lower().split())


def job_names(inputs: dict, verified: dict) -> set[str]:
    """Every name or id a paid job uses: its verified ``look_refs`` entity ids,
    the ``visual_bible_entity_id`` of each ``reference_manifest`` entry, and
    ``inputs["entities"]`` when given. Normalized like :func:`_name_key`."""
    raw: list[object] = [r.get("entity_id") for r in (verified or {}).get("look_refs") or [] if isinstance(r, dict)]
    manifest = inputs.get("reference_manifest")
    if isinstance(manifest, list):
        raw += [m.get("visual_bible_entity_id") for m in manifest if isinstance(m, dict)]
    entities = inputs.get("entities")
    if isinstance(entities, list):
        raw += entities
    return {key for key in (_name_key(n) for n in raw if isinstance(n, str)) if key}


def _first_line(text: str) -> str:
    """First non-blank line, without trailing sentence punctuation, so it reads
    cleanly inside the refusal sentence."""
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line.rstrip(".!?… ")


def check_appeals(project_root: Path, names: set[str]) -> None:
    """Refuse a paid job that an open appeal touches.

    A project with no ``wayfinder_root`` in its project.yaml has no Story-drive
    folder and is skipped. Otherwise the appeals are read live; any read
    failure raises :class:`AppealReadError` (spending stops), and the first
    open appeal (by file name) whose ``affects`` shares a name with ``names``
    raises :class:`OpenAppealError`.
    """
    import yaml

    config = Path(project_root) / "project.yaml"
    try:
        data = yaml.safe_load(config.read_bytes())
    except (OSError, yaml.YAMLError) as exc:
        raise AppealReadError(f"Front Lot can't read {config}, so it can't check for open appeals: {exc}") from exc
    value = data.get(WAYFINDER_ROOT_FIELD) if isinstance(data, dict) else None
    if value is None or (isinstance(value, str) and not value.strip()):
        return
    try:
        root = wayfinder_root_for(project_root)
    except LookIngestError as exc:
        raise AppealReadError(f"{CANT_READ_FOLDER}: {exc}") from exc

    wanted = {_name_key(n) for n in names}
    touching = sorted(
        (a for a in read_appeals(root, include_resolved=False) if wanted & {_name_key(x) for x in a.affects}),
        key=lambda a: a.path.name,
    )
    if not touching:
        return
    first = touching[0]
    message = (
        f"WriterOS changed canon this job uses: {_first_line(first.writeros_says)} "
        f"(Story-drive said: {_first_line(first.story_drive_says)}; ticket {first.path.name}). "
        f"Apply the change before generating? Nothing is spent until you answer."
    )
    others = len(touching) - 1
    if others == 1:
        message += " 1 more open appeal also touches this job."
    elif others > 1:
        message += f" {others} more open appeals also touch this job."
    raise OpenAppealError(message, first)


class ApplyAppealError(RuntimeError):
    """An appeal could not be closed; the message is a plain sentence for Ben."""


_TODAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _step_line(text: str) -> str:
    """The plain-words follow-up written into a closed appeal. Bringing Front
    Lot's references up to date is Ben's own signed step, so this names it and
    never gives command text."""
    if "writeros:looks/" in text:
        next_step = "ratify the updated look in Front Lot so its references match"
    else:
        next_step = "refresh Front Lot's canon from Story-drive so its references match"
    return f"Front Lot uses the WriterOS version from today. Next, {next_step}."


def apply_appeal(wayfinder_root: Path | str, ticket_name: str, today: str) -> tuple[Path, str]:
    """Close an open appeal as applied: set ``resolved:`` to ``today``, replace
    the empty ``## Answer`` heading with ``## Answer — applied (in Front Lot)``
    plus a line naming the step that brings Front Lot's references up to date,
    and move the file from ``tickets/`` to ``resolved/`` under the same name.
    Returns the new path and the step line written into the ticket. Nothing else in the file changes, and WriterOS canon
    is never touched."""
    if not _TODAY_RE.match(today or ""):
        raise ApplyAppealError(f"Front Lot needs today's date as YYYY-MM-DD, got {today!r}.")
    if (
        not isinstance(ticket_name, str)
        or not ticket_name.endswith(".md")
        or ticket_name != Path(ticket_name).name
        or ticket_name.startswith(".")
    ):
        raise ApplyAppealError(
            f"{ticket_name!r} is not a ticket file name. Give just the file name of an open appeal, like appeal-0123456789ab.md."
        )
    appeals = read_appeals(wayfinder_root)  # strict: refuses unreadable folders and malformed appeals
    match = next((a for a in appeals if a.path.name == ticket_name), None)
    if match is None:
        raise ApplyAppealError(f"{ticket_name} is not an appeal in the Story-drive tickets folder, so Front Lot left it alone.")
    if match.outcome is not None or match.path.parent.name != "tickets":
        raise ApplyAppealError(f"{ticket_name} is already closed, so there is nothing to apply.")
    resolved_dir = match.path.parent.parent / "resolved"
    target = resolved_dir / ticket_name
    if os.path.lexists(target):
        raise ApplyAppealError(
            f"Story-drive already has a closed ticket named {ticket_name}, so Front Lot did not overwrite it."
        )
    if resolved_dir.is_symlink() or (resolved_dir.exists() and not resolved_dir.is_dir()):
        raise ApplyAppealError(f"Front Lot can't use {resolved_dir} because it is not a plain folder.")

    original = match.path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    first_section = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    resolved_at = [i for i in range(first_section) if re.match(r"^resolved:[ \t]*(.*)$", lines[i])]
    answer_at = [i for i, l in enumerate(lines) if l.rstrip("\r\n") == "## Answer"]
    if len(resolved_at) != 1 or len(answer_at) != 1:
        raise ApplyAppealError(
            f"{ticket_name} doesn't have exactly one 'resolved:' line and one empty '## Answer' heading, so Front Lot left it alone."
        )
    if any(l.strip() for l in lines[answer_at[0] + 1 :]):
        raise ApplyAppealError(f"{ticket_name} already has text under '## Answer', so Front Lot left it alone.")
    nl = "\r\n" if lines[answer_at[0]].endswith("\r\n") else "\n"
    lines[resolved_at[0]] = f"resolved: {today}{nl}"
    step_line = _step_line(original)
    lines[answer_at[0]] = f"## Answer — applied (in Front Lot){nl}{step_line}{nl}"
    tmp = resolved_dir / f".{ticket_name}.tmp"
    linked = False
    try:
        resolved_dir.mkdir(exist_ok=True)
        tmp.write_text("".join(lines), encoding="utf-8")
        os.link(tmp, target)  # fails if the target exists: never overwrites
        linked = True
        tmp.unlink()
        match.path.unlink()
    except OSError as exc:
        # Undo whatever landed so the open ticket stays the only copy and a retry works.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if linked and match.path.exists():
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise ApplyAppealError(f"Front Lot could not close {ticket_name}: {exc}") from exc
    return target, step_line

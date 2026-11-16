"""Human-readable view of a project's visual canon.

``canon/visual/objects/`` is a content-addressed vault: every generated or
imported image lives there under its sha256, which is what receipts and
lineage checks bind to. It is not browsable by eye. This module derives a
shelf on top of it — ``canon/visual/by-entity/<entity>/<role>.png`` — as
relative symlinks that point INTO the vault. The view is rebuilt from the
project's checkpoints on every call and holds only what is currently
approved:

* ``visual_bible`` entries with ``status: approved`` — hero + every sheet
  view for characters, establishing + angles for locations, and the poster.
* ``headshots`` entries already approved (``approval_receipt_id`` present)
  for entities that have not reached the bible yet — exposed as ``hero.png``.

Nothing here is authoritative: the symlinks are a projection of signed
state, never an input to it. The view directory is cleared and rebuilt on
each run; only symlinks (and the generated ``INDEX.md``) are ever removed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VIEW_DIR = Path("canon/visual/by-entity")
CHARACTER_ROLES = ("turnaround", "expressions", "wardrobe", "front", "three_quarter", "profile", "full_body")


def _read_checkpoint(project_dir: Path, stage: str) -> dict[str, Any] | None:
    path = project_dir / f"checkpoint_{stage}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _ref_path(ref: Any) -> str | None:
    if isinstance(ref, dict) and isinstance(ref.get("path"), str) and ref["path"]:
        return ref["path"]
    return None


def plan_view(project_dir: Path | str) -> list[tuple[str, str]]:
    """Return ``[(link_relative_to_project, target_relative_to_project), ...]``
    for everything currently approved. Pure; touches no files besides reads."""
    project_dir = Path(project_dir)
    links: dict[str, str] = {}

    def add(entity: str, role: str, ref: Any) -> None:
        target = _ref_path(ref)
        if target is None:
            return
        suffix = Path(target).suffix or ".png"
        links[f"{VIEW_DIR}/{entity}/{role}{suffix}"] = target

    bible_cp = _read_checkpoint(project_dir, "visual_bible") or {}
    bible = (bible_cp.get("artifacts") or {}).get("visual_bible") or {}
    in_bible: set[str] = set()
    for ch in bible.get("characters") or []:
        if not isinstance(ch, dict) or ch.get("status") != "approved" or not ch.get("id"):
            continue
        in_bible.add(ch["id"])
        add(ch["id"], "hero", ch.get("hero"))
        for role in CHARACTER_ROLES:
            add(ch["id"], role, (ch.get("sheet") or {}).get(role))
    for loc in bible.get("locations") or []:
        if not isinstance(loc, dict) or loc.get("status") != "approved" or not loc.get("id"):
            continue
        in_bible.add(loc["id"])
        add(loc["id"], "establishing", loc.get("establishing"))
        for i, angle in enumerate(loc.get("angles") or [], start=1):
            add(loc["id"], f"angle_{i}", angle)
    poster = bible.get("poster")
    if isinstance(poster, dict) and poster.get("status") == "approved":
        add("poster", "poster", poster.get("image") or poster)

    hs_cp = _read_checkpoint(project_dir, "headshots") or {}
    packet = (hs_cp.get("artifacts") or {}).get("headshot_packet") or {}
    for entry in packet.get("characters") or []:
        if not isinstance(entry, dict) or not entry.get("approval_receipt_id"):
            continue
        eid = entry.get("entity_id")
        if not eid or eid in in_bible:
            continue
        add(eid, "hero", entry.get("hero"))

    return sorted(links.items())


def build_view(project_dir: Path | str) -> list[tuple[str, str]]:
    """Rebuild ``canon/visual/by-entity`` from the approved state. Returns the
    plan that was materialised. Removes only symlinks and INDEX.md it owns."""
    project_dir = Path(project_dir)
    plan = plan_view(project_dir)
    view_root = project_dir / VIEW_DIR
    if view_root.exists():
        for p in sorted(view_root.rglob("*"), reverse=True):
            if p.is_symlink() or p.name == "INDEX.md":
                p.unlink()
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
    view_root.mkdir(parents=True, exist_ok=True)

    for link_rel, target_rel in plan:
        link = project_dir / link_rel
        link.parent.mkdir(parents=True, exist_ok=True)
        rel_target = os.path.relpath(project_dir / target_rel, link.parent)
        link.symlink_to(rel_target)

    lines = ["# Visual canon — current approved images", "",
             "Derived from signed checkpoints; rebuilt by `scripts/canon_view.py`. Do not edit.", ""]
    current = None
    for link_rel, target_rel in plan:
        entity = Path(link_rel).parent.name
        if entity != current:
            lines += [f"## {entity}", ""]
            current = entity
        lines.append(f"- {Path(link_rel).name} -> {target_rel}")
    (view_root / "INDEX.md").write_text("\n".join(lines) + "\n")
    return plan

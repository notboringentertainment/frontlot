#!/usr/bin/env python3
"""Say where a film stands right now, from the same records the board reads:
what is waiting for Ben under Needs you, the approved trailer cast, and which
looks are locked into the look packet (or signed but not yet finished).

Usage:
  scripts/frontlot_status.py --project <slug>

Read-only: it changes nothing and spends nothing. Front Lot's Claude runs it
before telling Ben what is waiting, signed, finished or in the cast, so it
never answers those from memory of the conversation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.run_common import RunError, read_request, resolve_project_root  # noqa: E402


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def status_lines(root: Path) -> list[str]:
    from backlot.state import _gates_state
    from lib.cast_change import canon_entities, describe_cast, effective_cast, name_of

    names = canon_entities(root)
    lines: list[str] = []

    waiting = [r for r in (_gates_state(root, None) or {}).get("requests") or [] if r.get("state") == "pending"]
    if waiting:
        lines.append("Waiting for Ben under Needs you:")
        lines += [f"- {r.get('summary') or r.get('request_id')}" for r in waiting]
    else:
        lines.append("Nothing is waiting for Ben under Needs you.")

    lines.append("Approved trailer cast (anyone not listed is not in it): "
                 + describe_cast(effective_cast(root, project_id=root.name), names))

    lock = _json(root / "checkpoint_look_lock.json")
    looks = (((lock.get("artifacts") or {}).get("look_packet") or {}).get("looks")) or []
    locked = [name_of(str(l.get("entity_id")), names) for l in looks if isinstance(l, dict)]
    lines.append(f"Looks locked into the look packet: {', '.join(locked) or 'none'}.")
    for entity, st in sorted(((lock.get("metadata") or {}).get("run_state") or {}).items()):
        if not isinstance(st, dict):
            continue
        where, _ = read_request(root, str(st.get("request_id") or ""))
        who = name_of(entity, names)
        if where == "done":
            lines.append(f"Signed but not finished: {who}. Finish it now with the look op (free); it only records what Ben signed.")
        elif where == "pending":
            lines.append(f"Look waiting for Ben's signature: {who}.")
        elif where == "declined":
            lines.append(f"Look declined by Ben: {who}. The look op records his note.")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    a = ap.parse_args(argv)
    try:
        root = resolve_project_root(a.project)
    except RunError as exc:
        print(f"frontlot_status: {exc}", file=sys.stderr)
        return 1
    print("\n".join(status_lines(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

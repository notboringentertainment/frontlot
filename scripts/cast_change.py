#!/usr/bin/env python3
"""File a request to change the film's approved trailer cast.

Usage:
  scripts/cast_change.py --project <slug> [--remove <id> ...] [--add <id> ...]

Files ``.gate-requests/cast-change-<n>.json`` and nothing else: it signs
nothing, edits no checkpoint (the proposal file is fingerprinted and stays
as it is) and spends nothing. Ben signs the change under Needs you; only then
does the trailer cast change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.cast_change import CastChangeError, prepare_cast_change_request  # noqa: E402
from lib.receipts import ReceiptError  # noqa: E402
from lib.run_common import RunError, resolve_project_root  # noqa: E402


def _project_id(root: Path) -> str:
    import json

    try:
        data = json.loads((root / "project.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("project_id"), str) and data["project_id"]:
            return data["project_id"]
    except (OSError, ValueError):
        pass
    return root.name


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--remove", action="append", default=[], metavar="ID", help="take this id out of the trailer cast")
    ap.add_argument("--add", action="append", default=[], metavar="ID", help="put this id in the trailer cast")
    ap.add_argument("--projects-dir", type=Path, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        root = resolve_project_root(a.project, a.projects_dir)
        path = prepare_cast_change_request(root, _project_id(root), remove=a.remove, add=a.add)
    except (RunError, CastChangeError, ReceiptError) as exc:
        print(f"cast_change: {exc}", file=sys.stderr)
        return 1
    import json

    summary = json.loads(path.read_text(encoding="utf-8"))["summary"]
    print(f"Asked for: {summary}")
    print("Nothing has changed yet. The request is waiting under Needs you; the cast changes only when Ben signs it there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

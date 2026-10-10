#!/usr/bin/env python3
"""Rebuild the browsable per-entity view of a project's approved visual canon.

Usage: scripts/canon_view.py --project <slug> [--open]

Writes canon/visual/by-entity/<entity>/<role>.png symlinks (into the
content-addressed objects vault) plus INDEX.md. Only approved images appear.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.canon_view import VIEW_DIR, build_view  # noqa: E402
from lib.checkpoint import PROJECTS_DIR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--open", action="store_true", help="open the view folder in Finder (macOS)")
    a = ap.parse_args()
    project_dir = Path(PROJECTS_DIR) / a.project
    if not (project_dir / "project.yaml").is_file():
        print(f"no project at {project_dir}", file=sys.stderr)
        return 2
    plan = build_view(project_dir)
    root = project_dir / VIEW_DIR
    print(f"{root}: {len(plan)} approved images")
    for link, target in plan:
        print(f"  {Path(link).relative_to(VIEW_DIR)}  ->  {target}")
    if a.open and sys.platform == "darwin":
        subprocess.run(["open", str(root)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read today's canon from the film's Story-drive folder and say what changed
since the approved canon snapshot.

Usage:
  scripts/canon_check.py --project <slug>

Read-only: it changes no canon, no checkpoint and no Story-drive file, and
spends nothing. Mismatches with approved work are reported for Ben to settle
in his own tools.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.canon_fresh import CanonReadError, read_canon_changes  # noqa: E402
from lib.run_common import RunError, resolve_project_root  # noqa: E402


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    a = ap.parse_args(argv)
    try:
        changes = read_canon_changes(resolve_project_root(a.project))
    except (RunError, CanonReadError) as exc:
        print(f"canon_check: {exc}", file=sys.stderr)
        return 1
    if changes is None:
        print("This film has no Story-drive folder set up, so there is no live canon to read.")
        return 0
    print(f"Approved canon snapshot: {changes.snapshot_date}.")
    if changes.unchanged:
        print("Story-drive canon has not changed since then.")
        return 0
    for m in changes.mismatches:
        print(f"Mismatch: {m.message}")
    if changes.new_decisions:
        print("Decided in Story-drive since the snapshot:")
        for d in changes.new_decisions:
            note = " (scoped out)" if d.scoped_out else ""
            print(f"- {d.resolved}: {d.title}{note} [wayfinder/resolved/{d.file}]")
    if changes.changed_sources:
        print("Canon files changed since the snapshot:")
        for s in changes.changed_sources:
            print(f"- {s.path} ({s.state})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

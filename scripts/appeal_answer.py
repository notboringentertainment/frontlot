#!/usr/bin/env python3
"""Apply a canon appeal: Ben said yes to "Apply the change before generating?".

Usage:
  scripts/appeal_answer.py --project <slug> --ticket appeal-<id>.md

Closes the open appeal in the project's Story-drive folder (moves it from
wayfinder/tickets/ to wayfinder/resolved/ as applied). It changes no WriterOS
canon and spends nothing; bringing Front Lot's references up to date stays the
separate step the closed ticket names.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.appeals import ApplyAppealError, AppealReadError, apply_appeal  # noqa: E402
from lib.look_ingest import LookIngestError, wayfinder_root_for  # noqa: E402
from lib.run_common import RunError, resolve_project_root  # noqa: E402


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--ticket", required=True, help="file name of the open appeal, like appeal-0123456789ab.md")
    a = ap.parse_args(argv)
    try:
        root = resolve_project_root(a.project)
        try:
            wayfinder = wayfinder_root_for(root)
        except LookIngestError as exc:
            raise ApplyAppealError(
                f"This project has no Story-drive folder set up, so there is no appeal to apply here: {exc}"
            ) from exc
        closed, step = apply_appeal(wayfinder, a.ticket, date.today().isoformat())
    except (RunError, ApplyAppealError, AppealReadError) as exc:
        print(f"appeal_answer: {exc}", file=sys.stderr)
        return 1
    print(f"Applied: {closed.name} is closed. WriterOS canon is unchanged.")
    print(step)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

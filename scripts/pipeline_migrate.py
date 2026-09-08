#!/usr/bin/env python3
"""S0 — publish the pipeline migration request a human then signs.

Usage:
  scripts/pipeline_migrate.py --project <slug> --to 1.6
  scripts/pipeline_migrate.py --project <slug> --to 1.6 --pipeline authored-film

The shot-ready rule (authored-film 1.6) is adopted per project by one signed
``pipeline_migration`` receipt. Writing the request for it was, until now, a
library call with no command behind it (``lib.pipeline_pin.prepare_migration_request``),
so the one mandatory setup step of the shot-ready plan had no way to be run.
This is that command, and it is nothing more: it writes the gate request and
prints the id and the command that signs it.

It decides nothing and mints nothing. No ``--yes``, no environment bypass, no
non-TTY signing: Ben signs the request on the board, or in a terminal with the
printed ``gate_sign`` command. If the project is not quiet enough to migrate
(a gate request still pending, a run parked mid-flight), the request is never
written and this command exits non-zero with the blockers it was given.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, TextIO

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.pipeline_pin import PipelinePinError, prepare_migration_request  # noqa: E402
from lib.run_common import RunError, gate_command, resolve_project_root  # noqa: E402

DEFAULT_PIPELINE = "authored-film"


def request_migration(
    root: Path,
    version: str,
    *,
    pipeline_name: str = DEFAULT_PIPELINE,
    out: Optional[TextIO] = None,
) -> str:
    """Write the migration gate request for ``pipeline_name@version``.

    Returns the request id. Raises ``PipelinePinError`` — carrying the
    coverage blockers — when the project may not be pinned to this version.
    """
    path = prepare_migration_request(root, root.name, pipeline_name, version)
    request_id = path.stem
    if out is not None:
        print(
            f"published {request_id}: pin {root.name} to {pipeline_name}@{version}.\n"
            f"Nothing is signed yet. Ben signs it on the board, or: {gate_command(root, request_id)}",
            file=out,
        )
    return request_id


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="project slug")
    ap.add_argument("--to", required=True, dest="version", help="manifest version to pin, e.g. 1.6")
    ap.add_argument("--pipeline", default=DEFAULT_PIPELINE, help=f"pipeline name (default {DEFAULT_PIPELINE})")
    args = ap.parse_args(argv)
    try:
        root = resolve_project_root(args.project)
        request_migration(root, args.version, pipeline_name=args.pipeline, out=sys.stdout)
    except (RunError, PipelinePinError) as exc:
        print(f"pipeline_migrate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

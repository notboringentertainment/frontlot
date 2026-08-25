#!/usr/bin/env python3
"""Reconcile nonterminal paid-call reservations against the provider (PLAN §0, inspection #4/#5).

    python scripts/reconcile_paid_calls.py --project <slug>

For every reservation that is not ``completed``/``failed``:

- with a provider request id: GET the FAL status URL (fixture pattern, never a
  server-supplied URL). ``COMPLETED`` → if the reservation's ``output_hint``
  names an output that is missing, download it through the hardened helper
  (host allowlist, MIME/size caps, ffprobe for video, content-addressed store
  for images) and write its generation receipt; then mark ``completed`` at the
  reserved amount. ``FAILED``/``CANCELLED``/``ERROR`` → mark ``failed`` (refund).
  Still queued/running → left alone and reported.
- ``submitting`` with no request id: cannot be resolved by polling; the script
  prints what the human must check in the FAL dashboard and leaves it.

Read-only towards the provider: this script NEVER resubmits and never mints a
gate token or approval. The project must be registered under PROJECTS_DIR with
a human-approved ``project.yaml`` (that is where the budget tracker comes from).
No TTY is required.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib import paths  # noqa: E402  (PROJECTS_DIR read at call time)

VIDEO_MAX_BYTES = 512 * 1024 * 1024
IMAGE_MAX_BYTES = 64 * 1024 * 1024
LEAVE_ALONE = ("IN_QUEUE", "IN_PROGRESS")


class ReconcileError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fal_queue_status(model_id: str, request_id: str, *, api_key: str) -> dict[str, Any]:
    """GET the status body for one request (fixture URL pattern)."""
    import requests

    from tools.video import _shared

    resp = requests.get(
        _shared.fal_request_url(model_id, request_id, "status"),
        headers=_shared._fal_headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json() if resp.content else {}
    return body if isinstance(body, dict) else {}


def _record_receipt(project_root: Path, reservation: dict[str, Any], output_sha256: str) -> None:
    from lib.receipts import record_generation

    record_generation(
        project_root,
        execution_id=f"reconcile-{reservation['reservation_id']}",
        tool=str(reservation.get("tool") or "unknown"),
        model_endpoint=str(reservation.get("endpoint") or ""),
        provider_request_id=str(reservation.get("provider_request_id") or ""),
        normalized_inputs_hash=str(reservation.get("normalized_inputs_hash") or ""),
        output_sha256=output_sha256,
        cost_usd=float(reservation.get("reserved_usd") or 0.0),
        started_at=str(reservation.get("updated_at") or _now()),
        finished_at=_now(),
        generator_kind="model",
    )


def recover_output(project_root: Path, reservation: dict[str, Any], *, api_key: str) -> list[str]:
    """Download a COMPLETED job's output when the reservation says where it belongs.

    Returns the recovered paths (empty when nothing was needed).
    """
    from lib import pathsafe
    from lib.state_io import atomic_move
    from tools.video import _shared

    hint = reservation.get("output_hint") or {}
    kind = hint.get("kind")
    model_id, request_id = reservation["endpoint"], reservation["provider_request_id"]
    recovered: list[str] = []
    if kind == "video" and hint.get("output_path"):
        output_path = pathsafe.validate_output_parent(hint["output_path"], project_root)
        if output_path.exists():
            return recovered
        data = _shared.fal_queue_wait(model_id, request_id, api_key=api_key, deadline_s=60.0, poll_s=1.0)
        staging = pathsafe.staging_file(project_root, ".mp4")
        try:
            _shared.fal_download(
                data["video"]["url"], staging, max_bytes=VIDEO_MAX_BYTES,
                allowed_mime_prefixes=("video/", "application/octet-stream"),
            )
            _shared.verify_video_file(staging, require_audio=bool(hint.get("generate_audio", False)))
            atomic_move(staging, output_path)
        finally:
            try:
                staging.unlink()
            except FileNotFoundError:
                pass
        _record_receipt(project_root, reservation, pathsafe.sha256_file(output_path))
        recovered.append(str(output_path))
    elif kind == "image" and hint.get("objects_dir"):
        objects_dir = Path(hint["objects_dir"]).resolve()
        objects_dir.relative_to(project_root)  # ValueError when outside the project
        objects_dir.mkdir(parents=True, exist_ok=True)
        objects_dir = pathsafe.validate_output_parent(objects_dir / "x", project_root).parent
        data = _shared.fal_queue_wait(model_id, request_id, api_key=api_key, deadline_s=60.0, poll_s=1.0)
        for item in data.get("images") or []:
            staging = pathsafe.staging_file(project_root, ".img")
            try:
                _shared.fal_download(item["url"], staging, max_bytes=IMAGE_MAX_BYTES, allowed_mime_prefixes=("image/",))
                asset_id, final_path = pathsafe.store_content_addressed(staging, objects_dir, ".png")
            finally:
                try:
                    staging.unlink()
                except FileNotFoundError:
                    pass
            _record_receipt(project_root, reservation, asset_id)
            recovered.append(str(final_path))
    return recovered


def reconcile_project(project_root: Path, *, api_key: str | None, out=None) -> dict[str, list[str]]:
    """Reconcile every nonterminal reservation. Returns ids grouped by outcome."""
    from tools.cost_tracker import nonterminal_reservations, reconcile_paid_call
    from tools.video import _shared

    out = out or sys.stdout
    project_root, tracker, _config = _shared.paid_call_context(
        {"project_dir": str(project_root)}, check_resume=False  # reconciling IS the resume path
    )
    summary: dict[str, list[str]] = {"completed": [], "failed": [], "running": [], "manual": []}
    pending = nonterminal_reservations(project_root)
    if not pending:
        print("nothing to reconcile: every reservation is terminal", file=out)
        return summary
    for r in pending:
        rid = r["reservation_id"]
        if not r.get("provider_request_id"):
            summary["manual"].append(rid)
            print(
                f"[manual] {rid}: state=submitting, no provider request id, endpoint={r.get('endpoint')}, "
                f"reserved=${r.get('reserved_usd')}. Check the FAL dashboard for a request matching "
                f"tool={r.get('tool')} around {r.get('updated_at')}; if none exists, record it failed by hand "
                f"(reconcile_paid_call(..., state='failed')). Never resubmit.",
                file=out,
            )
            continue
        if not api_key:
            raise ReconcileError("FAL_KEY is required to poll reservations that carry a request id")
        status = str(fal_queue_status(r["endpoint"], r["provider_request_id"], api_key=api_key).get("status", "UNKNOWN")).upper()
        if status == "COMPLETED":
            recovered = recover_output(project_root, r, api_key=api_key)
            reconcile_paid_call(project_root, rid, float(r.get("reserved_usd") or 0.0), "completed", tracker)
            summary["completed"].append(rid)
            print(f"[completed] {rid}: request {r['provider_request_id']} completed; "
                  f"{'recovered ' + ', '.join(recovered) if recovered else 'output already present'}", file=out)
        elif status in _shared.FAL_TERMINAL_FAILURES:
            reconcile_paid_call(project_root, rid, 0.0, "failed", tracker)
            summary["failed"].append(rid)
            print(f"[failed] {rid}: request {r['provider_request_id']} {status.lower()}; reservation refunded", file=out)
        elif status in LEAVE_ALONE:
            summary["running"].append(rid)
            print(f"[running] {rid}: request {r['provider_request_id']} is {status}; re-run later", file=out)
        else:
            summary["running"].append(rid)
            print(f"[unknown] {rid}: provider status {status!r}; left as {r.get('state')}", file=out)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="project slug under PROJECTS_DIR")
    args = parser.parse_args(argv)
    project_root = Path(paths.PROJECTS_DIR) / args.project
    if not project_root.is_dir():
        print(f"error: no project {args.project!r} under {paths.PROJECTS_DIR}", file=sys.stderr)
        return 2
    api_key = os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY")
    try:
        summary = reconcile_project(project_root, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if not summary["manual"] and not summary["running"] else 3


if __name__ == "__main__":
    sys.exit(main())

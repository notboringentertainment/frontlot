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

Before polling, incomplete generation-WAL entries (an output staged but not
receipted / ledgered / reconciled because of a crash) are replayed
(``lib.receipts.recover_generation_wal``); an entry whose output is missing
is reported for manual recovery. An existing output is verified and
receipted rather than skipped.

Read-only towards the provider: this script NEVER resubmits and never mints a
gate token or approval. The project must be registered under PROJECTS_DIR with
a human-approved ``project.yaml`` (that is where the budget tracker comes from).
No TTY is required.
"""
from __future__ import annotations

import argparse
import requests
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
    from lib.receipts import find_generation
    from lib.state_io import atomic_move
    from tools.video import _shared

    hint = reservation.get("output_hint") or {}
    kind = hint.get("kind")
    model_id, request_id = reservation["endpoint"], reservation["provider_request_id"]
    recovered: list[str] = []
    if kind == "video" and hint.get("output_path"):
        output_path = pathsafe.validate_output_parent(hint["output_path"], project_root)
        if output_path.exists():
            # An output that survived the crash is verified and receipted, never
            # skipped: a paid file without a receipt is inadmissible forever.
            _shared.verify_video_file(output_path, require_audio=bool(hint.get("generate_audio", False)))
            sha = pathsafe.sha256_file(output_path)
            if find_generation(project_root, sha) is None:
                _record_receipt(project_root, reservation, sha)
                recovered.append(str(output_path))
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


QC_GRACE_SECONDS = 15 * 60


def reconcile_qc(project_root: Path, tracker: Any, summary: dict[str, list[str]], *, out=None) -> set[str]:
    """Resolve every QC WAL row of this project. Returns the reservation ids
    it handled (so the FAL loop skips them). Rows WITH a provider id are
    fetched and committed/voided; rows WITHOUT one are voided after the grace
    period (bounded loss, never a pass)."""
    from datetime import datetime, timezone

    from lib import gates
    from lib.project_config import load_verified_project_config
    from tools.qa.sheet_judge import OpenAIResponsesAdapter, commit_result, void_claim

    out = out or sys.stdout
    handled: set[str] = set()
    root = Path(project_root).resolve()
    rows = [w for w in gates.qc_wal_entries() if str(w.get("project_root")) == str(root)]
    if not rows:
        return handled
    config = load_verified_project_config(root)
    qc = config.qc
    for w in rows:
        rid = w.get("reservation_id")
        if rid:
            handled.add(rid)
        tuple_sha = w["tuple_sha256"]
        state = w.get("state")
        pid_ = w.get("provider_request_id")
        if not pid_ and rid:
            # cross-fill from the reservation log (round 2 #2): the id may have landed there only
            from tools.cost_tracker import load_reservations
            pid_ = (load_reservations(root).get(rid) or {}).get("provider_request_id")
            if pid_:
                w = dict(w, provider_request_id=pid_, state="submitted")
                gates.qc_wal_write(tuple_sha, w)
        if state == "voided_unconfirmed":
            gates.qc_wal_delete(tuple_sha)
            continue
        if pid_ and qc is not None and qc.judge_provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                summary["manual"].append(f"qc:{tuple_sha[:12]}")
                print(f"[manual] qc {tuple_sha[:12]}: provider id {pid_} but OPENAI_API_KEY is not set", file=out)
                continue
            try:
                result = OpenAIResponsesAdapter(key).wait(pid_, deadline_s=60, poll_s=3)
            except TimeoutError:
                summary["running"].append(f"qc:{tuple_sha[:12]}")
                print(f"[running] qc {tuple_sha[:12]}: response {pid_} still in progress; re-run later", file=out)
                continue
            if result.get("status") == "completed":
                row, sc, _ = commit_result(root, w, result, tracker)
                summary["completed"].append(rid or tuple_sha[:12])
                print(f"[completed] qc {tuple_sha[:12]}: verdict {sc['verdict']} recorded as {row['receipt_id']}", file=out)
            else:
                void_claim(root, w, tracker, reason=f"provider status {result.get('status')}")
                summary["failed"].append(rid or tuple_sha[:12])
                print(f"[failed] qc {tuple_sha[:12]}: provider status {result.get('status')}; claim voided, attempt stays open", file=out)
            continue
        claimed_at = w.get("claimed_at")
        age = None
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(claimed_at))).total_seconds()
        except (TypeError, ValueError):
            pass
        if age is not None and age < QC_GRACE_SECONDS:
            summary["running"].append(f"qc:{tuple_sha[:12]}")
            print(f"[running] qc {tuple_sha[:12]}: {state} without a provider id for {int(age)}s; grace is {QC_GRACE_SECONDS}s", file=out)
            continue
        void_claim(root, w, tracker, reason=f"{state} with no provider id after grace")
        summary["failed"].append(rid or tuple_sha[:12])
        print(f"[voided] qc {tuple_sha[:12]}: {state} with no provider id after the grace period; reservation settled "
              f"as spent (bounded loss of one judge call), tuple released, attempt left open", file=out)
    return handled


def reconcile_project(project_root: Path, *, api_key: str | None, out=None) -> dict[str, list[str]]:
    """Reconcile every nonterminal reservation. Returns ids grouped by outcome."""
    from tools.cost_tracker import nonterminal_reservations, reconcile_paid_call
    from tools.video import _shared

    from lib.receipts import GenerationWalError, recover_generation_wal

    out = out or sys.stdout
    project_root, tracker, _config = _shared.paid_call_context(
        {"project_dir": str(project_root)}, check_resume=False  # reconciling IS the resume path
    )
    summary: dict[str, list[str]] = {"completed": [], "failed": [], "running": [], "manual": [], "replayed": []}
    # Generation WAL first: a crash between a staged output and its receipt /
    # ledger / terminal reservation is finished here (idempotent). An entry
    # whose output is missing is reported and left for the human.
    def _report_replayed(receipts: list[dict[str, Any]]) -> None:
        for receipt in receipts:
            summary["replayed"].append(receipt["receipt_id"])
            print(f"[replayed] execution {receipt['execution_id']}: receipt {receipt['receipt_id']} "
                  f"for output {receipt['output_sha256']} completed from the generation WAL", file=out)

    try:
        _report_replayed(recover_generation_wal(project_root))
    except GenerationWalError as exc:
        _report_replayed(exc.recovered)
        summary["manual"].append("generation-wal")
        print(f"[manual] generation WAL: {exc}", file=out)
    # D19: judge calls (sheet_judge) reconcile through the QC WAL, never by resubmission.
    qc_reservations = reconcile_qc(project_root, tracker, summary, out=out)
    pending = [r for r in nonterminal_reservations(project_root) if r["reservation_id"] not in qc_reservations]
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
            try:
                recovered = recover_output(project_root, r, api_key=api_key)
            except requests.HTTPError as exc:
                code = getattr(exc.response, "status_code", None)
                if code in (400, 422):
                    # Queue says COMPLETED but the result is a validation/content-policy
                    # rejection: nothing was rendered or billed. Refund the reservation.
                    detail = (exc.response.text or "")[:300]
                    reconcile_paid_call(project_root, rid, 0.0, "failed", tracker)
                    summary["failed"].append(rid)
                    print(f"[failed] {rid}: request {r['provider_request_id']} rejected by provider "
                          f"({code}); reservation refunded. detail: {detail}", file=out)
                    continue
                raise
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
    try:
        from lib.env_loader import load_env

        load_env()
    except Exception:  # noqa: BLE001 — env file is optional; FAL_KEY may already be exported
        pass
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

#!/usr/bin/env python3
"""D19.5 — make a character's sheet: generate → judge → retry within the signed
cap → write the awaiting_human draft and the sheet gate request. Never presents
a failing sheet; never edits the prompt; never mints anything.

Usage:
  scripts/sheet_run.py --project <slug> --entity <id> [--roles turnaround,expressions[,wardrobe]]
                       [--max-attempts N] [--palette "a,b,c"] [--resume | --finish | --abandon] [--open]

Governed end to end: the project's pinned manifest must be authored-film 1.3
and its signed 1.1 config names the judge and the attempt cap
(``--max-attempts`` may only LOWER it). Every generated candidate is a signed
``attempt_started`` row (committed inside the paid tool's pre-submit hook, so
a crash after reservation still counts); every verdict is a signed QC
receipt; the draft carries ``qc_receipts`` per role and canon enforcement
refuses the write unless every role passes (or a human signed a qc_override).

Finish (D20 follow-up F0): the run keeps its state in the visual_bible
checkpoint metadata (``run_state``, ``sheet_prev_entry``, ``rejected_sheets``,
``sheet_revisions``). With run state present, every invocation resumes: a
pending request prints the gate command; a declined one restores the previous
entry and remembers the rejected assets; a missing one is republished; a done
one is finished (the entry flips to ``approved`` bound to the exact receipt).
``--abandon`` retires a done request whose checkpoint went stale after signing.

D21 step 0: the transaction discipline on that shared checkpoint — the single
writer, the write→digest→request order, the finish sequence, ``--abandon``,
decline handling, and single-flight against the location runner under either
run-state key — lives in ``lib.visual_entry``. This command is a thin caller
that supplies only what is specific to a character sheet.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib import visual_entry as ve  # noqa: E402
from lib.run_common import (  # noqa: E402
    REQUEST_DIRNAME, RunError, request_id_for, require_entity_id, resolve_project_root,
)
from lib.sheet_qc.verify import MANDATORY_ROLES  # noqa: E402

STAGE = ve.STAGE
EXIT_BLOCKED = 3
EXIT_DECLINED = 4
GENERATION_PRICE_USD = {"turnaround": 0.14, "expressions": 0.07, "wardrobe": 0.07}
# Explicit sizes: the local pre-check enforces landscape 3:2 for the 2x3 expression grid, so ask for it.
IMAGE_SIZE = {"turnaround": {"width": 2560, "height": 1600}, "expressions": {"width": 1536, "height": 1024}, "wardrobe": {"width": 1536, "height": 1024}}

# ``lib.visual_entry`` owns the transaction discipline on the shared visual_bible
# checkpoint and the errors it raises; sheet_run raises the same class so a
# caller sees one failure mode whichever layer refused.
SheetRunError = ve.VisualEntryError
Declined = ve.Declined


class Blocked(SheetRunError):
    """A role exhausted its attempts; an override request was written (or,
    for a human-rejected series, nothing is left to override)."""


def _log(out, msg: str) -> None:
    print(msg, file=out or sys.stdout)


# ---- generation (default: governed Seedream edit; tests inject a fake) ----

def default_generate(root: Path, role: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """Generate one sheet view through the governed Seedream path. Returns
    ``(asset_id, generation_receipt_id)``. The pre-submit hook installed by
    the run records the attempt row inside the tool."""
    from lib.receipts import find_generation
    from tools import prompt_builder as pb
    from tools.graphics.seedream_image import SeedreamImage

    look, head = ctx["look"], ctx["headshot"]
    built = pb.build_prompt(look.payload, role=role, palette=ctx["palette"])
    hero_path = root / "canon" / "visual" / "objects" / f"{head.asset_id}.png"
    inputs = {
        "operation": "edit", "prompt": built["prompt"], "prompt_recipe": built["prompt_recipe"],
        "asset_role": role, "stage": "visual_bible",
        "look_refs": [{"entity_kind": look.entity_kind, "entity_id": look.entity_id, "look_hash": look.look_hash}],
        "headshot_ref": {"entity_id": look.entity_id, "asset_id": head.asset_id, "approval_receipt_id": head.receipt_id},
        "reference_image_paths": [str(hero_path)],
        "reference_manifest": [{"asset_id": head.asset_id, "path": str(hero_path), "role": "hero", "visual_bible_entity_id": look.entity_id}],
        "palette": ctx["palette"], "image_size": IMAGE_SIZE[role], "num_images": 1, "output_format": "png",
        "project_dir": str(root),
    }
    r = SeedreamImage().execute(inputs)
    if not r.success:
        raise SheetRunError(f"generation failed for {role}: {r.error}")
    path = Path(r.data.get("output_path") or (r.data.get("output_paths") or [""])[0])
    asset_id = path.stem
    gen = find_generation(root, asset_id)
    if gen is None:
        raise SheetRunError(f"no verified generation receipt for {asset_id}")
    return asset_id, gen["receipt_id"]


# ---- the run ----

def run_sheet(
    project_root: Path | str, entity_id: str, *, roles: Optional[list[str]] = None, max_attempts: Optional[int] = None,
    palette: Optional[list[str]] = None, resume: bool = False, finish: bool = False, abandon: bool = False,
    open_images: bool = False,
    generate: Optional[Callable[[Path, str, dict[str, Any]], tuple[str, str]]] = None,
    judge_adapter: Any = None, out=None,
) -> dict[str, Any]:
    from lib import qc_receipts as qr
    from lib.canon_enforcement import _is_qc_manifest, character_approval_record
    from lib.config_model import BudgetMode
    from lib.headshots import active_headshots
    from lib.look_ingest import active_look_for
    from lib.pipeline_pin import _read_marker, pinned_pipeline
    from lib.project_config import load_verified_project_config
    from lib.receipts import find_generation
    from lib.sheet_qc import policy
    from tools import prompt_builder as pb
    from tools.cost_tracker import CostTracker, resume_check
    from tools.qa.sheet_judge import DEFAULT_RESERVE_USD, SheetJudge
    from tools.video import _shared

    out = out or sys.stdout
    root = Path(project_root).resolve()
    project_id = root.name
    generate = generate or default_generate
    require_entity_id(entity_id)
    tx = ve.Tx(root=root, project_id=project_id, runner=ve.SHEET, entity_id=entity_id, out=out)

    # Lease FIRST (plan R3#5): the config names the wall time but reading it is
    # itself an authoritative read, so the lease is taken on the default wall
    # time and tightened once the signed config is known.
    with ve.hold_lease(root) as lease:
        config = load_verified_project_config(root)
        ve.retime_lease(lease, config.data.get("wall_time_minutes") or ve.DEFAULT_WALL_TIME_MINUTES)
        qc = config.require_qc()
        pin = pinned_pipeline(root, str(_read_marker(root).get("pipeline_type") or "authored-film"))
        if not _is_qc_manifest(pin):
            raise SheetRunError(f"project is pinned to {pin.name}@{pin.version}; sheet_run needs authored-film 1.3")
        cap = qc.max_attempts_per_series
        if max_attempts is not None:
            if max_attempts > cap:
                raise SheetRunError(f"--max-attempts {max_attempts} exceeds the signed cap {cap}; raising it is a config change")
            cap = max_attempts
        if qc.policy_bundle_sha256 != policy.bundle_sha256():
            raise SheetRunError("the signed config pins a different QC policy bundle than this checkout; re-sign the config")

        resume_check(root)
        ctx: dict[str, Any] = {"root": root, "project_id": project_id, "entity_id": entity_id, "out": out,
                               "config": config, "pin": pin, "tx": tx}
        # D2: run state first; with it, every invocation resumes and generation flags are ignored.
        state = ve.run_state(root, ve.SHEET, entity_id)
        if abandon:
            if state is None:
                raise SheetRunError(f"nothing to abandon: no run state for {entity_id!r}")
            return ve.abandon(tx, state, approval_record=character_approval_record)
        if state is not None:
            return _resume(ctx, state)
        if finish:
            raise SheetRunError(f"nothing to finish: no run state for {entity_id!r}")
        # D1 (Q1): one outstanding entry at a time — sheets and locations share
        # one checkpoint, so a foreign owner under EITHER run-state key blocks.
        ve.require_single_flight(root, ve.SHEET, entity_id)

        look = active_look_for(root, "character", entity_id)
        if look is None:
            raise SheetRunError(f"{entity_id!r} has no active look (ratify it at the look_lock gate first)")
        head = active_headshots(root).get(entity_id)
        if head is None or head.look_hash != look.look_hash:
            raise SheetRunError(f"{entity_id!r} has no approved headshot for the active look")

        # existing bible (other entities' entries are preserved; this entity's entry is replaced)
        bible = ve.bible(root)
        prev = ve.entry_of(bible, ve.SHEET, entity_id)
        if palette is None:
            hues = (bible or {}).get("palette", {}).get("hues") if isinstance(bible, dict) else None
            if not hues:
                raise SheetRunError("no visual_bible palette yet; pass --palette \"hue,hue,hue\" for the first sheet")
            palette = list(hues)
        rejected = ve.rejected(root, ve.SHEET, entity_id)

        wanted = list(roles or MANDATORY_ROLES)
        bad = [r for r in wanted if r not in policy.SHEET_ROLES]
        if bad:
            raise SheetRunError(f"unknown roles {bad}; roles are {policy.SHEET_ROLES}")
        kept: dict[str, dict] = {}
        kept_qc: dict[str, str] = {}
        missing = [r for r in MANDATORY_ROLES if r not in wanted]
        if missing:
            # a subset is only a RERUN: the other mandatory roles must already be present and QC-passed on this entity
            if not prev or not prev.get("qc_receipts") or any(r not in (prev.get("sheet") or {}) or r not in prev["qc_receipts"] for r in missing):
                raise SheetRunError(f"--roles omits mandatory {missing}; a fresh revision runs every mandatory role")
            for r in missing:
                ref, rid = prev["sheet"][r], prev["qc_receipts"][r]
                if ref.get("asset_id") in rejected or rid in rejected:
                    # D2: a kept role the human already rejected is never carried over; it regenerates
                    _log(out, f"[{r}] the previous {r} was rejected by the writer; regenerating it instead of keeping it")
                    wanted.append(r)
                    continue
                kept[r] = ref; kept_qc[r] = rid

        tracker = CostTracker(budget_total_usd=float(config.budget_usd_cap), reserve_pct=0.0, single_action_approval_usd=float("inf"),
                              require_approval_for_new_paid_tool=False, mode=BudgetMode.CAP, cost_log_path=root / "cost_log.json")
        gen_ctx = {"look": look, "headshot": head, "palette": palette}
        judge = SheetJudge(adapter=judge_adapter)
        results: dict[str, dict[str, Any]] = {}

        for role in wanted:
            series = {"entity_kind": "character", "entity_id": entity_id, "role": role, "look_hash": look.look_hash,
                      "headshot_receipt_id": head.receipt_id, "policy_bundle_sha256": qc.policy_bundle_sha256,
                      "builder_policy_sha256": pb.builder_policy_sha256(), "generation_endpoint": "bytedance/seedream/v5/pro/edit",
                      "generation_model": "bytedance/seedream/v5/pro/edit", "judge_provider": qc.judge_provider, "judge_model": qc.judge_model}
            series_sha = qr.series_sha256(dict(series, project_id=project_id))
            # a committed pass in this series is reused without any paid call (never one the writer rejected)
            passed = _acceptable_verdict(root, series_sha, entity_id, qr, rejected)
            if passed is not None:
                _log(out, f"[{role}] reusing passing verdict {passed['receipt_id']} for asset {passed['asset_id'][:12]}")
                results[role] = {"asset_id": passed["asset_id"], "qc_receipt_id": passed["receipt_id"],
                                 "generation_receipt_id": qr.attempt_rows(root, passed["attempt_id"])["generation"]["generation_receipt_id"]}
                continue
            used = len(qr.attempts_started(root, series_sha))
            if used >= cap and _series_has_rejected(root, series_sha, qr, rejected):
                # D2 cap-exhausted rejection: the rejected pass still counts; there is no failed verdict to override.
                msg = (f"[{role}] human-rejected sheet exhausted its budget for {role}; raise qc.max_attempts_per_series "
                       f"(config re-sign) or change the series")
                _log(out, msg)
                raise Blocked(msg)
            remaining = cap - used
            need = remaining * (GENERATION_PRICE_USD[role] + DEFAULT_RESERVE_USD)
            if need > tracker.budget_remaining_usd:
                raise SheetRunError(f"[{role}] {remaining} attempt(s) could cost ${need:.2f}; only ${tracker.budget_remaining_usd:.2f} remains under the cap")
            failed_rows: list[dict] = []
            while True:
                open_ = qr.open_attempts(root, series_sha)
                if open_:
                    row = open_[0]
                    gen = qr.attempt_rows(root, row["attempt_id"])["generation"]
                    if gen is None:
                        # Crash between attempt_started and generation_attached (inspection #6):
                        # settle from the reservation the attempt names. failed → void (counts);
                        # completed → attach the receipt the provider id identifies; else reconcile.
                        gen = _recover_attempt_generation(root, row, qr, out)
                        if gen is None:
                            continue
                    _log(out, f"[{role}] resuming open attempt {row['attempt_n']} on asset {gen['asset_id'][:12]}")
                    attempt, asset_id, gen_rid = row, gen["asset_id"], gen["generation_receipt_id"]
                else:
                    if len(qr.attempts_started(root, series_sha)) >= cap:
                        break
                    holder: dict[str, Any] = {}

                    def _hook(_root: Path, reservation_id: str, _series=series, _holder=holder) -> None:
                        _holder["row"] = qr.start_attempt(_root, _series, max_attempts=cap, generation_reservation_id=reservation_id)

                    with _shared.pre_submit_hook(_hook):
                        asset_id, gen_rid = generate(root, role, gen_ctx)
                    attempt = holder.get("row")
                    if attempt is None:
                        raise SheetRunError(f"[{role}] the generator did not run the pre-submit hook; no attempt was recorded")
                    qr.attach_generation(root, attempt["attempt_id"], generation_receipt_id=gen_rid, asset_id=asset_id)
                    _log(out, f"[{role}] attempt {attempt['attempt_n']}/{cap}: generated {asset_id[:12]}")
                r = judge.execute({"project_dir": str(root), "attempt_id": attempt["attempt_id"], "asset_id": asset_id})
                if not r.success:
                    raise SheetRunError(f"[{role}] judge did not complete: {r.error}")
                verdict = qr.find_verdict_by_id(root, r.data["qc_receipt_id"])
                if r.data["verdict"] == "pass":
                    _log(out, f"[{role}] PASS (warnings: {', '.join(r.data.get('warnings') or []) or 'none'})")
                    results[role] = {"asset_id": asset_id, "qc_receipt_id": r.data["qc_receipt_id"], "generation_receipt_id": gen_rid}
                    break
                failed_rows.append(verdict)
                _log(out, f"[{role}] FAIL {r.data['failing_items']}" + (f" — {r.data.get('note')}" if r.data.get("note") else ""))
            if role not in results:
                best = min((v for v in failed_rows if v.get("provider") != "local"), key=lambda v: len(v.get("failing_items") or []), default=None)
                req_path = None
                if best is not None:
                    req_path = _write_override_request(root, project_id, entity_id, role, best)
                _log(out, f"[{role}] BLOCKED after {cap} attempt(s). Either fix the builder (a template change opens a new "
                          f"series) or accept the judge's failed items:" + (f"\n  {req_path}" if req_path else " (only deterministic failures; nothing to override)"))
                raise Blocked(role)

        # ---- every role passed: draft + gate request ----
        rev = ve.next_revision(tx, prev)
        sheet: dict[str, dict] = {}
        qc_ids: dict[str, str] = {}
        for role in policy.SHEET_ROLES:
            if role in results:
                gen = find_generation(root, results[role]["asset_id"])
                sheet[role] = _image_ref(results[role]["asset_id"], role, gen)
                qc_ids[role] = results[role]["qc_receipt_id"]
            elif role in kept:
                sheet[role] = kept[role]; qc_ids[role] = kept_qc[role]
        hero_gen = find_generation(root, head.asset_id)
        recipe = find_generation(root, sheet["turnaround"]["asset_id"]).get("prompt_recipe")
        entry = {"id": entity_id, "look_ref": {"entity_kind": "character", "entity_id": entity_id, "look_hash": look.look_hash, "receipt_id": look.receipt_id},
                 "sheet_revision": rev, "hero": _hero_ref(head.asset_id, hero_gen), "sheet": sheet,
                 "wardrobe_negative": "; ".join(look.payload.get("negative_lines") or []), "prompt_recipe": recipe,
                 "qc_receipts": qc_ids, "status": "draft"}
        vb = bible if isinstance(bible, dict) else {"version": "1.1", "project_slug": project_id, "palette": {"hues": palette},
                                                    "generator_defaults": {"image_model": "bytedance/seedream/v5/pro/text-to-image", "edit_model": "bytedance/seedream/v5/pro/edit"},
                                                    "characters": [], "locations": []}
        vb = ve.with_entry(json.loads(json.dumps(vb)), ve.SHEET, entity_id, entry)
        req_id = request_id_for("sheet", entity_id, rev)
        state = {"mode": "sheet", "request_id": req_id, "revision": rev, "expected_kind": "sheet", "run_id": ve.new_run_id()}
        # D1 order: checkpoint (draft + run state) → digest → request bound to that digest
        ve.publish(tx, vb, status="awaiting_human", state=state,
                   prev_entry={entity_id: json.loads(json.dumps(prev)) if prev else None},
                   write_request=lambda digest: _write_sheet_request(root, project_id, entity_id, entry,
                                                                     vb.get("palette") or {}, req_id, sheet, rev, digest))
        _log(out, f"\nvisual_bible draft written: {entity_id} revision {rev} ({', '.join(sheet)})")
        ve.refresh_canon_view(root)
        paths = [str(root / "canon" / "visual" / "objects" / f"{r['asset_id']}.png") for r in sheet.values()]
        if open_images and sys.platform == "darwin":
            subprocess.run(["open", "-a", "Preview", *paths], check=False)
        _log(out, "Images:\n  " + "\n  ".join(paths))
        res = ve.pending(tx, req_id)
        return {"entity_id": entity_id, "revision": rev, "request_id": req_id, "roles": results, "paths": paths, "command": res["command"]}


# ---- resume: the request states, per mode (D2) ----

def _resume(ctx, state: dict) -> dict[str, Any]:
    tx = ctx["tx"]
    return ve.resume(
        tx, state,
        on_declined=lambda st, req: ve.decline(tx, st, req, rejections=_draft_rejections),
        on_republish=lambda st: _republish(ctx, st),
        on_finish=lambda st, req: _finish_sheet(ctx, st, req),
    )


def _draft_rejections(entry: Optional[dict]) -> list[str]:
    """Verdict ids and asset hashes of a draft the writer rejected."""
    if not isinstance(entry, dict):
        return []
    ids = [str(v) for v in (entry.get("qc_receipts") or {}).values() if v]
    ids += [str(ref.get("asset_id")) for ref in (entry.get("sheet") or {}).values() if isinstance(ref, dict) and ref.get("asset_id")]
    return ids


def _verify_entry(ctx, entry: dict) -> None:
    from lib.canon_enforcement import _generation_receipt_rows
    from lib.checkpoint import CheckpointValidationError
    from lib.headshots import HeadshotError, active_headshots
    from lib.look_ingest import LookIngestError, active_look_for
    from lib.sheet_verify import verify_character_sheet

    root, entity_id = ctx["root"], ctx["entity_id"]
    try:
        look = active_look_for(root, "character", entity_id)
        heads = active_headshots(root)
    except (LookIngestError, HeadshotError) as exc:
        raise SheetRunError(str(exc)) from exc
    rows = {r["output_sha256"]: r for r in _generation_receipt_rows(root)}
    try:
        verify_character_sheet(root, entry, active_look=look, active_headshot=heads.get(entity_id) if isinstance(heads, dict) else None,
                               receipts_by_sha=rows, qc_required=True, config=ctx["config"], qc_must_be_present=True, pin=ctx["pin"])
    except CheckpointValidationError as exc:
        raise SheetRunError(f"the draft sheet no longer verifies: {exc}") from exc


def _republish(ctx, state) -> dict[str, Any]:
    """Republish the same request id, bound to a freshly taken digest."""
    tx = ctx["tx"]

    def write_request(vb, entry, digest):
        _write_sheet_request(tx.root, tx.project_id, tx.entity_id, entry, vb.get("palette") or {}, state["request_id"],
                             entry.get("sheet") or {}, int(state["revision"]), digest)

    return ve.republish(tx, state, verify_entry=lambda entry: _verify_entry(ctx, entry), write_request=write_request)


def _finish_sheet(ctx, state, req) -> dict[str, Any]:
    """D3: flip the draft to approved, bound to the exact receipt the gate minted."""
    from lib.canon_enforcement import character_approval_record

    tx = ctx["tx"]
    return ve.finish(tx, state, req, approval_record=character_approval_record,
                     verify_entry=lambda entry: _verify_entry(ctx, entry),
                     next_hint=f"Next: cd {REPO} && .venv/bin/python scripts/sheet_run.py --project {tx.project_id} "
                               f"--entity <next-character> --open")


# ---- helpers ----

def _recover_attempt_generation(root: Path, attempt: dict, qr, out) -> Optional[dict]:
    from lib.receipts import verified_generation_receipts
    from tools.cost_tracker import load_reservations

    rid = attempt.get("generation_reservation_id")
    res = load_reservations(root).get(rid or "")
    if res is None:
        raise SheetRunError(f"attempt {attempt['attempt_id']} names reservation {rid!r} which does not exist; reconcile by hand")
    state = res.get("state")
    if state == "failed":
        qr.void_attempt(root, attempt["attempt_id"], reason=f"reservation {rid} failed; no asset")
        _log(out, f"attempt {attempt['attempt_n']} voided (generation failed); it still counts toward the cap")
        return None
    if state == "completed" and res.get("provider_request_id"):
        for r in verified_generation_receipts(root):
            if r.get("provider_request_id") == res["provider_request_id"]:
                return qr.attach_generation(root, attempt["attempt_id"], generation_receipt_id=r["receipt_id"], asset_id=r["output_sha256"])
    raise SheetRunError(f"attempt {attempt['attempt_id']} is open with reservation {rid} in state {state!r}; "
                        f"run scripts/reconcile_paid_calls.py first")


def _acceptable_verdict(root: Path, series_sha: str, entity_id: str, qr, rejected: Optional[set[str]] = None) -> Optional[dict]:
    """A committed verdict in this series that passes, or that failed only on
    items a human has since accepted with a signed qc_override — never one
    whose verdict id or asset the writer rejected at the sheet gate."""
    from lib.sheet_qc.verify import overrides_for

    rejected = rejected or set()
    for a in qr.attempts_started(root, series_sha):
        rows = qr.attempt_rows(root, a["attempt_id"])
        if rows["verdict"] is None:
            continue
        v = qr.find_verdict_by_id(root, rows["verdict"]["qc_receipt_id"])
        if v is None or v.get("attempt_id") != a["attempt_id"] or v.get("provider") == "local":
            continue
        if v.get("receipt_id") in rejected or v.get("asset_id") in rejected:
            continue
        if v.get("verdict") == "pass":
            return v
        if set(v.get("failing_items") or []) <= overrides_for(root, v["receipt_id"], entity_id):
            return v
    return None


def _series_has_rejected(root: Path, series_sha: str, qr, rejected: set[str]) -> bool:
    if not rejected:
        return False
    for a in qr.attempts_started(root, series_sha):
        rows = qr.attempt_rows(root, a["attempt_id"])
        gen, verdict = rows.get("generation"), rows.get("verdict")
        if (gen and gen.get("asset_id") in rejected) or (verdict and verdict.get("qc_receipt_id") in rejected):
            return True
    return False


def _image_ref(asset_id: str, role: str, gen: dict) -> dict:
    prov = {"generator_kind": "model", "model_endpoint": gen["model_endpoint"], "prompt": gen.get("prompt"),
            "generation_receipt_id": gen["receipt_id"]}
    if gen.get("seed") is not None:
        prov["seed"] = gen["seed"]
    return {"asset_id": asset_id, "path": f"canon/visual/objects/{asset_id}.png", "role": role, "provenance": prov}


def _hero_ref(asset_id: str, gen: dict) -> dict:
    if gen.get("generator_kind") == "imported":
        # For an imported hero the import receipt IS its generation receipt and the
        # pixel hash IS the content address (lib.receipts._build_generation_receipt).
        prov = {"generator_kind": "imported", "origin_tool": gen.get("origin_tool"), "attestation_receipt_id": gen.get("attestation_receipt_id"),
                "import_receipt_id": gen.get("import_receipt_id") or gen["receipt_id"],
                "normalized_pixel_hash": gen.get("normalized_pixel_hash") or asset_id,
                "generation_receipt_id": gen["receipt_id"]}
        return {"asset_id": asset_id, "path": f"canon/visual/objects/{asset_id}.png", "role": "hero", "provenance": prov}
    return _image_ref(asset_id, "hero", gen)


def _write_sheet_request(root, project_id, entity_id, entry, palette, req_id, sheet, rev, digest: str) -> Path:
    from lib.canon_enforcement import character_approval_record
    from lib.state_io import atomic_write_json

    d = root / REQUEST_DIRNAME; d.mkdir(exist_ok=True)
    req = {"request_id": req_id, "project_id": project_id, "stage": STAGE, "scope": f"character:{entity_id}",
           "kind": "sheet", "entity_id": entity_id, "artifact": None, "approval_record": character_approval_record(entry, palette),
           "source_checkpoint_digest": digest,
           "summary": f"Approve {entity_id}'s sheet revision {rev} ({', '.join(sheet)}); every view passed machine QC.",
           "preview_paths": [f"canon/visual/objects/{r['asset_id']}.png" for r in sheet.values()]}
    path = d / f"{req_id}.json"
    atomic_write_json(path, req)
    return path


def _write_override_request(root, project_id, entity_id, role, verdict) -> Path:
    from lib.state_io import atomic_write_json

    d = root / REQUEST_DIRNAME; d.mkdir(exist_ok=True)
    for existing in sorted(d.glob(f"override-{entity_id}-{role}-*.json")):
        try:
            if json.loads(existing.read_text()).get("qc_receipt_id") == verdict["receipt_id"]:
                return existing  # already waiting for the human; do not duplicate
        except ValueError:
            continue
    n = 1 + sum(1 for _ in d.glob(f"override-{entity_id}-{role}-*.json"))
    req_id = f"override-{entity_id}-{role}-{n}"
    req = {"request_id": req_id, "project_id": project_id, "stage": STAGE, "scope": f"character:{entity_id}",
           "kind": "qc_override", "entity_id": entity_id, "qc_receipt_id": verdict["receipt_id"],
           "item_ids": list(verdict.get("failing_items") or []), "reason": "",  # typed by the human at the gate
           "summary": f"Accept failed QC items {verdict.get('failing_items')} on {entity_id} {role} (attempt {verdict.get('attempt_n')})",
           "preview_paths": [f"canon/visual/objects/{verdict['asset_id']}.png"]}
    path = d / f"{req_id}.json"
    atomic_write_json(path, req)
    return path


def main(argv: Optional[list[str]] = None) -> int:
    from lib.env_loader import load_env
    from lib.run_lease import LeaseHeldError

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--roles", help="comma list; a subset is only for re-running roles of an existing QC-passed revision")
    ap.add_argument("--max-attempts", type=int, help="may only LOWER the signed cap")
    ap.add_argument("--palette", help="comma list of hues for a project's first sheet")
    ap.add_argument("--resume", action="store_true", help="continue after a signed override (with run state: same as --finish)")
    ap.add_argument("--finish", action="store_true", help="apply the signed sheet request (requires run state)")
    ap.add_argument("--abandon", action="store_true", help="retire a done request whose checkpoint changed after signing")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    load_env()
    try:
        root = resolve_project_root(a.project)
        run_sheet(root, a.entity, roles=a.roles.split(",") if a.roles else None, max_attempts=a.max_attempts,
                  palette=a.palette.split(",") if a.palette else None, resume=a.resume, finish=a.finish, abandon=a.abandon,
                  open_images=a.open)
    except Blocked:
        return EXIT_BLOCKED
    except Declined:
        return EXIT_DECLINED
    except (RunError, LeaseHeldError) as exc:
        # Lease-first ordering means a held lease now refuses before the config
        # and pin checks do; it is a refusal like any other, not a crash.
        print(f"sheet_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

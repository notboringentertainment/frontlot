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

from lib.run_common import (  # noqa: E402
    REQUEST_DIRNAME, RunError, gate_command, read_request, request_id_for, request_paths, require_entity_id,
    resolve_project_root, write_decision,
)
from lib.sheet_qc.verify import MANDATORY_ROLES  # noqa: E402

STAGE = "visual_bible"
EXIT_BLOCKED = 3
EXIT_DECLINED = 4
ABANDONED_DIRNAME = "abandoned"
GENERATION_PRICE_USD = {"turnaround": 0.14, "expressions": 0.07, "wardrobe": 0.07}
# Explicit sizes: the local pre-check enforces landscape 3:2 for the 2x3 expression grid, so ask for it.
IMAGE_SIZE = {"turnaround": {"width": 2560, "height": 1600}, "expressions": {"width": 1536, "height": 1024}, "wardrobe": {"width": 1536, "height": 1024}}
_HEX = frozenset("0123456789abcdef")


class SheetRunError(RunError):
    pass


class Blocked(SheetRunError):
    """A role exhausted its attempts; an override request was written (or,
    for a human-rejected series, nothing is left to override)."""


class Declined(SheetRunError):
    """The human declined the sheet request; the draft was withdrawn."""


def _log(out, msg: str) -> None:
    print(msg, file=out or sys.stdout)


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


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


# ---- checkpoint + run state (D1) ----

def _checkpoint(root: Path) -> dict[str, Any]:
    path = root / f"checkpoint_{STAGE}.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SheetRunError(f"{path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _meta(root: Path) -> dict[str, Any]:
    return dict(_checkpoint(root).get("metadata") or {})


def _run_state(root: Path, entity_id: str) -> Optional[dict[str, Any]]:
    state = (_meta(root).get("run_state") or {}).get(entity_id)
    return dict(state) if isinstance(state, dict) else None


def _rejected(root: Path, entity_id: str) -> set[str]:
    return {str(x) for x in (_meta(root).get("rejected_sheets") or {}).get(entity_id) or []}


def _bible(root: Path) -> Optional[dict[str, Any]]:
    bible = (_checkpoint(root).get("artifacts") or {}).get("visual_bible")
    return json.loads(json.dumps(bible)) if isinstance(bible, dict) else None


def _entry_of(vb: Optional[dict], entity_id: str) -> Optional[dict[str, Any]]:
    for e in (vb or {}).get("characters") or []:
        if isinstance(e, dict) and e.get("id") == entity_id:
            return e
    return None


def _with_entry(vb: dict, entity_id: str, entry: Optional[dict]) -> dict:
    """``vb`` with this entity's entry replaced by ``entry`` (dropped when None)."""
    chars = [e for e in vb.get("characters") or [] if not (isinstance(e, dict) and e.get("id") == entity_id)]
    if entry is not None:
        chars.append(entry)
    vb["characters"] = chars
    return vb


def _restore_prev(root: Path, vb: dict, entity_id: str) -> dict:
    """Put back the entry the draft replaced (recorded at publish), or drop the draft."""
    prev = (_meta(root).get("sheet_prev_entry") or {}).get(entity_id)
    return _with_entry(vb, entity_id, dict(prev) if isinstance(prev, dict) else None)


def _write(root: Path, vb: dict[str, Any], *, status: str, run_state: Optional[dict[str, Optional[dict]]] = None,
           prev_entry: Optional[dict[str, Optional[dict]]] = None, clear_prev: Optional[list[str]] = None,
           rejected: Optional[dict[str, list[str]]] = None) -> str:
    """ONE checkpoint write carrying the bible and the run-state change;
    returns the digest of the file written (what a request binds)."""
    from lib.checkpoint import checkpoint_digest, write_checkpoint

    meta = _meta(root)
    states = dict(meta.get("run_state") or {})
    revs = dict(meta.get("sheet_revisions") or {})
    for entity, state in (run_state or {}).items():
        if state is None:
            states.pop(entity, None)
        else:
            states[entity] = state
            if state.get("revision") is not None:
                revs[entity] = max(int(revs.get(entity, 0)), int(state["revision"]))
    meta["run_state"] = states
    meta["sheet_revisions"] = revs
    prevs = dict(meta.get("sheet_prev_entry") or {})
    for entity, entry in (prev_entry or {}).items():
        prevs[entity] = entry
    for entity in clear_prev or []:
        prevs.pop(entity, None)
    meta["sheet_prev_entry"] = prevs
    if rejected:
        hist = dict(meta.get("rejected_sheets") or {})
        for entity, ids in rejected.items():
            hist[entity] = sorted(set(hist.get(entity) or []) | {str(i) for i in ids})
        meta["rejected_sheets"] = hist
    path = write_checkpoint(root.parent, root.name, STAGE, status, {"visual_bible": vb},
                            pipeline_type="authored-film", human_approval_required=True, metadata=meta)
    return checkpoint_digest(path)


def _next_revision(root: Path, entity_id: str, prev: Optional[dict]) -> int:
    issued = int((_meta(root).get("sheet_revisions") or {}).get(entity_id, 0))
    return max(issued, int((prev or {}).get("sheet_revision") or 0)) + 1


# ---- the run ----

def run_sheet(
    project_root: Path | str, entity_id: str, *, roles: Optional[list[str]] = None, max_attempts: Optional[int] = None,
    palette: Optional[list[str]] = None, resume: bool = False, finish: bool = False, abandon: bool = False,
    open_images: bool = False,
    generate: Optional[Callable[[Path, str, dict[str, Any]], tuple[str, str]]] = None,
    judge_adapter: Any = None, out=None,
) -> dict[str, Any]:
    from lib import qc_receipts as qr, run_lease
    from lib.canon_enforcement import _is_qc_manifest
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
    pipeline_dir = root.parent  # projects/<slug> — the checkpoint API addresses (pipeline_dir, project_id)
    generate = generate or default_generate
    require_entity_id(entity_id)

    config = load_verified_project_config(root)
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

    with run_lease.acquire(root, float(config.data.get("wall_time_minutes") or 60)):
        resume_check(root)
        ctx: dict[str, Any] = {"root": root, "project_id": project_id, "entity_id": entity_id, "out": out, "config": config, "pin": pin}
        # D2: run state first; with it, every invocation resumes and generation flags are ignored.
        state = _run_state(root, entity_id)
        if abandon:
            if state is None:
                raise SheetRunError(f"nothing to abandon: no run state for {entity_id!r}")
            return _abandon(ctx, state)
        if state is not None:
            return _resume(ctx, state)
        if finish:
            raise SheetRunError(f"nothing to finish: no run state for {entity_id!r}")
        # D1 (Q1): one outstanding sheet at a time — every entity shares one checkpoint.
        others = sorted(e for e in (_meta(root).get("run_state") or {}) if e != entity_id)
        if others:
            raise SheetRunError(f"{others[0]!r} has a sheet request in flight; finish or decline {others[0]} first "
                                f"(sheet_run.py --entity {others[0]} --finish)")

        look = active_look_for(root, "character", entity_id)
        if look is None:
            raise SheetRunError(f"{entity_id!r} has no active look (ratify it at the look_lock gate first)")
        head = active_headshots(root).get(entity_id)
        if head is None or head.look_hash != look.look_hash:
            raise SheetRunError(f"{entity_id!r} has no approved headshot for the active look")

        # existing bible (other entities' entries are preserved; this entity's entry is replaced)
        bible = _bible(root)
        prev = _entry_of(bible, entity_id)
        if palette is None:
            hues = (bible or {}).get("palette", {}).get("hues") if isinstance(bible, dict) else None
            if not hues:
                raise SheetRunError("no visual_bible palette yet; pass --palette \"hue,hue,hue\" for the first sheet")
            palette = list(hues)
        rejected = _rejected(root, entity_id)

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
        rev = _next_revision(root, entity_id, prev)
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
        vb = _with_entry(json.loads(json.dumps(vb)), entity_id, entry)
        req_id = request_id_for("sheet", entity_id, rev)
        state = {"mode": "sheet", "request_id": req_id, "revision": rev, "expected_kind": "sheet"}
        # D1 order: checkpoint (draft + run state) → digest → request bound to that digest
        digest = _write(root, vb, status="awaiting_human", run_state={entity_id: state},
                        prev_entry={entity_id: json.loads(json.dumps(prev)) if prev else None})
        _write_sheet_request(root, project_id, entity_id, entry, vb.get("palette") or {}, req_id, sheet, rev, digest)
        _log(out, f"\nvisual_bible draft written: {entity_id} revision {rev} ({', '.join(sheet)})")
        try:
            from lib.canon_view import build_view
            build_view(root)
        except Exception:  # noqa: BLE001
            pass
        paths = [str(root / "canon" / "visual" / "objects" / f"{r['asset_id']}.png") for r in sheet.values()]
        if open_images and sys.platform == "darwin":
            subprocess.run(["open", "-a", "Preview", *paths], check=False)
        _log(out, "Images:\n  " + "\n  ".join(paths))
        res = _pending(root, entity_id, req_id, out, "")
        return {"entity_id": entity_id, "revision": rev, "request_id": req_id, "roles": results, "paths": paths, "command": res["command"]}


def _pending(root: Path, entity_id: str, request_id: str, out, headline: str) -> dict[str, Any]:
    cmd = gate_command(root, request_id)
    _log(out, (f"{headline}\n" if headline else "") + f"Approve in Terminal (y to sign):\n  {cmd}")
    return {"entity_id": entity_id, "status": "pending", "request_id": request_id, "command": cmd}


# ---- resume: the request states, per mode (D2) ----

def _resume(ctx, state: dict) -> dict[str, Any]:
    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    request_id, mode = str(state.get("request_id") or ""), state.get("mode")
    if mode == "abandoning":
        return _complete_abandon(ctx, state)
    if mode != "sheet" or not request_id:
        raise SheetRunError(f"run state for {entity_id!r} is malformed: {state}")
    where, req = read_request(root, request_id)
    if where == "pending":
        return _pending(root, entity_id, request_id, out, f"request {request_id} is still waiting for the writer")
    if where == "declined":
        return _declined(ctx, state, req or {})
    if where == "missing":
        return _republish(ctx, state)
    if req is None or req.get("kind") != state.get("expected_kind"):
        raise SheetRunError(f"run state names request {request_id} ({state.get('expected_kind')}); the done request has another kind "
                            f"({(req or {}).get('kind')!r}) — refusing to guess")
    return _finish_sheet(ctx, state, req)


def _draft_rejections(entry: Optional[dict]) -> list[str]:
    """Verdict ids and asset hashes of a draft the writer rejected."""
    if not isinstance(entry, dict):
        return []
    ids = [str(v) for v in (entry.get("qc_receipts") or {}).values() if v]
    ids += [str(ref.get("asset_id")) for ref in (entry.get("sheet") or {}).values() if isinstance(ref, dict) and ref.get("asset_id")]
    return ids


def _declined(ctx, state, req) -> dict[str, Any]:
    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    note = str(req.get("declined_note") or "").strip() or "(no note)"
    write_decision(root, stage=STAGE, category="revision", subject=f"sheet {state['request_id']} declined for {entity_id}",
                   reason=note, selected="declined", user_approved=True)
    vb = _bible(root) or {}
    rejected = _draft_rejections(_entry_of(vb, entity_id))
    vb = _restore_prev(root, vb, entity_id)
    _write(root, vb, status="in_progress", run_state={entity_id: None}, clear_prev=[entity_id], rejected={entity_id: rejected})
    _log(out, f"declined: {note}\nThe draft was withdrawn; the rejected views are never reused. Re-run sheet_run to regenerate "
              f"(revision {int(state.get('revision') or 0) + 1}).")
    raise Declined(note)


def _load_draft(ctx, state) -> tuple[dict, dict, dict]:
    """``(checkpoint, bible, draft entry)`` for the revision the run state names."""
    entity_id = ctx["entity_id"]
    cp = _checkpoint(ctx["root"])
    vb = json.loads(json.dumps((cp.get("artifacts") or {}).get("visual_bible") or {}))
    entry = _entry_of(vb, entity_id)
    if entry is None or entry.get("status") != "draft" or int(entry.get("sheet_revision") or 0) != int(state.get("revision") or -1):
        found = "no entry" if entry is None else f"status {entry.get('status')!r} revision {entry.get('sheet_revision')}"
        raise SheetRunError(f"run state names {entity_id!r} draft revision {state.get('revision')} but the checkpoint carries {found}; refusing")
    return cp, vb, entry


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
    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    request_id = state["request_id"]
    cp, vb, entry = _load_draft(ctx, state)
    if cp.get("status") != "awaiting_human":
        raise SheetRunError(f"run state names request {request_id} but the visual_bible checkpoint is {cp.get('status')!r}, not awaiting_human; refusing")
    _verify_entry(ctx, entry)
    digest = _write(root, vb, status="awaiting_human", run_state={entity_id: state})
    _write_sheet_request(root, ctx["project_id"], entity_id, entry, vb.get("palette") or {}, request_id, entry.get("sheet") or {},
                         int(state["revision"]), digest)
    return _pending(root, entity_id, request_id, out, f"republished {request_id}")


def _bound_digest(req: dict) -> str:
    bound = req.get("source_checkpoint_digest")
    if not _sha256_hex(bound):
        raise SheetRunError(f"done request {req.get('request_id')} carries no 64-hex source_checkpoint_digest; it was not published by sheet_run — refusing")
    return bound


def _receipt_id_for_done(root: Path, req: dict, entity_id: str, record_sha: str, bound: str) -> str:
    """The receipt the gate minted for this done request: its enriched
    ``approval_receipt_id``; for a legacy marker without one, the unique
    verified receipt matching the reconstructed tuple (refuse on 0 or >1)."""
    from lib.receipts import verified_approvals

    rid = req.get("approval_receipt_id")
    if rid is not None:
        if not isinstance(rid, str) or not rid:
            raise SheetRunError(f"done request {req.get('request_id')} carries a malformed approval_receipt_id {rid!r}")
        return rid
    rows = [r for r in verified_approvals(root, "sheet", entity_id=entity_id)
            if r.get("record_sha256") == record_sha and r.get("source_checkpoint_digest") == bound]
    if len(rows) != 1:
        raise SheetRunError(f"done request {req.get('request_id')} names no approval_receipt_id and {len(rows)} verified sheet receipts "
                            f"match its record + checkpoint digest; refusing to guess")
    return str(rows[0]["receipt_id"])


def _finish_sheet(ctx, state, req) -> dict[str, Any]:
    """D3: flip the draft to approved, bound to the exact receipt the gate minted. Fail closed on every check."""
    from lib.canon_enforcement import character_approval_record
    from lib.canonical_json import record_sha256
    from lib.checkpoint import checkpoint_digest
    from lib.receipts import ReceiptError, exact_approval

    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    request_id = state["request_id"]
    bound = _bound_digest(req)  # 1
    cp = _checkpoint(root)  # 2: nothing may have rewritten the checkpoint since the request was published
    actual = checkpoint_digest(root / f"checkpoint_{STAGE}.json") if (root / f"checkpoint_{STAGE}.json").is_file() else None
    if cp.get("status") != "awaiting_human" or actual != bound:
        raise SheetRunError(
            f"request {request_id} is signed against checkpoint digest {bound[:12]}… but the visual_bible checkpoint is now "
            f"{cp.get('status')!r} with digest {(actual or 'none')[:12]}…; it changed after signing. The receipt cannot be applied: "
            f"run sheet_run.py --project {ctx['project_id']} --entity {entity_id} --abandon to retire this request, then re-run"
        )
    _, vb, entry = _load_draft(ctx, state)  # 3
    palette = vb.get("palette") or {}
    record_sha = record_sha256(character_approval_record(entry, palette))
    receipt_id = _receipt_id_for_done(root, req, entity_id, record_sha, bound)
    try:  # 4
        exact_approval(root, receipt_id=receipt_id, kind="sheet", entity_id=entity_id, record_sha256=record_sha, source_checkpoint_digest=bound)
    except ReceiptError as exc:
        raise SheetRunError(f"request {request_id} is done but its receipt does not verify: {exc}") from exc
    _verify_entry(ctx, entry)  # 5
    entry["status"] = "approved"  # 6
    entry["approval_receipt_id"] = receipt_id
    _write(root, _with_entry(vb, entity_id, entry), status="in_progress", run_state={entity_id: None}, clear_prev=[entity_id])
    try:  # 7
        from lib.canon_view import build_view
        build_view(root)
    except Exception:  # noqa: BLE001
        pass
    rev = int(state["revision"])
    _log(out, f"approved {entity_id} rev {rev} receipt {receipt_id}\n"  # 8
              f"Next: cd {REPO} && .venv/bin/python scripts/sheet_run.py --project {ctx['project_id']} --entity <next-character> --open")
    return {"entity_id": entity_id, "status": "approved", "revision": rev, "receipt_id": receipt_id}


# ---- abandon: a done request whose checkpoint went stale after signing (D3 item 2) ----

def _abandon(ctx, state) -> dict[str, Any]:
    from lib.canon_enforcement import character_approval_record
    from lib.canonical_json import record_sha256
    from lib.checkpoint import checkpoint_digest

    root, entity_id = ctx["root"], ctx["entity_id"]
    if state.get("mode") == "abandoning":
        return _complete_abandon(ctx, state)
    request_id = str(state.get("request_id") or "")
    if state.get("mode") != "sheet" or not request_id:
        raise SheetRunError(f"run state for {entity_id!r} is malformed: {state}")
    where, req = read_request(root, request_id)
    if where != "done" or req is None:
        raise SheetRunError(f"--abandon applies only to a done request whose checkpoint went stale; {request_id} is {where} "
                            f"(a pending request is declined at the gate; a declined or missing one is handled by re-running sheet_run)")
    bound = _bound_digest(req)
    cp = _checkpoint(root)
    path = root / f"checkpoint_{STAGE}.json"
    if cp.get("status") == "awaiting_human" and path.is_file() and checkpoint_digest(path) == bound:
        raise SheetRunError(f"request {request_id} is not stale: the checkpoint still matches its digest; run --finish instead")
    # a. reconcile: the current entry must still be exactly the draft this run published
    vb = _bible(root) or {}
    entry = _entry_of(vb, entity_id)
    expected = record_sha256(req.get("approval_record") or {})
    if (entry is None or entry.get("status") != "draft" or int(entry.get("sheet_revision") or 0) != int(state.get("revision") or -1)
            or record_sha256(character_approval_record(entry, vb.get("palette") or {})) != expected):
        raise SheetRunError(f"the visual_bible entry for {entity_id!r} is no longer the draft request {request_id} published "
                            f"(a newer edit exists); reconcile it by hand — nothing was overwritten and the run state is kept")
    # b. one write: restore the previous entry AND mark the transition
    new_state = {"mode": "abandoning", "request_id": request_id, "receipt_id": req.get("approval_receipt_id"), "revision": state.get("revision")}
    _write(root, _restore_prev(root, vb, entity_id), status="awaiting_human", run_state={entity_id: new_state})
    return _complete_abandon(ctx, new_state)


def _decision_logged(root: Path, subject: str) -> bool:
    path = root / "decision_log.json"
    if not path.is_file():
        return False
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(isinstance(d, dict) and d.get("subject") == subject for d in log.get("decisions") or [])


def _clear_abandon_state(ctx, entity_id: str) -> None:
    """Step e: clear the run state; the checkpoint returns to in_progress."""
    _write(ctx["root"], _bible(ctx["root"]) or {}, status="in_progress", run_state={entity_id: None}, clear_prev=[entity_id])


def _complete_abandon(ctx, state) -> dict[str, Any]:
    """Steps c–e, each idempotent: a crash anywhere is completed by re-running."""
    from lib.state_io import atomic_move

    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    request_id = str(state.get("request_id") or "")
    subject = f"abandon sheet {request_id} for {entity_id}"
    if not _decision_logged(root, subject):  # c
        write_decision(root, stage=STAGE, category="revision", subject=subject, selected="abandoned", user_approved=True,
                       reason=f"stale checkpoint digest; receipt {state.get('receipt_id')} stays in the ledger unused")
    paths = request_paths(root, request_id)  # d
    archive = root / REQUEST_DIRNAME / ABANDONED_DIRNAME / f"{request_id}.json"
    if paths["done"].is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        atomic_move(paths["done"], archive)
    elif not archive.is_file():
        raise SheetRunError(f"request {request_id} is neither done nor abandoned; refusing to clear its run state")
    _clear_abandon_state(ctx, entity_id)  # e
    _log(out, f"abandoned {request_id} for {entity_id!r}: the draft was withdrawn and the marker archived under "
              f"{REQUEST_DIRNAME}/{ABANDONED_DIRNAME}/. Re-run sheet_run to publish revision {int(state.get('revision') or 0) + 1}.")
    return {"entity_id": entity_id, "status": "abandoned", "request_id": request_id}


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
    except RunError as exc:
        print(f"sheet_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

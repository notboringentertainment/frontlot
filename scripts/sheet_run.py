#!/usr/bin/env python3
"""D19.5 — make a character's sheet: generate → judge → retry within the signed
cap → write the awaiting_human draft and the sheet gate request. Never presents
a failing sheet; never edits the prompt; never mints anything.

Usage:
  scripts/sheet_run.py --project <slug> --entity <id> [--roles turnaround,expressions[,wardrobe]]
                       [--max-attempts N] [--palette "a,b,c"] [--resume] [--open]

Governed end to end: the project's pinned manifest must be authored-film 1.3
and its signed 1.1 config names the judge and the attempt cap
(``--max-attempts`` may only LOWER it). Every generated candidate is a signed
``attempt_started`` row (committed inside the paid tool's pre-submit hook, so
a crash after reservation still counts); every verdict is a signed QC
receipt; the draft carries ``qc_receipts`` per role and canon enforcement
refuses the write unless every role passes (or a human signed a qc_override).
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

from lib.sheet_qc.verify import MANDATORY_ROLES  # noqa: E402

GENERATION_PRICE_USD = {"turnaround": 0.14, "expressions": 0.07, "wardrobe": 0.07}
IMAGE_SIZE = {"turnaround": {"width": 2560, "height": 1600}, "expressions": "auto_1K", "wardrobe": "auto_1K"}


class SheetRunError(RuntimeError):
    pass


class Blocked(SheetRunError):
    """A role exhausted its attempts; an override request was written."""


def _log(out, msg: str) -> None:
    print(msg, file=out)


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
    palette: Optional[list[str]] = None, resume: bool = False, open_images: bool = False,
    generate: Optional[Callable[[Path, str, dict[str, Any]], tuple[str, str]]] = None,
    judge_adapter: Any = None, out=None,
) -> dict[str, Any]:
    from lib import qc_receipts as qr, run_lease
    from lib.canon_enforcement import _is_qc_manifest
    from lib.checkpoint import read_checkpoint, write_checkpoint
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
        look = active_look_for(root, "character", entity_id)
        if look is None:
            raise SheetRunError(f"{entity_id!r} has no active look (ratify it at the look_lock gate first)")
        head = active_headshots(root).get(entity_id)
        if head is None or head.look_hash != look.look_hash:
            raise SheetRunError(f"{entity_id!r} has no approved headshot for the active look")

        # existing bible (other entities' entries are preserved; this entity's entry is replaced)
        try:
            cp = read_checkpoint(pipeline_dir, project_id, "visual_bible")
        except Exception:  # noqa: BLE001
            cp = None
        bible = (cp or {}).get("artifacts", {}).get("visual_bible") if cp else None
        prev = None
        if isinstance(bible, dict):
            prev = next((e for e in bible.get("characters") or [] if isinstance(e, dict) and e.get("id") == entity_id), None)
        if palette is None:
            hues = (bible or {}).get("palette", {}).get("hues") if isinstance(bible, dict) else None
            if not hues:
                raise SheetRunError("no visual_bible palette yet; pass --palette \"hue,hue,hue\" for the first sheet")
            palette = list(hues)

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
                kept[r] = prev["sheet"][r]; kept_qc[r] = prev["qc_receipts"][r]

        tracker = CostTracker(budget_total_usd=float(config.budget_usd_cap), reserve_pct=0.0, single_action_approval_usd=float("inf"),
                              require_approval_for_new_paid_tool=False, mode=BudgetMode.CAP, cost_log_path=root / "cost_log.json")
        ctx = {"look": look, "headshot": head, "palette": palette}
        judge = SheetJudge(adapter=judge_adapter)
        results: dict[str, dict[str, Any]] = {}

        for role in wanted:
            series = {"entity_kind": "character", "entity_id": entity_id, "role": role, "look_hash": look.look_hash,
                      "headshot_receipt_id": head.receipt_id, "policy_bundle_sha256": qc.policy_bundle_sha256,
                      "builder_policy_sha256": pb.builder_policy_sha256(), "generation_endpoint": "bytedance/seedream/v5/pro/edit",
                      "generation_model": "bytedance/seedream/v5/pro/edit", "judge_provider": qc.judge_provider, "judge_model": qc.judge_model}
            series_sha = qr.series_sha256(dict(series, project_id=project_id))
            # a committed pass in this series is reused without any paid call
            passed = _acceptable_verdict(root, series_sha, entity_id, qr)
            if passed is not None:
                _log(out, f"[{role}] reusing passing verdict {passed['receipt_id']} for asset {passed['asset_id'][:12]}")
                results[role] = {"asset_id": passed["asset_id"], "qc_receipt_id": passed["receipt_id"],
                                 "generation_receipt_id": qr.attempt_rows(root, passed["attempt_id"])["generation"]["generation_receipt_id"]}
                continue
            used = len(qr.attempts_started(root, series_sha))
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
                        asset_id, gen_rid = generate(root, role, ctx)
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
        rev = int((prev or {}).get("sheet_revision") or 0) + 1
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
        vb = json.loads(json.dumps(vb))
        chars = [e for e in vb.get("characters") or [] if not (isinstance(e, dict) and e.get("id") == entity_id)]
        chars.append(entry); vb["characters"] = chars
        write_checkpoint(pipeline_dir, project_id, "visual_bible", "awaiting_human", {"visual_bible": vb},
                         pipeline_type="authored-film", human_approval_required=True)
        req_id = f"sheet-{entity_id}-{rev}"
        _write_sheet_request(root, project_id, entity_id, entry, vb.get("palette") or {}, req_id, sheet, rev)
        _log(out, f"\nvisual_bible draft written: {entity_id} revision {rev} ({', '.join(sheet)})")
        try:
            from lib.canon_view import build_view
            build_view(root)
        except Exception:  # noqa: BLE001
            pass
        paths = [str(root / "canon" / "visual" / "objects" / f"{r['asset_id']}.png") for r in sheet.values()]
        if open_images and sys.platform == "darwin":
            subprocess.run(["open", "-a", "Preview", *paths], check=False)
        cmd = f"cd {REPO} && .venv/bin/python scripts/gate_approve.py --project {project_id} --request {req_id}"
        _log(out, "Images:\n  " + "\n  ".join(paths))
        _log(out, f"Approve in Terminal (y to sign):\n  {cmd}")
        return {"entity_id": entity_id, "revision": rev, "request_id": req_id, "roles": results, "paths": paths}


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


def _acceptable_verdict(root: Path, series_sha: str, entity_id: str, qr) -> Optional[dict]:
    """A committed verdict in this series that passes, or that failed only on
    items a human has since accepted with a signed qc_override."""
    from lib.sheet_qc.verify import overrides_for

    for a in qr.attempts_started(root, series_sha):
        rows = qr.attempt_rows(root, a["attempt_id"])
        if rows["verdict"] is None:
            continue
        v = qr.find_verdict_by_id(root, rows["verdict"]["qc_receipt_id"])
        if v is None or v.get("attempt_id") != a["attempt_id"] or v.get("provider") == "local":
            continue
        if v.get("verdict") == "pass":
            return v
        if set(v.get("failing_items") or []) <= overrides_for(root, v["receipt_id"], entity_id):
            return v
    return None


def _image_ref(asset_id: str, role: str, gen: dict) -> dict:
    prov = {"generator_kind": "model", "model_endpoint": gen["model_endpoint"], "prompt": gen.get("prompt"),
            "generation_receipt_id": gen["receipt_id"]}
    if gen.get("seed") is not None:
        prov["seed"] = gen["seed"]
    return {"asset_id": asset_id, "path": f"canon/visual/objects/{asset_id}.png", "role": role, "provenance": prov}


def _hero_ref(asset_id: str, gen: dict) -> dict:
    if gen.get("generator_kind") == "imported":
        prov = {"generator_kind": "imported", "origin_tool": gen.get("origin_tool"), "attestation_receipt_id": gen.get("attestation_receipt_id"),
                "import_receipt_id": gen.get("import_receipt_id"), "normalized_pixel_hash": gen.get("normalized_pixel_hash"),
                "generation_receipt_id": gen["receipt_id"]}
        return {"asset_id": asset_id, "path": f"canon/visual/objects/{asset_id}.png", "role": "hero", "provenance": prov}
    return _image_ref(asset_id, "hero", gen)


def _write_sheet_request(root, project_id, entity_id, entry, palette, req_id, sheet, rev) -> Path:
    from lib.canon_enforcement import character_approval_record

    d = root / ".gate-requests"; d.mkdir(exist_ok=True)
    req = {"request_id": req_id, "project_id": project_id, "stage": "visual_bible", "scope": f"character:{entity_id}",
           "kind": "sheet", "entity_id": entity_id, "artifact": None, "approval_record": character_approval_record(entry, palette),
           "source_checkpoint_digest": None,
           "summary": f"Approve {entity_id}'s sheet revision {rev} ({', '.join(sheet)}); every view passed machine QC.",
           "preview_paths": [f"canon/visual/objects/{r['asset_id']}.png" for r in sheet.values()]}
    path = d / f"{req_id}.json"
    path.write_text(json.dumps(req, indent=2))
    return path


def _write_override_request(root, project_id, entity_id, role, verdict) -> Path:
    d = root / ".gate-requests"; d.mkdir(exist_ok=True)
    for existing in sorted(d.glob(f"override-{entity_id}-{role}-*.json")):
        try:
            if json.loads(existing.read_text()).get("qc_receipt_id") == verdict["receipt_id"]:
                return existing  # already waiting for the human; do not duplicate
        except ValueError:
            continue
    n = 1 + sum(1 for _ in d.glob(f"override-{entity_id}-{role}-*.json"))
    req_id = f"override-{entity_id}-{role}-{n}"
    req = {"request_id": req_id, "project_id": project_id, "stage": "visual_bible", "scope": f"character:{entity_id}",
           "kind": "qc_override", "entity_id": entity_id, "qc_receipt_id": verdict["receipt_id"],
           "item_ids": list(verdict.get("failing_items") or []), "reason": "",  # typed by the human at the gate
           "summary": f"Accept failed QC items {verdict.get('failing_items')} on {entity_id} {role} (attempt {verdict.get('attempt_n')})",
           "preview_paths": [f"canon/visual/objects/{verdict['asset_id']}.png"]}
    path = d / f"{req_id}.json"
    path.write_text(json.dumps(req, indent=2))
    return path


def main(argv: Optional[list[str]] = None) -> int:
    from lib.env_loader import load_env
    from lib.paths import PROJECTS_DIR

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--roles", help="comma list; a subset is only for re-running roles of an existing QC-passed revision")
    ap.add_argument("--max-attempts", type=int, help="may only LOWER the signed cap")
    ap.add_argument("--palette", help="comma list of hues for a project's first sheet")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    load_env()
    root = Path(PROJECTS_DIR) / a.project
    if not (root / "project.yaml").is_file():
        print(f"no project at {root}", file=sys.stderr)
        return 2
    try:
        run_sheet(root, a.entity, roles=a.roles.split(",") if a.roles else None, max_attempts=a.max_attempts,
                  palette=a.palette.split(",") if a.palette else None, resume=a.resume, open_images=a.open)
    except Blocked:
        return 3
    except SheetRunError as exc:
        print(f"sheet_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

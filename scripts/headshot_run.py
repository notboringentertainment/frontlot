#!/usr/bin/env python3
"""D20.2 — one approved face per character, as a command.

Usage:
  scripts/headshot_run.py --project <slug> --entity <id>
        [--import <file> --origin-tool <name>]   Mode A: the writer's own generated image, judged like any other
        [--candidates N]                          Mode C (default): generate, judge, present up to N (≤4) unique passing faces
        [--replace] [--finish] [--open]
        [--grandfather]                           D20.7: judge the ACTIVE legacy (1.3-era) hero and request its attestation
        [--retire]                                D20.7: retire the active hero with no replacement (a legacy hero the writer
                                                  will neither override nor keep; unblocks the 1.4 migration)

Governed end to end: authored-film 1.4 (``--grandfather`` also under 1.3),
a signed 1.2 config naming the judge, the HERO policy bundle and the hero
budget (``qc.max_hero_attempts`` per entity per look — the ONLY hero limit,
counted across every series). Every candidate the human sees carries a
signed hero verdict; the run never edits the prompt and never mints
anything. Durable run state lives in ``checkpoint_headshots.json``
``metadata.run_state[<entity>]``; any later invocation finishes or resumes
ONLY the request that state names (missing → republish after re-validation;
pending → print the command; done → finish; declined → consume the writer's
note and regenerate; mismatch → fail closed).
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
    RunError, gate_command, hold_lease, read_request, request_id_for, require_entity_id, resolve_project_root, write_decision,
)

STAGE = "headshots"
HERO_ROLE = "hero"
GENERATION_ENDPOINT = "bytedance/seedream/v5/pro/text-to-image"
GENERATION_PRICE_USD = 0.07
HERO_IMAGE_SIZE = {"width": 1024, "height": 1280}  # portrait; the local pre-check refuses landscape
MAX_CANDIDATES = 4
EXIT_BLOCKED = 3
EXIT_DECLINED = 4
# R1#12: a reject-all note that asks for a different APPEARANCE is a look change,
# not a regeneration. This is a conservative word list, not a judgement of taste.
LOOK_CHANGE_WORDS = ("hair", "beard", "moustache", "mustache", "older", "younger", "age", "thinner", "heavier",
                     "taller", "shorter", "scar", "tattoo", "glasses", "eye colour", "eye color", "skin", "ethnic",
                     "nose", "jaw", "build", "weight", "bald", "blond", "brunette", "grey", "gray")


class HeadshotRunError(RunError):
    pass


class Blocked(HeadshotRunError):
    """The hero budget is spent (or the legacy hero failed); an override request was written."""


class Declined(HeadshotRunError):
    pass


def _log(out, msg: str) -> None:
    print(msg, file=out or sys.stdout)


# ---- generation (default: governed Seedream text_to_image; tests inject a fake) ----

def default_generate(root: Path, ctx: dict[str, Any]) -> tuple[str, str]:
    """One hero candidate through the governed Seedream path (``stage:
    headshots``, look_refs, sealed prompt_recipe, NO headshot_ref). Returns
    ``(asset_id, generation_receipt_id)``. The pre-submit hook installed by
    the run records the attempt row inside the tool."""
    from lib.receipts import find_generation
    from tools.graphics.seedream_image import SeedreamImage

    look, built = ctx["look"], ctx["built"]
    inputs = {
        "operation": "text_to_image", "prompt": built["prompt"], "prompt_recipe": built["prompt_recipe"],
        "asset_role": HERO_ROLE, "stage": STAGE,
        "look_refs": [{"entity_kind": look.entity_kind, "entity_id": look.entity_id, "look_hash": look.look_hash}],
        "palette": ctx["palette"], "image_size": dict(HERO_IMAGE_SIZE), "num_images": 1, "output_format": "png",
        "project_dir": str(root),
    }
    r = SeedreamImage().execute(inputs)
    if not r.success:
        raise HeadshotRunError(f"generation failed: {r.error}")
    path = Path(r.data.get("output_path") or (r.data.get("output_paths") or [""])[0])
    asset_id = path.stem
    gen = find_generation(root, asset_id)
    if gen is None:
        raise HeadshotRunError(f"no verified generation receipt for {asset_id}")
    return asset_id, gen["receipt_id"]


# ---- checkpoint + run state ----

def _checkpoint(root: Path) -> dict[str, Any]:
    path = root / f"checkpoint_{STAGE}.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HeadshotRunError(f"{path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _meta(root: Path) -> dict[str, Any]:
    return dict(_checkpoint(root).get("metadata") or {})


def _run_state(root: Path, entity_id: str) -> Optional[dict[str, Any]]:
    state = (_meta(root).get("run_state") or {}).get(entity_id)
    return dict(state) if isinstance(state, dict) else None


def _approved_entries(root: Path) -> list[dict[str, Any]]:
    """Approved entries survive the pending phase of the NEXT character in
    checkpoint metadata (a packet is either pending or approved)."""
    cp = _checkpoint(root)
    packet = (cp.get("artifacts") or {}).get("headshot_packet") or {}
    if packet.get("state") == "approved":
        return [dict(e) for e in packet.get("characters") or [] if isinstance(e, dict)]
    return [dict(e) for e in (_meta(root).get("approved_entries") or []) if isinstance(e, dict)]


def _write(root: Path, packet: dict[str, Any], *, status: str, run_state: dict[str, Optional[dict]],
           approved_entries: Optional[list[dict]] = None, rejected: Optional[list[str]] = None,
           palette: Optional[list[str]] = None) -> str:
    """ONE checkpoint write carrying the packet and the run-state change."""
    from lib.checkpoint import checkpoint_digest, write_checkpoint

    meta = _meta(root)
    if palette is not None:
        pal = dict(meta.get("hero_palette") or {})
        for entity, state in run_state.items():
            if state is not None:
                pal[entity] = {"palette": list(palette), "look_hash": _look_hash_of(root, entity)}
        meta["hero_palette"] = pal
    if rejected:
        hist = dict(meta.get("rejected_candidates") or {})
        for entity in run_state:
            hist[entity] = sorted(set(hist.get(entity) or []) | set(rejected))
        meta["rejected_candidates"] = hist
    states = dict(meta.get("run_state") or {})
    for entity, state in run_state.items():
        if state is None:
            states.pop(entity, None)
        else:
            states[entity] = state
    meta["run_state"] = states
    revs = dict(meta.get("run_revisions") or {})
    for entity, state in run_state.items():
        if state is not None:
            revs[entity] = max(int(revs.get(entity, 0)), int(state.get("revision", 0)))
    meta["run_revisions"] = revs
    if approved_entries is not None:
        meta["approved_entries"] = approved_entries
    path = write_checkpoint(root.parent, root.name, STAGE, status, {"headshot_packet": packet},
                            pipeline_type="authored-film", human_approval_required=(status == "awaiting_human"), metadata=meta)
    return checkpoint_digest(path)


def _look_hash_of(root: Path, entity_id: str) -> Optional[str]:
    from lib.look_ingest import active_look_for

    look = active_look_for(root, "character", entity_id)
    return look.look_hash if look else None


def _next_revision(root: Path, entity_id: str) -> int:
    return int((_meta(root).get("run_revisions") or {}).get(entity_id, 0)) + 1


def _packet(state: str, entries: list[dict]) -> dict[str, Any]:
    return {"version": "1.1", "state": state, "characters": entries}


# ---- refs ----

def _image_ref(root: Path, asset_id: str, gen: dict, qc_receipt_id: Optional[str]) -> dict:
    if gen.get("generator_kind") == "imported":
        prov = {"generator_kind": "imported", "origin_tool": gen.get("origin_tool"), "attestation_receipt_id": gen.get("attestation_receipt_id"),
                "import_receipt_id": gen["receipt_id"], "normalized_pixel_hash": asset_id, "generation_receipt_id": gen["receipt_id"]}
    else:
        prov = {"generator_kind": "model", "model_endpoint": gen["model_endpoint"], "prompt": gen.get("prompt"),
                "generation_receipt_id": gen["receipt_id"]}
        if gen.get("seed") is not None:
            prov["seed"] = gen["seed"]
    ref = {"asset_id": asset_id, "path": f"canon/visual/objects/{asset_id}.png", "role": HERO_ROLE, "provenance": prov}
    if qc_receipt_id:
        ref["qc_receipt_id"] = qc_receipt_id
    return ref


def _look_ref(look) -> dict:
    return {"entity_kind": "character", "entity_id": look.entity_id, "look_hash": look.look_hash, "receipt_id": look.receipt_id}


# ---- the command ----

def run_headshot(
    project_root: Path | str, entity_id: str, *, import_file: Optional[Path | str] = None, origin_tool: Optional[str] = None,
    candidates: int = MAX_CANDIDATES, replace: bool = False, finish: bool = False, open_images: bool = False,
    grandfather: bool = False, retire: bool = False, palette: Optional[list[str]] = None,
    generate: Optional[Callable[[Path, dict[str, Any]], tuple[str, str]]] = None, judge_adapter: Any = None, out=None,
) -> dict[str, Any]:
    from lib.canon_enforcement import _is_hero_qc_manifest, _is_qc_manifest
    from lib.headshots import HeadshotError, active_headshots
    from lib.look_ingest import LookIngestError, active_look_for
    from lib.pipeline_pin import PipelinePinError, _read_marker, pinned_pipeline
    from lib.project_config import ProjectConfigError, load_verified_project_config
    from lib.sheet_qc import policy
    from tools.cost_tracker import resume_check

    root = Path(project_root).resolve()
    project_id = root.name
    entity_id = require_entity_id(entity_id)
    if not 1 <= int(candidates) <= MAX_CANDIDATES:
        raise HeadshotRunError(f"--candidates must be 1..{MAX_CANDIDATES}")
    try:
        config = load_verified_project_config(root)
        qc = config.require_hero_qc()
        pin = pinned_pipeline(root, str(_read_marker(root).get("pipeline_type") or "authored-film"))
    except (ProjectConfigError, PipelinePinError) as exc:
        raise HeadshotRunError(str(exc)) from exc
    if grandfather or retire:
        if not _is_qc_manifest(pin):
            raise HeadshotRunError(f"--grandfather / --retire run under authored-film 1.3 or 1.4; project is pinned to {pin.name}@{pin.version}")
    elif not _is_hero_qc_manifest(pin):
        raise HeadshotRunError(f"project is pinned to {pin.name}@{pin.version}; headshot_run needs authored-film 1.4 "
                               f"(grandfather every legacy hero, then approve the 1.4 pipeline_migration)")
    if qc.hero_policy_sha256 != policy.hero_bundle_sha256():
        raise HeadshotRunError("the signed config pins a different HERO policy bundle than this checkout; re-sign the config")
    if import_file is not None and not (isinstance(origin_tool, str) and origin_tool.strip()):
        raise HeadshotRunError("--import needs --origin-tool <name> (the tool that generated the image)")

    with hold_lease(root, config):
        resume_check(root)
        try:
            look = active_look_for(root, "character", entity_id)
        except LookIngestError as exc:
            raise HeadshotRunError(str(exc)) from exc
        if look is None:
            raise HeadshotRunError(f"{entity_id!r} has no active look; run look_run.py first")
        # Inspection #5: the palette is part of the prompt; once a run has used
        # one for this entity + look it is reused until the look changes.
        stored = ((_meta(root).get("hero_palette") or {}).get(entity_id) or {})
        if palette is None and stored.get("look_hash") == look.look_hash:
            palette = list(stored.get("palette") or [])
        ctx = {"root": root, "project_id": project_id, "entity_id": entity_id, "look": look, "config": config, "qc": qc,
               "pin": pin, "candidates": int(candidates), "open": open_images, "palette": palette or ["neutral grey"],
               "generate": generate or default_generate, "judge_adapter": judge_adapter, "out": out}
        state = _run_state(root, entity_id)
        if state is not None:
            return _resume(ctx, state)
        if finish:
            raise HeadshotRunError(f"nothing to finish: no run state for {entity_id!r}")
        # Inspection #2 / D18: one character at a time — another entity's
        # outstanding run owns the pending packet and the gate.
        others = {e: st for e, st in (_meta(root).get("run_state") or {}).items() if e != entity_id and isinstance(st, dict)}
        if others:
            e, st = next(iter(others.items()))
            raise HeadshotRunError(f"character {e!r} has an outstanding {st.get('mode')} request ({st.get('request_id')}); "
                                   f"finish or decline it before starting {entity_id!r}")
        try:
            current = active_headshots(root).get(entity_id)
        except HeadshotError as exc:
            raise HeadshotRunError(str(exc)) from exc
        if retire:
            return _start_retire(ctx, current)
        if grandfather:
            return _start_grandfather(ctx, current)
        if current is not None:
            if current.look_hash == look.look_hash and not replace:
                raise HeadshotRunError(
                    f"{entity_id!r} already has an approved hero (receipt {current.receipt_id}); re-run with --replace to "
                    f"supersede it — every sheet, storyboard and take built on it is invalidated"
                )
            _log(out, f"--replace: superseding hero {current.receipt_id}; {_downstream_cost(root, entity_id)}")
        if import_file is not None:
            return _start_import(ctx, Path(import_file), str(origin_tool))
        return _generate_and_present(ctx)


def _downstream_cost(root: Path, entity_id: str) -> str:
    try:
        cp = json.loads((root / "checkpoint_visual_bible.json").read_text(encoding="utf-8"))
        chars = ((cp.get("artifacts") or {}).get("visual_bible") or {}).get("characters") or []
        sheets = sum(1 for c in chars if isinstance(c, dict) and c.get("id") == entity_id and c.get("status") != "superseded")
    except (OSError, ValueError):
        sheets = 0
    return f"{sheets} approved sheet entr{'y' if sheets == 1 else 'ies'} plus every storyboard and take derived from the old hero will need regenerating"


# ---- Mode A: import ----

def _start_import(ctx, image: Path, origin_tool: str) -> dict[str, Any]:
    from lib.reference_import import ORIGIN_IMPORTED_SYNTHETIC, ReferenceImportError, normalize_reference_import, publish_import_request, stage_reference_upload

    root, entity_id, project_id, out = ctx["root"], ctx["entity_id"], ctx["project_id"], ctx["out"]
    if not image.is_file():
        raise HeadshotRunError(f"--import {image} is not a file")
    rev = _next_revision(root, entity_id)
    request_id = request_id_for("import", entity_id, rev)
    try:
        staged = stage_reference_upload(root, image)  # a copy; the writer's original is never consumed
        normalized = normalize_reference_import(root, staged, origin_class=ORIGIN_IMPORTED_SYNTHETIC, origin_tool=origin_tool, entity_id=entity_id)
    except ReferenceImportError as exc:
        raise HeadshotRunError(str(exc)) from exc
    state = {"mode": "import", "revision": rev, "request_id": request_id, "normalized_pixel_hash": normalized.normalized_pixel_hash,
             "normalized": {"width": normalized.width, "height": normalized.height, "source_format": normalized.source_format},
             "origin_tool": origin_tool, "expected_kind": "reference_import"}
    digest = _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: state})
    try:
        publish_import_request(root, project_id, normalized, request_id=request_id, source_checkpoint_digest=digest)
    except ReferenceImportError as exc:
        raise HeadshotRunError(str(exc)) from exc
    return _pending(root, entity_id, request_id, out, f"import staged for {entity_id!r} ({normalized.width}x{normalized.height}); the writer attests it at the gate")


def _current_pending_packet(root: Path) -> dict[str, Any]:
    """The packet to carry while nothing is being selected: the approved
    packet if one is on disk, else an empty approved packet (D18)."""
    cp = _checkpoint(root)
    packet = (cp.get("artifacts") or {}).get("headshot_packet")
    if isinstance(packet, dict) and packet.get("state") == "approved":
        return _packet("approved", [dict(e) for e in packet.get("characters") or []])
    return _packet("approved", _approved_entries(root))


def _finish_import(ctx, state, bound: str) -> dict[str, Any]:
    """The import was attested: finalize, judge it like any other candidate, present it alone."""
    from lib.qc_receipts import IMPORTED_SENTINEL
    from lib.receipts import find_generation
    from lib.reference_import import ORIGIN_IMPORTED_SYNTHETIC, ReferenceImportError, finalize_reference_import, reference_import_receipts, record_entity_id

    root, entity_id = ctx["root"], ctx["entity_id"]
    pixel_hash = str(state.get("normalized_pixel_hash") or "")
    att = None
    for r in reference_import_receipts(root):  # inspection #1: the receipt signed for THIS request (checkpoint digest)
        if r.get("normalized_pixel_hash") == pixel_hash and r.get("origin_class") == ORIGIN_IMPORTED_SYNTHETIC \
                and record_entity_id(r.get("record")) == entity_id and r.get("source_checkpoint_digest") == bound:
            att = r
    if att is None:
        raise HeadshotRunError(f"request {state['request_id']} is done but no verified imported_synthetic receipt signed for it binds {pixel_hash[:12]}… to {entity_id!r}")
    try:
        imported = finalize_reference_import(root, att["receipt_id"])
    except ReferenceImportError as exc:
        raise HeadshotRunError(str(exc)) from exc
    gen = find_generation(root, pixel_hash)
    if gen is None or gen.get("receipt_id") != imported.import_receipt_id:
        raise HeadshotRunError("imported generation receipt not found after finalize")
    series = _series(ctx, builder=IMPORTED_SENTINEL, endpoint=IMPORTED_SENTINEL)
    verdict = _judge_existing(ctx, series, pixel_hash, gen["receipt_id"])
    if verdict.get("verdict") != "pass" and not _overridden(root, verdict, entity_id):
        req = _write_override_request(root, ctx["project_id"], entity_id, verdict)
        # Inspection #10: keep a continuation so an accepted override presents THIS import on the next run
        blocked = {"mode": "import_blocked", "revision": int(state.get("revision") or 0), "request_id": req.stem,
                   "asset_id": pixel_hash, "qc_receipt_id": verdict["receipt_id"], "expected_kind": "qc_override"}
        _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: blocked})
        _log(ctx["out"], f"the imported image FAILED hero QC {verdict.get('failing_items')}. Accept the failed items at the gate "
                         f"(then rerun), or decline it and import another image:\n  {gate_command(root, req.stem)}")
        raise Blocked("import failed hero QC")
    return _present(ctx, [(pixel_hash, gen, verdict["receipt_id"])], recipe=None, replacing_state=state)


def _finish_import_blocked(ctx, state) -> dict[str, Any]:
    from lib import qc_receipts as qr
    from lib.receipts import find_generation

    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    verdict = qr.find_verdict_by_id(root, str(state.get("qc_receipt_id")))
    if verdict is None or verdict.get("asset_id") != state.get("asset_id"):
        raise HeadshotRunError("import_blocked state names a verdict that is not in the QC chain; refusing")
    if not _overridden(root, verdict, entity_id):
        return _pending(root, entity_id, str(state["request_id"]), out, "the import's override is signed but does not cover every failed item; decline it and import another image")
    gen = find_generation(root, str(state["asset_id"]))
    if gen is None:
        raise HeadshotRunError("imported generation receipt not found")
    return _present(ctx, [(str(state["asset_id"]), gen, verdict["receipt_id"])], recipe=None, replacing_state=state)


# ---- Mode C: generate ----

def _series(ctx, *, builder: Optional[str] = None, endpoint: Optional[str] = None, grandfather: Optional[bool] = None) -> dict:
    from tools import prompt_builder as pb

    look, qc = ctx["look"], ctx["qc"]
    key = {"entity_kind": "character", "entity_id": ctx["entity_id"], "role": HERO_ROLE, "look_hash": look.look_hash,
           "headshot_receipt_id": None, "policy_bundle_sha256": qc.hero_policy_sha256,
           "builder_policy_sha256": builder or pb.builder_policy_sha256(),
           "generation_endpoint": endpoint or GENERATION_ENDPOINT, "generation_model": endpoint or GENERATION_ENDPOINT,
           "judge_provider": qc.judge_provider, "judge_model": qc.judge_model}
    if grandfather:
        key["grandfather"] = True
    return key


def _budget(ctx) -> tuple[dict, int, int]:
    """(budget_key, cap, used)"""
    from lib import qc_receipts as qr

    key = qr.hero_budget_key(ctx["project_id"], ctx["entity_id"], ctx["look"].look_hash)
    used = len(qr.hero_attempts_started(ctx["root"], ctx["entity_id"], ctx["look"].look_hash))
    return key, int(ctx["qc"].max_hero_attempts), used


def _tracker(ctx):
    from lib.config_model import BudgetMode
    from tools.cost_tracker import CostTracker

    return CostTracker(budget_total_usd=float(ctx["config"].budget_usd_cap), reserve_pct=0.0, single_action_approval_usd=float("inf"),
                       require_approval_for_new_paid_tool=False, mode=BudgetMode.CAP, cost_log_path=ctx["root"] / "cost_log.json")


def _judge_existing(ctx, series: dict, asset_id: str, gen_receipt_id: str) -> dict:
    """Start a budgeted attempt over an EXISTING receipted asset (an import or
    a legacy hero), attach it, judge it. Returns the verdict row."""
    from lib import qc_receipts as qr
    from tools.qa.sheet_judge import SheetJudge

    root = ctx["root"]
    budget_key, cap, _ = _budget(ctx)
    series_sha = qr.series_sha256(dict(series, project_id=ctx["project_id"]))
    reuse = _passing_verdicts(root, series_sha, ctx["entity_id"], qr)
    for v in reuse:
        if v["asset_id"] == asset_id:
            return v
    open_ = qr.open_attempts(root, series_sha)
    if open_:
        attempt = open_[0]
        rows = qr.attempt_rows(root, attempt["attempt_id"])
        if rows["generation"] is None:
            qr.attach_generation(root, attempt["attempt_id"], generation_receipt_id=gen_receipt_id, asset_id=asset_id)
        elif rows["generation"].get("asset_id") != asset_id:
            raise HeadshotRunError(f"series has an open attempt on another asset {rows['generation'].get('asset_id')[:12]}; reconcile first")
    else:
        try:
            attempt = qr.start_attempt(root, series, max_attempts=cap, budget_key=budget_key, budget_cap=cap)
        except qr.AttemptCapExceeded as exc:
            raise Blocked(str(exc)) from exc
        qr.attach_generation(root, attempt["attempt_id"], generation_receipt_id=gen_receipt_id, asset_id=asset_id)
    r = SheetJudge(adapter=ctx["judge_adapter"]).execute({"project_dir": str(root), "attempt_id": attempt["attempt_id"], "asset_id": asset_id})
    if not r.success:
        raise HeadshotRunError(f"judge did not complete: {r.error}")
    verdict = qr.find_verdict_by_id(root, r.data["qc_receipt_id"])
    _log(ctx["out"], f"[hero] {asset_id[:12]}: {r.data['verdict'].upper()}"
                     + (f" {r.data.get('failing_items')}" if r.data["verdict"] != "pass" else f" (warnings: {', '.join(r.data.get('warnings') or []) or 'none'})"))
    return verdict


def _overridden(root: Path, verdict: dict, entity_id: str) -> bool:
    from lib.sheet_qc.verify import NON_OVERRIDABLE, overrides_for

    failing = set(verdict.get("failing_items") or [])
    if verdict.get("provider") == "local" or failing & NON_OVERRIDABLE:
        return False
    return failing <= overrides_for(root, verdict["receipt_id"], entity_id)


def _passing_verdicts(root: Path, series_sha: str, entity_id: str, qr) -> list[dict]:
    """Committed verdicts in this series that pass (or are fully overridden), oldest first."""
    out = []
    for a in qr.attempts_started(root, series_sha):
        rows = qr.attempt_rows(root, a["attempt_id"])
        if rows["verdict"] is None or rows.get("voided") is not None:
            continue
        v = qr.find_verdict_by_id(root, rows["verdict"]["qc_receipt_id"])
        if v is None or v.get("attempt_id") != a["attempt_id"] or v.get("provider") == "local":
            continue
        if v.get("verdict") == "pass" or _overridden(root, v, entity_id):
            out.append(v)
    return out


def _generate_and_present(ctx) -> dict[str, Any]:
    from lib import qc_receipts as qr
    from lib.receipts import find_generation
    from tools import prompt_builder as pb
    from tools.qa.sheet_judge import DEFAULT_RESERVE_USD, SheetJudge
    from tools.video import _shared

    root, entity_id, look, out = ctx["root"], ctx["entity_id"], ctx["look"], ctx["out"]
    built = pb.build_prompt(look.payload, role=HERO_ROLE, palette=ctx["palette"])  # once; never edited
    ctx["built"] = built
    series = _series(ctx)
    series_sha = qr.series_sha256(dict(series, project_id=ctx["project_id"]))
    budget_key, cap, used = _budget(ctx)
    want = ctx["candidates"]
    passing: dict[str, tuple[dict, str]] = {}  # asset_id -> (gen receipt, qc id); unique assets only (R1#15)
    rejected = set((_meta(root).get("rejected_candidates") or {}).get(entity_id) or [])  # history after a reject-all
    for v in _passing_verdicts(root, series_sha, entity_id, qr):
        if v["asset_id"] in rejected:
            continue
        if v["asset_id"] not in passing and (root / "canon/visual/objects" / f"{v['asset_id']}.png").is_file():
            gen = find_generation(root, v["asset_id"])
            if gen is not None:
                passing[v["asset_id"]] = (gen, v["receipt_id"])
    if passing:
        _log(out, f"[hero] reusing {len(passing)} passing candidate(s) from earlier attempts")
    if len(passing) > want:  # inspection #7: never present more than asked
        passing = dict(list(passing.items())[:want])
    tracker = _tracker(ctx)
    remaining = cap - used
    # Inspection #8 / D20.2 step 2: every remaining attempt must fit before the run starts.
    need = (remaining if len(passing) < want else 0) * (GENERATION_PRICE_USD + DEFAULT_RESERVE_USD)
    if need > tracker.budget_remaining_usd:
        raise HeadshotRunError(f"up to {remaining} attempt(s) could cost ${need:.2f}; only ${tracker.budget_remaining_usd:.2f} remains under the cap")
    judge = SheetJudge(adapter=ctx["judge_adapter"])
    failed: list[dict] = []
    while len(passing) < want:
        open_ = qr.open_attempts(root, series_sha)
        if open_:
            row = open_[0]
            gen_row = qr.attempt_rows(root, row["attempt_id"])["generation"]
            if gen_row is None:
                gen_row = _recover_attempt_generation(root, row, qr, out)
                if gen_row is None:
                    continue
            attempt, asset_id, gen_rid = row, gen_row["asset_id"], gen_row["generation_receipt_id"]
            _log(out, f"[hero] resuming open attempt on {asset_id[:12]}")
        else:
            _, cap, used = _budget(ctx)
            if used >= cap:
                break
            holder: dict[str, Any] = {}

            def _hook(_root: Path, reservation_id: str, _series=series, _holder=holder) -> None:
                _holder["row"] = qr.start_attempt(_root, _series, max_attempts=cap, generation_reservation_id=reservation_id,
                                                  budget_key=budget_key, budget_cap=cap)

            with _shared.pre_submit_hook(_hook):
                asset_id, gen_rid = ctx["generate"](root, ctx)
            attempt = holder.get("row")
            if attempt is None:
                raise HeadshotRunError("the generator did not run the pre-submit hook; no attempt was recorded")
            qr.attach_generation(root, attempt["attempt_id"], generation_receipt_id=gen_rid, asset_id=asset_id)
            _log(out, f"[hero] attempt {attempt['budget_n']}/{cap}: generated {asset_id[:12]}")
        r = judge.execute({"project_dir": str(root), "attempt_id": attempt["attempt_id"], "asset_id": asset_id})
        if not r.success:
            raise HeadshotRunError(f"judge did not complete: {r.error}")
        verdict = qr.find_verdict_by_id(root, r.data["qc_receipt_id"])
        if r.data["verdict"] == "pass":
            if asset_id in passing:
                # Inspection #6: identical pixels got a newer receipt and a REUSED verdict (bound to
                # the first attempt); the candidate keeps citing the first receipt, and every
                # verifier resolves the receipt by the cited id, never latest-for-hash.
                _log(out, f"[hero] PASS but duplicate pixels {asset_id[:12]} (attempt consumed, no new slot)")
            elif asset_id in rejected:
                _log(out, f"[hero] PASS but pixels {asset_id[:12]} were rejected earlier (attempt consumed, no new slot)")
            else:
                passing[asset_id] = (find_generation(root, asset_id), r.data["qc_receipt_id"])
                _log(out, f"[hero] PASS {asset_id[:12]} ({len(passing)}/{want}; warnings: {', '.join(r.data.get('warnings') or []) or 'none'})")
        else:
            failed.append(verdict)
            _log(out, f"[hero] FAIL {r.data['failing_items']}" + (f" — {r.data.get('note')}" if r.data.get("note") else ""))
    if not passing:
        best = min((v for v in failed if v.get("provider") != "local"), key=lambda v: len(v.get("failing_items") or []), default=None)
        req = _write_override_request(root, ctx["project_id"], entity_id, best) if best is not None else None
        _log(out, f"[hero] BLOCKED: the hero budget ({cap}) is spent with no passing candidate. Change the look (a ticket edit "
                  f"opens a new budget) or accept the judge's failed items:" + (f"\n  {gate_command(root, req.stem)}" if req else " (only deterministic failures; nothing to override)"))
        raise Blocked("no passing candidate")
    if len(passing) < want:
        _log(out, f"[hero] budget spent: presenting {len(passing)} of {want} requested")
    cands = [(a, g, q) for a, (g, q) in passing.items()]
    return _present(ctx, cands, recipe=built["prompt_recipe"], replacing_state=None)


def _recover_attempt_generation(root: Path, attempt: dict, qr, out) -> Optional[dict]:
    from lib.receipts import verified_generation_receipts
    from tools.cost_tracker import load_reservations

    rid = attempt.get("generation_reservation_id")
    res = load_reservations(root).get(rid or "")
    if res is None:
        raise HeadshotRunError(f"attempt {attempt['attempt_id']} names reservation {rid!r} which does not exist; reconcile by hand")
    state = res.get("state")
    if state == "failed":
        qr.void_attempt(root, attempt["attempt_id"], reason=f"reservation {rid} failed; no asset")
        _log(out, "[hero] open attempt voided (generation failed); it still counts toward the budget")
        return None
    if state == "completed" and res.get("provider_request_id"):
        for r in verified_generation_receipts(root):
            if r.get("provider_request_id") == res["provider_request_id"]:
                return qr.attach_generation(root, attempt["attempt_id"], generation_receipt_id=r["receipt_id"], asset_id=r["output_sha256"])
    raise HeadshotRunError(f"attempt {attempt['attempt_id']} is open with reservation {rid} in state {state!r}; run scripts/reconcile_paid_calls.py first")


# ---- present: pending packet 1.1 + run state + selection request ----

def _present(ctx, cands: list[tuple[str, dict, str]], *, recipe: Optional[dict], replacing_state: Optional[dict]) -> dict[str, Any]:
    from lib.headshots import headshot_request

    root, entity_id, look, out = ctx["root"], ctx["entity_id"], ctx["look"], ctx["out"]
    rev = int((replacing_state or {}).get("revision") or 0) + 1 if replacing_state else _next_revision(root, entity_id)
    request_id = request_id_for("headshot", entity_id, rev)
    entry = {"entity_kind": "character", "entity_id": entity_id, "look_ref": _look_ref(look),
             "candidates": [_image_ref(root, a, g, q) for a, g, q in cands]}
    if recipe is not None:
        entry["prompt_recipe"] = recipe
    notes = _rejection_notes(root, entity_id)
    if notes:
        entry["rejection_notes"] = notes
    state = {"mode": "select", "revision": rev, "request_id": request_id, "candidate_hashes": [a for a, _, _ in cands],
             "expected_kind": "headshot"}
    approved = _approved_entries(root)
    digest = _write(root, _packet("pending", [entry]), status="awaiting_human", run_state={entity_id: state}, approved_entries=approved,
                    palette=ctx["palette"] if recipe is not None else None)
    headshot_request(root, ctx["project_id"], entity_id, request_id=request_id, source_checkpoint_digest=digest)
    paths = [str(root / "canon/visual/objects" / f"{a}.png") for a, _, _ in cands]
    if ctx["open"] and sys.platform == "darwin":
        subprocess.run(["open", "-a", "Preview", *paths], check=False)
    _log(out, "Candidates (all passed hero QC):\n  " + "\n  ".join(paths))
    return _pending(root, entity_id, request_id, out, f"{len(cands)} candidate(s) presented for {entity_id!r} (revision {rev}); choose at the gate")


def _rejection_notes(root: Path, entity_id: str) -> list[str]:
    try:
        log = json.loads((root / "decision_log.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(d.get("reason")) for d in log.get("decisions") or []
            if isinstance(d, dict) and d.get("category") == "revision" and d.get("stage") == STAGE
            and str(d.get("subject") or "").startswith(f"reject-all {entity_id}")]


def _pending(root, entity_id, request_id, out, headline: str) -> dict[str, Any]:
    cmd = gate_command(root, request_id)
    _log(out, f"{headline}\nApprove in Terminal (y to sign; reject-all needs a note):\n  {cmd}")
    return {"entity_id": entity_id, "status": "pending", "request_id": request_id, "command": cmd}


def _write_override_request(root, project_id, entity_id, verdict) -> Path:
    d = root / ".gate-requests"; d.mkdir(exist_ok=True)
    for existing in sorted(d.glob(f"override-{entity_id}-hero-*.json")):
        try:
            if json.loads(existing.read_text()).get("qc_receipt_id") == verdict["receipt_id"]:
                return existing
        except ValueError:
            continue
    n = 1 + sum(1 for _ in d.glob(f"override-{entity_id}-hero-*.json"))
    req_id = f"override-{entity_id}-hero-{n}"
    req = {"request_id": req_id, "project_id": project_id, "stage": STAGE, "scope": f"character:{entity_id}",
           "kind": "qc_override", "entity_id": entity_id, "qc_receipt_id": verdict["receipt_id"],
           "item_ids": list(verdict.get("failing_items") or []), "reason": "",
           "summary": f"Accept failed hero QC items {verdict.get('failing_items')} on {entity_id} (attempt {verdict.get('attempt_n')})",
           "preview_paths": [f"canon/visual/objects/{verdict['asset_id']}.png"]}
    path = d / f"{req_id}.json"
    path.write_text(json.dumps(req, indent=2))
    return path


# ---- grandfather (D20.7) ----

def _start_grandfather(ctx, current) -> dict[str, Any]:
    from lib.headshots import record_version_of
    from lib.qc_receipts import IMPORTED_SENTINEL
    from lib.receipts import find_generation

    root, entity_id, look, out = ctx["root"], ctx["entity_id"], ctx["look"], ctx["out"]
    if current is None:
        raise HeadshotRunError(f"{entity_id!r} has no active headshot to grandfather")
    if record_version_of(current.record) != "1.0":
        raise HeadshotRunError(f"the active hero of {entity_id!r} is already a {record_version_of(current.record)} record; nothing to grandfather")
    if current.look_hash != look.look_hash:
        raise HeadshotRunError(f"the active hero of {entity_id!r} predates the active look; re-approve it with --replace under 1.4 instead")
    gen = find_generation(root, current.asset_id)
    if gen is None:
        raise HeadshotRunError(f"no verified generation receipt for the active hero asset {current.asset_id[:12]}")
    imported = gen.get("generator_kind") == "imported"
    series = _series(ctx, builder=IMPORTED_SENTINEL if imported else None, endpoint=IMPORTED_SENTINEL if imported else None, grandfather=True)
    verdict = _judge_existing(ctx, series, current.asset_id, gen["receipt_id"])
    if verdict.get("verdict") != "pass" and not _overridden(root, verdict, entity_id):
        req = _write_override_request(root, ctx["project_id"], entity_id, verdict)
        _log(out, f"the legacy hero FAILED hero QC {verdict.get('failing_items')}. Accept the failed items at the gate and rerun, "
                  f"or re-approve a new hero with --replace after migrating:\n  {gate_command(root, req.stem)}")
        raise Blocked("legacy hero failed hero QC")
    rev = _next_revision(root, entity_id)
    request_id = request_id_for("grandfather", entity_id, rev)
    state = {"mode": "grandfather", "revision": rev, "request_id": request_id, "asset_id": current.asset_id,
             "legacy_receipt_id": current.receipt_id, "qc_receipt_id": verdict["receipt_id"], "expected_kind": "headshot_grandfather"}
    digest = _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: state})
    _write_grandfather_request(root, ctx["project_id"], entity_id, request_id, verdict["receipt_id"], current, digest)
    return _pending(root, entity_id, request_id, out, f"legacy hero of {entity_id!r} passed hero QC; attest it at the gate")


def _write_grandfather_request(root, project_id, entity_id, request_id, qc_receipt_id, current, digest) -> Path:
    d = root / ".gate-requests"; d.mkdir(exist_ok=True)
    req = {"request_id": request_id, "project_id": project_id, "stage": STAGE, "scope": f"character:{entity_id}",
           "kind": "headshot_grandfather", "entity_id": entity_id, "artifact": None, "approval_record": None,
           "qc_receipt_id": qc_receipt_id, "source_checkpoint_digest": digest,
           "summary": f"Attest that {entity_id}'s existing hero (receipt {current.receipt_id}) passed the hero judge (verdict {qc_receipt_id}). "
                      f"The legacy receipt stays the chain tip; existing sheets stay valid.",
           "preview_paths": [f"canon/visual/objects/{current.asset_id}.png"]}
    path = d / f"{request_id}.json"
    path.write_text(json.dumps(req, indent=2))
    return path


# ---- resume: the five states, per mode ----

def _resume(ctx, state: dict) -> dict[str, Any]:
    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    request_id, mode = str(state.get("request_id") or ""), state.get("mode")
    if mode not in ("import", "select", "grandfather", "retire", "import_blocked") or not request_id:
        raise HeadshotRunError(f"run state for {entity_id!r} is malformed: {state}")
    where, req = read_request(root, request_id)
    if where == "pending":
        return _pending(root, entity_id, request_id, out, f"request {request_id} is still waiting for the writer")
    if where == "declined":
        return _declined(ctx, state, req or {})
    if where == "missing":
        return _republish(ctx, state)
    if req is None or req.get("kind") != state.get("expected_kind"):
        raise HeadshotRunError(f"run state names request {request_id} ({state.get('expected_kind')}); the done request has another kind — refusing to guess")
    if mode == "import_blocked":
        return _finish_import_blocked(ctx, state)
    bound = req.get("source_checkpoint_digest")
    if not isinstance(bound, str) or len(bound) != 64:
        raise HeadshotRunError(f"done request {request_id} carries no source_checkpoint_digest; it was not published by headshot_run — refusing")
    if mode == "import":
        return _finish_import(ctx, state, bound)
    if mode == "grandfather":
        return _finish_grandfather(ctx, state, bound)
    if mode == "retire":
        return _finish_retire(ctx, state)
    return _finish_select(ctx, state, bound)


def _declined(ctx, state, req) -> dict[str, Any]:
    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    note = str(req.get("declined_note") or "").strip() or "(no note)"
    mode = state["mode"]
    if mode == "select":
        if any(w in note.lower() for w in LOOK_CHANGE_WORDS):
            raise HeadshotRunError(
                f"the reject-all note reads as a look change ({note!r}); that is decided in the ticket — edit it and run "
                f"look_run.py --supersede. The prompt is never changed from a note."
            )
        write_decision(root, stage=STAGE, category="revision", subject=f"reject-all {entity_id} revision {state.get('revision')}",
                       reason=note, selected="reject-all", user_approved=True)
        _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: None},
               rejected=list(state.get("candidate_hashes") or []))
        _log(out, f"reject-all consumed ({note!r}); regenerating with the same prompt against the same budget")
        return _generate_and_present(ctx)
    write_decision(root, stage=STAGE, category="revision", subject=f"{mode} {state['request_id']} declined for {entity_id}",
                   reason=note, selected="declined", user_approved=True)
    _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: None})
    _log(out, f"declined: {note}")
    raise Declined(note)


# ---- retire (D20.7, inspection #4) ----

def _start_retire(ctx, current) -> dict[str, Any]:
    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    if current is None:
        raise HeadshotRunError(f"{entity_id!r} has no active headshot to retire")
    rev = _next_revision(root, entity_id)
    request_id = request_id_for("retire", entity_id, rev)
    state = {"mode": "retire", "revision": rev, "request_id": request_id, "legacy_receipt_id": current.receipt_id, "expected_kind": "headshot"}
    digest = _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: state})
    d = root / ".gate-requests"; d.mkdir(exist_ok=True)
    req = {"request_id": request_id, "project_id": ctx["project_id"], "stage": STAGE, "scope": f"character:{entity_id}",
           "kind": "headshot", "entity_id": entity_id, "artifact": None, "approval_record": None,
           "envelope": {"action": "retire", "entity_kind": "character", "look_hash": current.look_hash, "supersedes_receipt_id": current.receipt_id},
           "source_checkpoint_digest": digest,
           "summary": f"Retire {entity_id}'s active hero (receipt {current.receipt_id}) with no replacement. {_downstream_cost(root, entity_id)}.",
           "preview_paths": [f"canon/visual/objects/{current.asset_id}.png"]}
    (d / f"{request_id}.json").write_text(json.dumps(req, indent=2))
    return _pending(root, entity_id, request_id, out, f"retire request written for {entity_id!r}; {_downstream_cost(root, entity_id)}")


def _finish_retire(ctx, state) -> dict[str, Any]:
    from lib.headshots import active_headshots

    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    if entity_id in active_headshots(root):
        raise HeadshotRunError(f"request {state['request_id']} is done but {entity_id!r} still has an active hero; refusing")
    _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: None})
    _log(out, f"hero of {entity_id!r} retired; generate a new one after the 1.4 migration with headshot_run.py")
    return {"entity_id": entity_id, "status": "retired"}


def _republish(ctx, state) -> dict[str, Any]:
    from lib.headshot_verify import HeadshotVerifyError, verify_headshot_candidate
    from lib.headshots import headshot_request
    from lib.reference_import import (
        ORIGIN_IMPORTED_SYNTHETIC, NormalizedImport, ReferenceImportError, STAGING_DIR, import_record, publish_import_request,
    )

    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    request_id = state["request_id"]
    if state["mode"] == "import":
        pixel_hash = str(state.get("normalized_pixel_hash") or "")
        staged = root / STAGING_DIR / f"reference-{pixel_hash}.png"
        from lib.pathsafe import sha256_file
        if not staged.is_file() or sha256_file(staged) != pixel_hash:
            raise HeadshotRunError(f"staged import for {request_id} is missing or altered; rerun --import with the image")
        dims = state.get("normalized") or {}
        try:
            normalized = NormalizedImport(ORIGIN_IMPORTED_SYNTHETIC, pixel_hash, staged,
                                          import_record(ORIGIN_IMPORTED_SYNTHETIC, pixel_hash, origin_tool=str(state.get("origin_tool")), entity_id=entity_id),
                                          int(dims.get("width") or 0), int(dims.get("height") or 0), str(dims.get("source_format") or "png"))
            digest = _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: state})
            publish_import_request(root, ctx["project_id"], normalized, request_id=request_id, source_checkpoint_digest=digest)
        except ReferenceImportError as exc:
            raise HeadshotRunError(str(exc)) from exc
        return _pending(root, entity_id, request_id, out, f"republished {request_id}")
    if state["mode"] == "grandfather":
        from lib.headshots import active_headshots
        current = active_headshots(root).get(entity_id)
        if current is None or current.receipt_id != state.get("legacy_receipt_id") or current.asset_id != state.get("asset_id"):
            raise HeadshotRunError(f"run state names legacy receipt {state.get('legacy_receipt_id')} but the active hero changed; refusing")
        digest = _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: state})
        _write_grandfather_request(root, ctx["project_id"], entity_id, request_id, str(state.get("qc_receipt_id")), current, digest)
        return _pending(root, entity_id, request_id, out, f"republished {request_id}")
    # select (R6#3): re-validate the checkpoint digest, every candidate hash and its verdict before republishing
    cp = _checkpoint(root)
    packet = (cp.get("artifacts") or {}).get("headshot_packet") or {}
    entry = next((e for e in packet.get("characters") or [] if isinstance(e, dict) and e.get("entity_id") == entity_id), None)
    if cp.get("status") != "awaiting_human" or packet.get("state") != "pending" or entry is None:
        raise HeadshotRunError(f"run state names selection request {request_id} but no pending packet for {entity_id!r} is on disk; refusing")
    hashes = [c.get("asset_id") for c in entry.get("candidates") or []]
    if hashes != list(state.get("candidate_hashes") or []):
        raise HeadshotRunError("pending candidates differ from the run state's candidate hashes; refusing to republish")
    for cand in entry.get("candidates") or []:
        try:
            verify_headshot_candidate(root, entry, cand, active_look=ctx["look"], config=ctx["config"], pin=ctx["pin"])
        except HeadshotVerifyError as exc:
            raise HeadshotRunError(f"candidate {str(cand.get('asset_id'))[:12]} no longer verifies: {exc}") from exc
    from lib.checkpoint import checkpoint_digest
    digest = checkpoint_digest(root / f"checkpoint_{STAGE}.json")
    path = headshot_request(root, ctx["project_id"], entity_id, request_id=request_id)
    req = json.loads(path.read_text()); req["source_checkpoint_digest"] = digest
    path.write_text(json.dumps(req, indent=2), encoding="utf-8")
    return _pending(root, entity_id, request_id, out, f"republished {request_id}")


def _finish_select(ctx, state, bound: str) -> dict[str, Any]:
    from lib.headshots import HeadshotError, active_headshots, headshot_receipts
    from lib.receipts import find_generation

    root, entity_id, look, out = ctx["root"], ctx["entity_id"], ctx["look"], ctx["out"]
    try:
        current = active_headshots(root).get(entity_id)
    except HeadshotError as exc:
        raise HeadshotRunError(str(exc)) from exc
    hashes = list(state.get("candidate_hashes") or [])
    if current is None or current.asset_id not in hashes or current.look_hash != look.look_hash:
        raise HeadshotRunError(f"request {state['request_id']} is done but the active hero for {entity_id!r} is not one of its candidates")
    row = next((r for r in headshot_receipts(root) if r.get("receipt_id") == current.receipt_id), None)
    if row is None or row.get("source_checkpoint_digest") != bound or (current.record or {}).get("candidates_checkpoint_digest") != bound:
        raise HeadshotRunError(f"the active hero receipt {current.receipt_id} was not signed for request {state['request_id']} "
                               f"(checkpoint digest mismatch); refusing to finish another request's selection")
    rec = current.record
    from lib.receipts import find_generation_by_id
    gen = find_generation_by_id(root, str(rec.get("generation_receipt_id")), output_sha256=current.asset_id) if rec.get("generation_receipt_id") else None
    gen = gen or find_generation(root, current.asset_id)
    if gen is None:
        raise HeadshotRunError("no generation receipt for the approved hero")
    cp = _checkpoint(root)
    pending = (cp.get("artifacts") or {}).get("headshot_packet") or {}
    prev = next((e for e in pending.get("characters") or [] if isinstance(e, dict) and e.get("entity_id") == entity_id), {}) if pending.get("state") == "pending" else {}
    entry = {"entity_kind": "character", "entity_id": entity_id, "look_ref": _look_ref(look),
             "hero": _image_ref(root, current.asset_id, gen, rec.get("qc_receipt_id")), "origin": rec.get("origin"),
             "normalized_pixel_hash": current.asset_id, "approval_receipt_id": current.receipt_id,
             "candidates_checkpoint_digest": rec.get("candidates_checkpoint_digest"),
             "candidates_rejected": [h for h in hashes if h != current.asset_id], "qc_receipt_id": rec.get("qc_receipt_id")}
    if rec.get("import_receipt_id"):
        entry["import_receipt_id"] = rec["import_receipt_id"]
    if prev.get("prompt_recipe") is not None and rec.get("origin") == "generated":
        entry["prompt_recipe"] = prev["prompt_recipe"]
    if prev.get("rejection_notes"):
        entry["rejection_notes"] = prev["rejection_notes"]
    approved = [e for e in _approved_entries(root) if e.get("entity_id") != entity_id] + [entry]
    _write(root, _packet("approved", approved), status="in_progress", run_state={entity_id: None}, approved_entries=approved,
           rejected=entry["candidates_rejected"])  # inspection #7: rejected candidates are history
    try:
        from lib.canon_view import build_view
        build_view(root)
    except Exception:  # noqa: BLE001
        pass
    _log(out, f"hero approved for {entity_id!r} (receipt {current.receipt_id}, asset {current.asset_id[:12]}…)\n"
              f"Next: cd {REPO} && .venv/bin/python scripts/sheet_run.py --project {ctx['project_id']} --entity {entity_id} --open")
    return {"entity_id": entity_id, "status": "approved", "receipt_id": current.receipt_id, "asset_id": current.asset_id}


def _finish_grandfather(ctx, state, bound: str) -> dict[str, Any]:
    from lib.headshot_verify import grandfather_receipt_for
    from lib.headshots import active_headshots

    root, entity_id, out = ctx["root"], ctx["entity_id"], ctx["out"]
    current = active_headshots(root).get(entity_id)
    if current is None or current.receipt_id != state.get("legacy_receipt_id"):
        raise HeadshotRunError("the active hero changed since the grandfather request; refusing")
    gf = grandfather_receipt_for(root, current)
    if gf is None or (gf.get("record") or {}).get("qc_receipt_id") != state.get("qc_receipt_id") or gf.get("source_checkpoint_digest") != bound:
        raise HeadshotRunError(f"request {state['request_id']} is done but no grandfather receipt attests {current.receipt_id} with verdict {state.get('qc_receipt_id')}")
    _write(root, _current_pending_packet(root), status="in_progress", run_state={entity_id: None})
    _log(out, f"legacy hero of {entity_id!r} attested (receipt {gf['receipt_id']}). When every cast hero is attested, "
              f"request the 1.4 migration: lib.pipeline_pin.prepare_migration_request(…, '1.4')")
    return {"entity_id": entity_id, "status": "grandfathered", "receipt_id": gf["receipt_id"]}


def main(argv: Optional[list[str]] = None) -> int:
    from lib.env_loader import load_env

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--import", dest="import_file", help="the writer's own generated image of the character")
    ap.add_argument("--origin-tool", help="the tool that generated --import")
    ap.add_argument("--candidates", type=int, default=MAX_CANDIDATES)
    ap.add_argument("--palette", help="comma list of background hues")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--finish", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--grandfather", action="store_true")
    ap.add_argument("--retire", action="store_true", help="retire the active hero with no replacement (see D20.7)")
    a = ap.parse_args(argv)
    load_env()
    try:
        root = resolve_project_root(a.project)
        run_headshot(root, a.entity, import_file=a.import_file, origin_tool=a.origin_tool, candidates=a.candidates,
                     replace=a.replace, finish=a.finish, open_images=a.open, grandfather=a.grandfather, retire=a.retire,
                     palette=a.palette.split(",") if a.palette else None)
    except Blocked:
        return EXIT_BLOCKED
    except Declined:
        return EXIT_DECLINED
    except RunError as exc:
        print(f"headshot_run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

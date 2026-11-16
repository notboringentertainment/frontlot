"""D19.2 — SheetJudge: an independent vision judge for character sheets.

Runs ONLY inside an open signed attempt (``qc_call_context``). Everything it
shows the provider comes from signed state: the asset bytes are read once
through ``lib.sheet_qc.local_checks.read_asset_bytes`` (O_NOFOLLOW, hashed,
must equal the asset id), the hero from the active headshot chain tip, the
wardrobe list from the active look, the questions from the policy bundle
pinned in the signed config. The provider answers a JSON schema; code scores
it (``lib.sheet_qc.scoring``); the verdict is signed into the QC stream
(``lib.qc_receipts``) bound to the full evaluation tuple, with the raw
provider response stored content-addressed and its hash signed.

Paid-call discipline (Codex R3#4 / R4): claim → submit → commit. The claim row
(QC WAL) carries the client idempotency key BEFORE any network I/O; the
provider's response id is persisted the moment it is returned (background
mode) and attached to the cost reservation; a crash or timeout leaves a
``submitted``/``unknown`` WAL row that ``scripts/reconcile_paid_calls.py``
resolves — never a blind resubmission. One verdict per evaluation tuple: a
second call for identical pixels under the same look/headshot/policy/judge
reuses the committed verdict and makes no provider call.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib.canonical_json import record_sha256
from tools.base_tool import (
    BaseTool, Determinism, ExecutionMode, ToolResult, ToolRuntime, ToolStability, ToolTier,
)

OBJECTS_SUBDIR = Path("canon") / "visual" / "objects"
QC_OBJECTS_SUBDIR = Path("canon") / "qc" / "objects"
TEST_MODE_ENV = "OPENMONTAGE_TEST_MODE"
DEFAULT_RESERVE_USD = 0.05


class JudgeAdapter:
    """Provider contract every adapter must satisfy (and prove with the probe):
    ``submit`` returns a provider id BEFORE the answer exists; ``wait`` polls
    that id to a terminal result ``{status, output_text, model, usage}``."""

    provider = "abstract"

    def submit(self, *, model: str, system: str, user: str, images: list[bytes], schema: dict) -> str:  # pragma: no cover
        raise NotImplementedError

    def wait(self, provider_id: str, *, deadline_s: float, poll_s: float) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class OpenAIResponsesAdapter(JudgeAdapter):
    provider = "openai"
    BASE = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _data_url(png: bytes) -> str:
        import base64
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    def submit(self, *, model: str, system: str, user: str, images: list[bytes], schema: dict) -> str:
        import requests

        content: list[dict[str, Any]] = [{"type": "input_text", "text": user}]
        for i, png in enumerate(images):
            content.append({"type": "input_image", "image_url": self._data_url(png), "detail": "high" if i == 0 else "low"})
        body = {
            # gpt-5.x reasoning models reject ``temperature``; determinism comes
            # from the fixed prompt + strict schema + code-side scoring.
            "model": model, "background": True, "max_output_tokens": 1200, "store": True,
            "instructions": system,
            "text": {"format": {"type": "json_schema", "name": "sheet_qc", "strict": True, "schema": schema}},
            "input": [{"role": "user", "content": content}],
        }
        resp = requests.post(self.BASE, headers=self._headers, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        rid = data.get("id")
        if not isinstance(rid, str) or not rid:
            raise RuntimeError("OpenAI Responses returned no response id")
        return rid

    def wait(self, provider_id: str, *, deadline_s: float, poll_s: float) -> dict[str, Any]:
        import requests

        start = time.monotonic()
        while True:
            resp = requests.get(f"{self.BASE}/{provider_id}", headers=self._headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status in ("queued", "in_progress"):
                if time.monotonic() - start > deadline_s:
                    raise TimeoutError(f"OpenAI response {provider_id} still {status} after {deadline_s}s")
                time.sleep(poll_s)
                continue
            text = ""
            for o in data.get("output") or []:
                for c in o.get("content") or []:
                    if c.get("type") == "output_text":
                        text += c.get("text") or ""
            return {"status": status, "output_text": text, "model": data.get("model"),
                    "usage": data.get("usage"), "error": data.get("error"), "raw": data}


def _persist_provider_id(root: Path, tuple_sha: str, reservation_id: str, provider_id: str, *, state: str, error: str | None = None) -> None:
    """Write the provider id to the reservation log AND the QC WAL, each in its
    own try, so a failure of one store never loses the id in the other. If
    both fail the exception propagates (the caller's failure path)."""
    from lib import gates
    from tools.cost_tracker import attach_request_id, load_reservations

    errors = []
    try:
        res = load_reservations(root).get(reservation_id) or {}
        if res.get("provider_request_id") != provider_id:
            attach_request_id(root, reservation_id, provider_id)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
    try:
        wal = gates.qc_wal_read(tuple_sha) or {}
        wal.update({"state": state, "provider_request_id": provider_id})
        if error:
            wal["error"] = error
        gates.qc_wal_write(tuple_sha, wal)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
    if len(errors) == 2:
        raise RuntimeError(f"could not persist provider id {provider_id} anywhere: {errors[0]}; {errors[1]}")


def commit_result(root: Path, wal: dict[str, Any], result: dict[str, Any], tracker: Any) -> tuple[dict, dict, str]:
    """Turn a provider result into a signed verdict for the evaluation the WAL
    row describes (used by SheetJudge and by the reconciler). Stores the raw
    response content-addressed, scores in code, records the verdict, attaches
    it to the attempt, settles the reservation, deletes the WAL row."""
    from lib import gates, qc_receipts
    from lib.sheet_qc import scoring
    from tools.cost_tracker import reconcile_paid_call

    evaluation = dict(wal["evaluation"])
    tuple_sha = wal["tuple_sha256"]
    role = evaluation["role"]
    raw_bytes = json.dumps(result.get("raw", result), sort_keys=True).encode("utf-8")
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    qc_dir = root / QC_OBJECTS_SUBDIR
    qc_dir.mkdir(parents=True, exist_ok=True)
    raw_path = qc_dir / f"{raw_sha}.json"
    if not raw_path.exists():
        tmp = raw_path.with_suffix(".tmp")
        tmp.write_bytes(raw_bytes)
        os.replace(tmp, raw_path)
    answers: Any = None
    if result.get("status") == "completed" and result.get("output_text"):
        try:
            answers = (json.loads(result["output_text"]) or {}).get("items")
        except ValueError:
            answers = None
    sc = scoring.score(role, answers)
    reserved = 0.0
    from tools.cost_tracker import load_reservations
    res = load_reservations(root).get(wal.get("reservation_id") or "")
    if res is not None:
        reserved = float(res.get("reserved_usd") or 0.0)
    v = dict(evaluation, provider_request_id=wal.get("provider_request_id"), provider_model_version=result.get("model"),
             raw_response_asset_id=raw_sha, items=sc["items"], verdict=sc["verdict"],
             failing_items=sc["failing_items"], warnings=sc["warnings"], cost_usd=round(reserved, 6))
    v["tuple_sha256"] = tuple_sha
    with gates.receipt_lock(evaluation["project_id"], qc_receipts.STREAM):
        row = qc_receipts.record_verdict(root, v)
        qc_receipts.attach_verdict(root, wal["attempt_id"], qc_receipt_id=row["receipt_id"])
        if res is not None and res.get("state") not in ("completed", "failed"):
            reconcile_paid_call(root, wal["reservation_id"], reserved, "completed", tracker)
        gates.qc_wal_delete(tuple_sha)
    return row, sc, raw_sha


def void_claim(root: Path, wal: dict[str, Any], tracker: Any, *, reason: str) -> None:
    """A claimed/unknown judge call with no provider id and no answer after the
    grace period (Codex R3#4): settle the reservation as spent (bounded loss
    of one judge call, never a pass), release the tuple, keep the attempt
    open for the run to re-judge."""
    from lib import gates
    from tools.cost_tracker import load_reservations, reconcile_paid_call

    res = load_reservations(root).get(wal.get("reservation_id") or "")
    if res is not None and res.get("state") not in ("completed", "failed"):
        reconcile_paid_call(root, wal["reservation_id"], float(res.get("reserved_usd") or 0.0), "completed", tracker)
    wal = dict(wal, state="voided_unconfirmed", voided_reason=reason)
    gates.qc_wal_write(wal["tuple_sha256"], wal)
    gates.qc_wal_delete(wal["tuple_sha256"])


class SheetJudge(BaseTool):
    name = "sheet_judge"
    version = "1.0.0"
    tier = ToolTier.ANALYZE
    capability = "sheet_qc"
    provider = "openai"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API
    dependencies = ["env:OPENAI_API_KEY"]
    install_instructions = "Set OPENAI_API_KEY in .env (the judge provider named in project.yaml qc.judge_provider)."
    capabilities = ["sheet_qc"]
    best_for = ["D19: pass/fail a character sheet against the pinned QC policy before a human is asked"]
    not_good_for = ["judging likeness or taste (the human gate does that)", "anything outside an open signed attempt"]
    governance_bound = False  # not a look-governed generation; it has its own boundary (qc_call_context)
    emits_generation_receipt = False
    input_schema = {
        "type": "object",
        "required": ["project_dir", "attempt_id", "asset_id"],
        "properties": {
            "project_dir": {"type": "string"},
            "attempt_id": {"type": "string", "description": "an OPEN signed attempt_started row of this project"},
            "asset_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "deadline_s": {"type": "number", "default": 300},
            "poll_s": {"type": "number", "default": 3},
        },
    }
    idempotency_key_fields = ["attempt_id", "asset_id"]

    def __init__(self, adapter: Optional[JudgeAdapter] = None) -> None:
        self._adapter = adapter

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return DEFAULT_RESERVE_USD

    # ---- adapter resolution ----

    def _resolve_adapter(self, project_root: Path, provider: str) -> JudgeAdapter:
        if self._adapter is not None:
            # Codex R1#9: a fake judge can never produce a production verdict.
            import tempfile

            from lib import paths as _paths
            tmp = Path(tempfile.gettempdir()).resolve()
            under_tmp = False
            try:
                Path(_paths.PROJECTS_DIR).resolve().relative_to(tmp)
                project_root.resolve().relative_to(tmp)
                under_tmp = True
            except ValueError:
                pass
            if os.environ.get(TEST_MODE_ENV) != "1" or not under_tmp:
                raise RuntimeError(
                    "an injected judge adapter is allowed only under OPENMONTAGE_TEST_MODE=1 with PROJECTS_DIR and the "
                    "project both under the system temp dir (test worlds), never for a production projects root"
                )
            if self._adapter.provider != provider:
                raise RuntimeError(f"injected adapter is {self._adapter.provider!r}; signed config names {provider!r}")
            return self._adapter
        if provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            return OpenAIResponsesAdapter(key)
        raise RuntimeError(f"no judge adapter for provider {provider!r}")

    # ---- execute ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from lib import gates, qc_receipts
        from lib.headshots import active_headshots
        from lib.look_ingest import active_look_for
        from lib.sheet_qc import local_checks, policy, scoring
        from tools.base_tool import normalized_inputs_hash
        from tools.cost_tracker import attach_request_id, reconcile_paid_call, reserve_paid_call
        from tools.video import _shared

        start = time.time()
        try:
            root, tracker, config, qc, started = _shared.qc_call_context(inputs)
            key = started["series_key"]
            entity_kind, entity_id, role = key["entity_kind"], key["entity_id"], key["role"]
            asset_id = str(inputs.get("asset_id") or "")
            is_hero = policy.is_hero_role(role)
            # D20: the role picks the bundle; each is pinned separately in the signed config.
            pinned_bundle = qc.hero_policy_sha256 if is_hero else qc.policy_bundle_sha256
            local_bundle = policy.bundle_sha256_for_role(role)
            if pinned_bundle != local_bundle:
                raise RuntimeError(
                    f"signed config pins {'hero' if is_hero else 'sheet'} policy bundle {str(pinned_bundle)[:12]} but this "
                    f"checkout's bundle is {local_bundle[:12]} — re-sign the config for the new policy"
                )
            # Signed state, never caller text.
            look = active_look_for(root, entity_kind, entity_id)
            if look is None or look.look_hash != key["look_hash"]:
                raise RuntimeError(f"attempt series look {key['look_hash']} is not the active look for {entity_id!r}")
            hero_asset = hero_receipt = None
            if is_hero:
                # R1#8: the hero is what is being chosen; the series names no headshot.
                if key.get("headshot_receipt_id") is not None:
                    raise RuntimeError("a hero attempt series must name no headshot")
            elif entity_kind == "character":
                heads = active_headshots(root)
                current = heads.get(entity_id) if isinstance(heads, dict) else None
                if current is None or current.receipt_id != key["headshot_receipt_id"]:
                    raise RuntimeError(f"attempt series headshot receipt is not the active headshot tip for {entity_id!r}")
                hero_asset, hero_receipt = current.asset_id, current.receipt_id
            gen_row = qc_receipts.attempt_rows(root, started["attempt_id"])["generation"]
            if gen_row is None or gen_row.get("asset_id") != asset_id:
                raise RuntimeError("the attempt has no generation_attached row for this asset; attach the generation receipt first")
            from lib.receipts import find_generation
            from lib.sheet_qc.verify import SeriesMismatch, verify_series_against_receipt
            gen_receipt = find_generation(root, asset_id)
            if gen_receipt is None or gen_receipt.get("receipt_id") != gen_row.get("generation_receipt_id"):
                raise RuntimeError("generation_attached names a receipt that is not the asset's verified generation receipt")
            try:
                verify_series_against_receipt(key, gen_receipt, asset_id=asset_id)
            except SeriesMismatch as exc:
                raise RuntimeError(f"attempt series does not match the sealed generation receipt: {exc}") from exc
            adapter = self._resolve_adapter(root, qc.judge_provider)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"sheet_judge preflight failed: {exc}")

        objects_dir = root / OBJECTS_SUBDIR
        judged_at = datetime.now(timezone.utc).isoformat()
        base = {
            "version": "1.0", "project_id": started["project_id"], "entity_kind": entity_kind, "entity_id": entity_id,
            "role": role, "asset_id": asset_id, "look_hash": look.look_hash, "look_receipt_id": look.receipt_id,
            "headshot_asset_id": hero_asset, "headshot_receipt_id": hero_receipt,
            "policy_version": policy.QC_POLICY_VERSION, "policy_bundle_sha256": pinned_bundle,
            "attempt_id": started["attempt_id"], "attempt_n": int(started["attempt_n"]), "judged_at": judged_at,
        }

        # ---- deterministic pre-checks: a failure is a signed local verdict, never a provider call ----
        try:
            data = local_checks.read_asset_bytes(objects_dir, asset_id)
            dims = local_checks.check_image(role, data)
        except local_checks.LocalCheckError as exc:
            v = dict(base, provider="local", model="local_checks", prompt_sha256=record_sha256(policy.local_rules_for(role)),
                     provider_request_id=None, provider_model_version=None, raw_response_asset_id=None,
                     items=[{"id": exc.item, "answer": "no", "note": str(exc)[:400], "severity": "fail"}],
                     verdict="fail", failing_items=[exc.item], warnings=[], cost_usd=0.0)
            v["tuple_sha256"] = qc_receipts.tuple_sha256(v)
            row = qc_receipts.record_verdict(root, v)
            qc_receipts.attach_verdict(root, started["attempt_id"], qc_receipt_id=row["receipt_id"])
            return ToolResult(success=True, data={"verdict": "fail", "failing_items": [exc.item], "local": True,
                                                  "qc_receipt_id": row["receipt_id"], "note": str(exc)},
                              cost_usd=0.0, duration_seconds=time.time() - start)

        items = policy.checklist(role)
        needs_hero = any(i.evidence == "hero_image" for i in items) and hero_asset is not None
        images = [data]
        if needs_hero:
            images.append(local_checks.read_asset_bytes(objects_dir, hero_asset))
        pieces = list(((look.payload.get("default_wardrobe") or {}).get("pieces")) or []) if any(i.evidence == "wardrobe_pieces" for i in items) else None
        look_fields = policy.look_evidence(role, look.payload) if is_hero else None
        user = policy.judge_prompt(role, wardrobe_pieces=pieces, has_hero=needs_hero, look_fields=look_fields)
        system = policy.SYSTEM_PROMPT
        schema = policy.response_schema(role)
        prompt_sha = hashlib.sha256((system + "\n\n" + user).encode("utf-8")).hexdigest()
        evaluation = dict(base, provider=qc.judge_provider, model=qc.judge_model, prompt_sha256=prompt_sha)
        tuple_sha = qc_receipts.tuple_sha256(evaluation)
        pid = started["project_id"]

        # ---- claim (under the QC lock), then release before network I/O ----
        try:
            with gates.receipt_lock(pid, qc_receipts.STREAM):
                existing = qc_receipts.find_verdict(root, tuple_sha)
                if existing is not None:
                    qc_receipts.attach_verdict(root, started["attempt_id"], qc_receipt_id=existing["receipt_id"], reused=True)
                    return ToolResult(success=True, data={"verdict": existing["verdict"], "failing_items": existing.get("failing_items"),
                                                          "warnings": existing.get("warnings"), "qc_receipt_id": existing["receipt_id"],
                                                          "reused": True}, cost_usd=0.0, duration_seconds=time.time() - start)
                wal = gates.qc_wal_read(tuple_sha)
                if wal is not None and wal.get("state") in ("claimed", "submitted", "unknown"):
                    raise RuntimeError(
                        f"evaluation {tuple_sha[:12]} has a {wal['state']} judge call with no outcome; "
                        f"run scripts/reconcile_paid_calls.py first"
                    )
                estimate = self.estimate_cost(inputs)
                reservation_id = reserve_paid_call(
                    tracker, root, tool=self.name, endpoint=f"{qc.judge_provider}/{qc.judge_model}",
                    normalized_inputs_hash=normalized_inputs_hash(inputs), reserved_usd=estimate,
                    output_hint={"kind": "qc_verdict", "tuple_sha256": tuple_sha},
                )
                gates.qc_wal_write(tuple_sha, {
                    "tuple_sha256": tuple_sha, "state": "claimed", "project_root": str(root), "project_id": pid,
                    "attempt_id": started["attempt_id"], "reservation_id": reservation_id,
                    "idempotency_key": f"{tuple_sha}:{started['attempt_id']}", "provider": qc.judge_provider,
                    "model": qc.judge_model, "evaluation": evaluation, "claimed_at": judged_at,
                })
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"sheet_judge claim failed: {exc}")

        # ---- submit / wait (no lock held) ----
        provider_id: Optional[str] = None
        state, actual = "failed", 0.0
        try:
            provider_id = adapter.submit(model=qc.judge_model, system=system, user=user, images=images, schema=schema)
            _persist_provider_id(root, tuple_sha, reservation_id, provider_id, state="submitted")
            state, actual = "pending_billing", estimate
            result = adapter.wait(provider_id, deadline_s=float(inputs.get("deadline_s", 300)), poll_s=float(inputs.get("poll_s", 3)))
        except Exception as exc:  # noqa: BLE001
            if provider_id:
                # never lose a returned id (inspection #7 / round 2 #2): both stores, independently
                _persist_provider_id(root, tuple_sha, reservation_id, provider_id, state="unknown", error=str(exc)[:400])
                try:
                    reconcile_paid_call(root, reservation_id, estimate, "pending_billing", tracker)
                except ValueError:
                    pass
            else:
                try:
                    wal = gates.qc_wal_read(tuple_sha) or {}
                    wal.update({"state": "claimed", "error": str(exc)[:400]})
                    gates.qc_wal_write(tuple_sha, wal)
                except Exception:  # noqa: BLE001
                    pass
            return ToolResult(success=False, error=f"sheet_judge provider call did not complete ({'submitted' if provider_id else 'not submitted'}): {exc}")

        # ---- commit ----
        try:
            wal = gates.qc_wal_read(tuple_sha) or {}
            row, sc, raw_sha = commit_result(root, wal, result, tracker)
        except Exception as exc:  # noqa: BLE001
            wal = gates.qc_wal_read(tuple_sha) or {}
            wal.update({"state": "unknown", "error": f"commit: {exc}"[:400]})
            gates.qc_wal_write(tuple_sha, wal)
            return ToolResult(success=False, error=f"sheet_judge commit failed (provider answered; reconcile): {exc}")

        return ToolResult(
            success=True,
            data={"verdict": sc["verdict"], "failing_items": sc["failing_items"], "warnings": sc["warnings"],
                  "coverage_error": sc.get("coverage_error"), "qc_receipt_id": row["receipt_id"],
                  "provider_request_id": provider_id, "raw_response_asset_id": raw_sha, "dims": dims},
            cost_usd=estimate, duration_seconds=time.time() - start,
        )

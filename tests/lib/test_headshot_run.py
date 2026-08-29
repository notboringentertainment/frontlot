"""D20.2 — scripts/headshot_run.py: generate → judge → select gate → approved
packet; budget + Blocked + override; reject-all via the declined request;
import mode; grandfather; recovery. Fake generator + fake judge; real
signed receipts. Invented names only."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from lib import qc_receipts as qr
from lib import receipts
from lib.headshot_verify import hero_migration_blockers
from lib.headshots import active_headshots
from lib.look_spec import look_hash as _look_hash
from lib.sheet_qc import policy
from scripts import headshot_run as hr
from scripts.headshot_run import Blocked, Declined, HeadshotRunError, run_headshot
from tests.lib.d19_helpers import JUDGE_MODEL, config_1_2
from tests.lib.look_lock_helpers import (
    CHAR, CHAR2, LOC, PROJECT, activate_look, approve_headshot, approve_request, character_look, decline_request,
    location_look, look_packet_for, look_refs_for, pin_project,
)
from tests.lib.test_authored_film_contract import (  # noqa: F401
    PIPELINE, approval_policy_decision, gates_dir, plain_decision_log, project, write_project_config,
)
from tests.lib.test_visual_bible_contract import canon_packet_v11, proposal_v11
from tests.tools.test_sheet_judge import FakeAdapter, _all
from tools import prompt_builder as pb
from tools.video import _shared


def write(pipeline_dir, stage, artifacts, *, status="completed"):
    from lib.checkpoint import write_checkpoint
    return write_checkpoint(pipeline_dir, PROJECT, stage, status, artifacts, pipeline_type=PIPELINE, human_approved=True)


def _png(w, h, seed):
    d = hashlib.sha256(seed.encode()).digest()
    buf = io.BytesIO(); Image.new("RGB", (w, h), (d[0], d[1], d[2])).save(buf, "PNG"); return buf.getvalue()


def _world(project, monkeypatch, *, version: str, cap: int = 3, cast=(CHAR, CHAR2)):
    pipeline_dir, project_dir = project
    import sys

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())
    import lib.events as events_mod
    import lib.paths as paths_mod
    monkeypatch.setattr(events_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setattr(paths_mod, "PROJECTS_DIR", pipeline_dir)
    monkeypatch.setenv("OPENMONTAGE_TEST_MODE", "1")
    digest = write_project_config(project_dir, config_1_2(max_hero_attempts=cap))
    pin_project(project_dir, version)
    from lib.pipeline_pin import refresh_cache
    refresh_cache(project_dir, "authored-film")
    write(pipeline_dir, "canon_ingest", {"canon_packet": canon_packet_v11()})
    log = plain_decision_log(); log["decisions"] = [approval_policy_decision(digest)]
    write(pipeline_dir, "proposal", {"proposal_packet": proposal_v11(cast, (LOC,)), "decision_log": log})
    c, l = character_look(), location_look()
    activate_look(project_dir, c); activate_look(project_dir, l)
    write(pipeline_dir, "look_lock", {"look_packet": look_packet_for(project_dir, c, l)}, status="in_progress")
    (project_dir / "canon/visual/objects").mkdir(parents=True, exist_ok=True)
    return {"pipeline": pipeline_dir, "project": project_dir, "c": c, "l": l, "out": io.StringIO()}


@pytest.fixture
def world(project, monkeypatch):
    return _world(project, monkeypatch, version="1.4")


@pytest.fixture
def legacy(project, monkeypatch):
    w = _world(project, monkeypatch, version="1.3", cast=(CHAR,))
    data = _png(1024, 1024, "legacy"); sha = hashlib.sha256(data).hexdigest()
    (w["project"] / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
    built = pb.build_prompt(w["c"], role="hero", palette=["moss"])
    receipts.record_generation(w["project"], execution_id="exec-legacy", tool="seedream_image", normalized_inputs_hash="a" * 64,
                               output_sha256=sha, cost_usd=0.07, started_at="t", finished_at="t", model_endpoint=hr.GENERATION_ENDPOINT,
                               prompt=built["prompt"], look_refs=look_refs_for(w["c"]), prompt_recipe=built["prompt_recipe"])
    _, hs = approve_headshot(w["project"], w["c"], sha, "d" * 64, recipe=built["prompt_recipe"])
    w.update({"asset": sha, "hs": hs})
    return w


class FakeGen:
    """Runs the pre-submit hook like the governed tool, writes a real hero PNG, records a governed receipt."""

    def __init__(self, *, dup_every: int = 0):
        self.n = 0; self.dup_every = dup_every

    def __call__(self, root, ctx):
        self.n += 1
        _shared.run_pre_submit(root, f"res-fake-{self.n}")
        seed = "dup" if self.dup_every and self.n % self.dup_every == 0 else f"hero-{self.n}"
        data = _png(1024, 1024, seed); sha = hashlib.sha256(data).hexdigest()
        (root / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
        built = ctx["built"]
        row = receipts.record_generation(root, execution_id=f"exec-hero-{self.n}", tool="seedream_image", normalized_inputs_hash="a" * 64,
                                         output_sha256=sha, cost_usd=0.07, started_at="t", finished_at="t", model_endpoint=hr.GENERATION_ENDPOINT,
                                         prompt=built["prompt"], prompt_recipe=built["prompt_recipe"], look_refs=look_refs_for(ctx["look"].payload))
        return sha, row["receipt_id"]


class PlanJudge(FakeAdapter):
    """Answers in order; a plan entry is a dict of overrides for _all('hero')."""

    def __init__(self, plan):
        super().__init__(None); self.plan = list(plan); self.calls = []

    def submit(self, *, model, system, user, images, schema):
        self.calls.append(user)
        assert user.startswith("Candidate role: hero")
        over = self.plan.pop(0) if self.plan else {}
        self.answers = _all("hero", **over)
        return f"resp_{len(self.calls)}"


def _run(w, **kw):
    kw.setdefault("generate", FakeGen()); kw.setdefault("judge_adapter", PlanJudge([]))
    return run_headshot(w["project"], CHAR, out=w["out"], **kw)


def _req(w, request_id):
    return json.loads((w["project"] / ".gate-requests" / f"{request_id}.json").read_text())


def _cp(w):
    return json.loads((w["project"] / "checkpoint_headshots.json").read_text())


class TestGenerateAndSelect:
    def test_generate_present_select_approve(self, world):
        w = world
        r = _run(w, candidates=2)
        assert r["status"] == "pending" and r["request_id"] == f"headshot-{CHAR}-1"
        cp = _cp(w)
        packet = cp["artifacts"]["headshot_packet"]
        assert cp["status"] == "awaiting_human" and packet["version"] == "1.1" and packet["state"] == "pending"
        entry = packet["characters"][0]
        assert len(entry["candidates"]) == 2 and all(c["qc_receipt_id"] for c in entry["candidates"])
        assert entry["prompt_recipe"]["builder_policy_sha256"] == pb.builder_policy_sha256()
        st = cp["metadata"]["run_state"][CHAR]
        assert st["mode"] == "select" and st["candidate_hashes"] == [c["asset_id"] for c in entry["candidates"]]
        req = _req(w, r["request_id"])
        assert req["kind"] == "headshot" and req["source_checkpoint_digest"]
        # idempotent while pending; --finish without a signed choice is still pending
        assert _run(w, finish=True)["status"] == "pending"
        receipt = approve_request(req, w["project"], selection=2)
        assert receipt["record"]["record_version"] == "1.1" and receipt["record"]["qc_receipt_id"] == entry["candidates"][1]["qc_receipt_id"]
        r2 = _run(w)
        assert r2["status"] == "approved" and r2["receipt_id"] == receipt["receipt_id"]
        cp = _cp(w)
        packet = cp["artifacts"]["headshot_packet"]
        assert packet["state"] == "approved" and cp["status"] == "in_progress" and cp["metadata"]["run_state"] == {}
        e = packet["characters"][0]
        assert e["origin"] == "generated" and e["qc_receipt_id"] == receipt["record"]["qc_receipt_id"]
        assert e["candidates_rejected"] == [entry["candidates"][0]["asset_id"]] and e["hero"]["qc_receipt_id"] == e["qc_receipt_id"]
        assert (w["project"] / "canon/visual/by-entity" / CHAR / "hero.png").exists()
        assert "sheet_run.py" in w["out"].getvalue()
        with pytest.raises(HeadshotRunError, match="--replace"):
            _run(w)
        # --replace starts a new selection whose receipt supersedes the tip
        r3 = _run(w, replace=True, candidates=1)
        assert r3["request_id"] == f"headshot-{CHAR}-2"
        assert "superseding hero" in w["out"].getvalue()

    def test_duplicate_pixels_consume_an_attempt_without_a_slot(self, world):
        w = world
        r = _run(w, candidates=3, generate=FakeGen(dup_every=2))  # attempts: hero-1, dup, hero-3 (cap 3)
        assert r["status"] == "pending"
        entry = _cp(w)["artifacts"]["headshot_packet"]["characters"][0]
        assert len(entry["candidates"]) == 3
        assert len(qr.hero_attempts_started(w["project"], CHAR, _look_hash(w["c"]))) == 3

    def test_budget_partial_then_blocked_with_override(self, world):
        w = world
        r = _run(w, candidates=4, judge_adapter=PlanJudge([{}, {"bust_front": "no"}, {}]))
        entry = _cp(w)["artifacts"]["headshot_packet"]["characters"][0]
        assert r["status"] == "pending" and len(entry["candidates"]) == 2
        assert "budget spent: presenting 2 of 4" in w["out"].getvalue()

    def test_all_fail_is_blocked_and_writes_override(self, world):
        w = world
        with pytest.raises(Blocked):
            _run(w, candidates=1, judge_adapter=PlanJudge([{"no_text": "no"}, {"no_text": "no", "hair_matches": "no"}, {"no_text": "no"}]))
        reqs = sorted((w["project"] / ".gate-requests").glob(f"override-{CHAR}-hero-*.json"))
        assert len(reqs) == 1
        ov = json.loads(reqs[0].read_text())
        assert ov["kind"] == "qc_override" and ov["stage"] == "headshots" and ov["item_ids"] == ["no_text"]
        assert not (w["project"] / "checkpoint_headshots.json").exists() or _cp(w).get("metadata", {}).get("run_state", {}) == {}
        # budget is spent: a rerun is blocked without generating
        gen = FakeGen()
        with pytest.raises(Blocked):
            _run(w, candidates=1, generate=gen)
        assert gen.n == 0

    def test_generator_without_hook_records_nothing(self, world):
        w = world

        def rogue(root, ctx):
            data = _png(1024, 1024, "rogue"); sha = hashlib.sha256(data).hexdigest()
            (root / "canon/visual/objects" / f"{sha}.png").write_bytes(data)
            return sha, "no-receipt"
        with pytest.raises(HeadshotRunError, match="pre-submit hook"):
            _run(w, generate=rogue)


class TestRejectAll:
    def test_declined_note_is_logged_and_regeneration_skips_rejected(self, world):
        w = world
        r = _run(w, candidates=1)
        first = _cp(w)["artifacts"]["headshot_packet"]["characters"][0]["candidates"][0]["asset_id"]
        decline_request(_req(w, r["request_id"]), w["project"], note="too stern, try again")
        r2 = _run(w, candidates=1)
        assert r2["status"] == "pending" and r2["request_id"] == f"headshot-{CHAR}-2"
        log = json.loads((w["project"] / "decision_log.json").read_text())
        rev = [d for d in log["decisions"] if d["category"] == "revision"]
        assert rev and rev[-1]["reason"] == "too stern, try again"
        entry = _cp(w)["artifacts"]["headshot_packet"]["characters"][0]
        assert entry["candidates"][0]["asset_id"] != first and entry["rejection_notes"] == ["too stern, try again"]
        assert _cp(w)["metadata"]["rejected_candidates"][CHAR] == [first]
        # same prompt across rounds: the recipe is the builder's, never the note's
        assert entry["prompt_recipe"] == pb.build_prompt(w["c"], role="hero", palette=["neutral grey"])["prompt_recipe"]

    def test_look_change_note_is_refused(self, world):
        w = world
        r = _run(w, candidates=1)
        decline_request(_req(w, r["request_id"]), w["project"], note="give him shorter hair")
        with pytest.raises(HeadshotRunError, match="look change"):
            _run(w, candidates=1)
        assert _cp(w)["metadata"]["run_state"][CHAR]["request_id"] == r["request_id"]  # nothing consumed


class TestImport:
    def test_import_is_attested_then_judged_then_selected(self, world, tmp_path):
        w = world
        src = tmp_path / "mine.png"; src.write_bytes(_png(1024, 1280, "writer-made"))
        r = _run(w, import_file=src, origin_tool="elsewhere-gen")
        assert r["status"] == "pending" and r["request_id"] == f"import-{CHAR}-1" and src.exists()
        req = _req(w, r["request_id"])
        assert req["kind"] == "reference_import" and req["approval_record"]["entity_id"] == CHAR
        assert _cp(w)["metadata"]["run_state"][CHAR]["mode"] == "import"
        approve_request(req, w["project"])
        gen = FakeGen()
        r2 = _run(w, generate=gen)
        assert gen.n == 0 and r2["status"] == "pending" and r2["request_id"] == f"headshot-{CHAR}-2"
        entry = _cp(w)["artifacts"]["headshot_packet"]["characters"][0]
        assert len(entry["candidates"]) == 1 and entry["candidates"][0]["provenance"]["generator_kind"] == "imported"
        assert "prompt_recipe" not in entry and entry["candidates"][0]["qc_receipt_id"]
        receipt = approve_request(_req(w, r2["request_id"]), w["project"], selection=1)
        assert receipt["record"]["origin"] == "imported_synthetic" and receipt["record"]["generation_receipt_id"] == receipt["record"]["import_receipt_id"]
        assert _run(w)["status"] == "approved"
        e = _cp(w)["artifacts"]["headshot_packet"]["characters"][0]
        assert e["origin"] == "imported_synthetic" and e["import_receipt_id"] == receipt["record"]["import_receipt_id"]

    def test_failed_import_is_blocked(self, world, tmp_path):
        w = world
        src = tmp_path / "mine.png"; src.write_bytes(_png(1024, 1280, "writer-made-2"))
        r = _run(w, import_file=src, origin_tool="elsewhere-gen")
        approve_request(_req(w, r["request_id"]), w["project"])
        with pytest.raises(Blocked):
            _run(w, judge_adapter=PlanJudge([{"plain_background": "no"}]))
        assert list((w["project"] / ".gate-requests").glob(f"override-{CHAR}-hero-*.json"))

    def test_import_needs_origin_tool(self, world, tmp_path):
        with pytest.raises(HeadshotRunError, match="--origin-tool"):
            _run(world, import_file=tmp_path / "x.png")


class TestGrandfather:
    def test_grandfather_under_1_3(self, legacy):
        w = legacy
        assert hero_migration_blockers(w["project"])
        with pytest.raises(HeadshotRunError, match="needs authored-film 1.4"):
            _run(w)
        r = _run(w, grandfather=True)
        assert r["status"] == "pending" and r["request_id"] == f"grandfather-{CHAR}-1"
        req = _req(w, r["request_id"])
        assert req["kind"] == "headshot_grandfather" and req["qc_receipt_id"]
        receipt = approve_request(req, w["project"])
        assert receipt["attests_receipt_id"] == w["hs"]["receipt_id"]
        r2 = _run(w, grandfather=True)
        assert r2["status"] == "grandfathered" and hero_migration_blockers(w["project"]) == []
        assert active_headshots(w["project"])[CHAR].receipt_id == w["hs"]["receipt_id"]
        with pytest.raises(HeadshotRunError, match="already a 1.1|nothing to grandfather|needs authored-film 1.4"):
            _run(w, grandfather=False)

    def test_failed_legacy_hero_is_blocked(self, legacy):
        w = legacy
        with pytest.raises(Blocked):
            _run(w, grandfather=True, judge_adapter=PlanJudge([{"single_subject": "no"}]))
        assert list((w["project"] / ".gate-requests").glob(f"override-{CHAR}-hero-*.json"))


class TestRecovery:
    def test_missing_select_request_is_republished_after_revalidation(self, world):
        w = world
        r = _run(w, candidates=1)
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()
        r2 = _run(w, candidates=1)
        assert r2["request_id"] == r["request_id"] and _req(w, r["request_id"])["source_checkpoint_digest"]
        # tamper the pending candidate: republish refused
        (w["project"] / ".gate-requests" / f"{r['request_id']}.json").unlink()
        path = w["project"] / "checkpoint_headshots.json"
        cp = json.loads(path.read_text()); cp["metadata"]["run_state"][CHAR]["candidate_hashes"] = ["f" * 64]
        path.write_text(json.dumps(cp))
        with pytest.raises(HeadshotRunError, match="differ from the run state"):
            _run(w, candidates=1)

    def test_done_request_of_another_kind_fails_closed(self, world):
        w = world
        r = _run(w, candidates=1)
        path = w["project"] / ".gate-requests" / f"{r['request_id']}.json"
        done = path.parent / "done"; done.mkdir()
        req = json.loads(path.read_text()); req["kind"] = "look_lock"
        (done / path.name).write_text(json.dumps(req)); path.unlink()
        with pytest.raises(HeadshotRunError, match="refusing to guess"):
            _run(w)

    def test_preconditions(self, world):
        w = world
        with pytest.raises(HeadshotRunError, match="nothing to finish"):
            _run(w, finish=True)
        with pytest.raises(HeadshotRunError, match="no active look"):
            run_headshot(w["project"], CHAR2, out=w["out"], generate=FakeGen(), judge_adapter=PlanJudge([]))
        with pytest.raises(HeadshotRunError, match="--candidates"):
            _run(w, candidates=5)

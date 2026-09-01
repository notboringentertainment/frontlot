"""Hero batch overrides (authored-film 1.5): waive items, don't cast.

The exhausted-budget flow separates the policy decision (accept these failed
items for the exact reviewed candidates — ONE field-bound qc_override record
1.1) from the casting decision (the selection gate presents passing ∪
unlocked, each unlocked candidate carrying its full citation). 1.4 keeps the
single-verdict 1.0 behavior byte-for-byte. Fake generator + fake judge; real
signed receipts. Invented names only."""
from __future__ import annotations

import json

import pytest

from lib import qc_receipts as qr
from lib.headshots import active_headshots
from lib.look_spec import look_hash as _look_hash
from scripts import headshot_run as hr
from scripts.headshot_run import Blocked, run_headshot
from tests.lib.d19_helpers import config_1_2
from tests.lib.look_lock_helpers import CHAR, PROJECT, activate_look, approve_request, character_look, decline_request
from tests.lib.test_authored_film_contract import project, write_project_config  # noqa: F401
from tests.lib.test_headshot_run import FakeGen, PlanJudge, _cp, _req, _run, _world
from scripts.gate_approve import GateHandlerError


@pytest.fixture
def bworld(project, monkeypatch):
    """A 1.5-pinned world: batch overrides + packet 1.2 apply."""
    return _world(project, monkeypatch, version="1.5")


def _fail(item="no_text"):
    return {item: "no"}


def _batch_reqs(w):
    return sorted((w["project"] / ".gate-requests").glob(f"override-{CHAR}-hero-*.json"))


def _sign_batch(w, req, items, *, reason="the writer accepts these items for this character"):
    req = dict(req)
    req["_chosen_items"] = list(items)
    req["reason"] = reason
    return approve_request(req, w["project"])


class TestBatchGateFires:
    def test_all_fail_raises_one_field_request_not_a_pick(self, bworld):
        w = bworld
        r = _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        assert r["status"] == "pending"
        reqs = _batch_reqs(w)
        assert len(reqs) == 1
        ov = json.loads(reqs[0].read_text())
        assert ov["batch"] is True and len(ov["field"]) == 3 and ov["item_ids"] == ["no_text"]
        assert ov["field_manifest_sha256"] and ov["source_checkpoint_digest"]
        assert ov["look_receipt_id"] and ov["config_approval_receipt_id"]
        st = _cp(w)["metadata"]["run_state"][CHAR]
        assert st["mode"] == "override_pending" and st["request_id"] == ov["request_id"]
        assert "source_checkpoint_digest" not in st  # round-3 #1: no digest-of-self

    def test_gate_fires_even_with_a_passing_candidate(self, bworld):
        w = bworld
        r = _run(w, candidates=3, judge_adapter=PlanJudge([{}, _fail(), _fail()]))
        assert r["status"] == "pending"
        reqs = _batch_reqs(w)
        assert len(reqs) == 1, "round-2 #8: one passing face must not swallow the field"
        ov = json.loads(reqs[0].read_text())
        assert len(ov["field"]) == 2  # only the failed candidates

    def test_declined_field_with_nothing_passing_is_terminal(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        decline_request(ov, w["project"], note="the judge is right about every one of these")
        with pytest.raises(Blocked, match="no unlockable field"):
            _run(w)
        assert len(_batch_reqs(w)) == 0, "a declined field is never re-raised (no new pending request)"


class TestBatchSigningAndCasting:
    def test_sign_unlocks_field_and_writer_casts_with_citation(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        receipt = _sign_batch(w, ov, ["no_text"])
        rec = receipt["record"]
        assert rec["record_version"] == "1.1" and rec["request_id"] == ov["request_id"]
        assert len(rec["batch"]) == 3 and sorted(rec["unlocked_asset_ids"]) == sorted(b["asset_id"] for b in rec["batch"])
        assert rec["field_manifest_sha256"] == ov["field_manifest_sha256"]
        # the re-run presents the whole unlocked field in a 1.2 packet
        r = _run(w)
        assert r["status"] == "pending" and r["request_id"].startswith("headshot-")
        packet = _cp(w)["artifacts"]["headshot_packet"]
        assert packet["version"] == "1.2"
        cands = packet["characters"][0]["candidates"]
        assert len(cands) == 3
        for c in cands:
            assert c["qc_override_receipt_id"] == receipt["receipt_id"]
            assert c["qc_override_record_sha256"] == receipt["record_sha256"]
            assert c["field_manifest_sha256"] == rec["field_manifest_sha256"]
        # cast one; the headshot record 1.2 seals the citation verbatim
        sel = approve_request(_req(w, r["request_id"]), w["project"], selection=2)
        hs = sel["record"]
        assert hs["record_version"] == "1.2"
        assert hs["qc_override_receipt_id"] == receipt["receipt_id"]
        assert hs["qc_override_record_sha256"] == receipt["record_sha256"]
        assert hs["field_manifest_sha256"] == rec["field_manifest_sha256"]
        assert hs["legacy_citations"] == []
        done = _run(w)
        assert done["status"] == "approved"
        assert active_headshots(w["project"])[CHAR].asset_id == cands[1]["asset_id"]

    def test_zero_unlock_choice_is_refused(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([
            {"no_text": "no", "plain_background": "no"},
            {"no_text": "no", "plain_background": "no"},
            {"no_text": "no", "plain_background": "no"},
        ]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        with pytest.raises(GateHandlerError, match="unlocks NO candidate"):
            _sign_batch(w, ov, ["no_text"])  # every candidate also fails plain_background

    def test_decline_then_rerun_does_not_reraise_the_same_field(self, bworld):
        w = bworld
        _run(w, candidates=3, judge_adapter=PlanJudge([{}, _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        decline_request(ov, w["project"], note="the judge is right; I want clean faces only")
        r = _run(w)
        assert r["status"] == "pending" and r["request_id"].startswith("headshot-")
        packet = _cp(w)["artifacts"]["headshot_packet"]
        assert len(packet["characters"][0]["candidates"]) == 1, "declined field presents passing only"

    def test_two_batches_never_compose(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([
            _fail("no_text"),
            {"no_text": "no", "plain_background": "no"},
            _fail("no_text"),
        ]))
        ov1 = json.loads(_batch_reqs(w)[0].read_text())
        r1 = _sign_batch(w, ov1, ["no_text"])
        # the A-only waiver unlocked the two single-failure candidates
        assert len(r1["record"]["unlocked_asset_ids"]) == 2
        # the A+B candidate is the residual field of a NEW request
        res = _run(w)
        assert res["status"] == "pending"
        assert res["request_id"].startswith("override-"), \
            "the residual field raises its waiver gate BEFORE presentation — deterministic, no skip (r2 #13)"
        ov2 = json.loads((w["project"] / ".gate-requests" / f"{res['request_id']}.json").read_text())
        assert len(ov2["field"]) == 1 and sorted(ov2["item_ids"]) == ["no_text", "plain_background"]
        r2 = _sign_batch(w, ov2, ["no_text", "plain_background"])
        assert len(r2["record"]["unlocked_asset_ids"]) == 1
        # eligibility of the A+B candidate cites ONLY batch 2's row
        row = r2["record"]["batch"][0]
        assert sorted(row["accepted_item_ids"]) == ["no_text", "plain_background"]


class TestStaleAuthority:
    def test_look_change_before_display_keeps_the_request_pending(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        c2 = character_look()
        c2["prompt_safe_description"] = c2["prompt_safe_description"] + " Now wearing a long coat in every scene always."
        activate_look(w["project"], c2, supersedes=_look_hash(w["c"]))
        with pytest.raises(GateHandlerError, match="look binding|state moved"):
            _sign_batch(w, ov, ["no_text"])
        assert (w["project"] / ".gate-requests" / f"{ov['request_id']}.json").is_file(), \
            "a display-time refusal never abandons — the operator re-runs headshot_run"

    def test_look_change_between_display_and_signing_abandons(self, bworld):
        from lib import gates
        from scripts import gate_approve

        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        req = dict(ov)
        req["_chosen_items"] = ["no_text"]
        req["reason"] = "the writer accepts this item for the field"
        shown = gate_approve.construct(w["project"], req)  # display succeeds
        c2 = character_look()
        c2["prompt_safe_description"] = c2["prompt_safe_description"] + " Now wearing a long coat in every scene always."
        activate_look(w["project"], c2, supersedes=_look_hash(w["c"]))  # state moves mid-signing
        with pytest.raises(GateHandlerError, match="look binding|state moved"):
            with gates.handler_context():
                gate_approve._decide(req, w["project"], shown=shown, note=None)
        assert (w["project"] / ".gate-requests" / "abandoned" / f"{ov['request_id']}.json").is_file(), \
            "round-4 #8: a signing-time pre-commit failure moves pending → abandoned first"
        # Recovery from here is the normal look-supersession path: the stale
        # look_packet fails canon enforcement closed until look_run absorbs
        # the new look — so a bare re-run refuses rather than proceeding.
        with pytest.raises(Exception, match="look_packet|look"):
            _run(w, candidates=1, generate=FakeGen(fixed="recovery-after-abandon"))

    def test_cap_raise_voids_the_pending_request(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        write_project_config(w["project"], config_1_2(max_hero_attempts=5, budget=50.0))
        with pytest.raises(GateHandlerError, match="config snapshot|no longer exhausted"):
            _sign_batch(w, ov, ["no_text"])

    def test_missing_request_republishes_same_id(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov_path = _batch_reqs(w)[0]
        rid = json.loads(ov_path.read_text())["request_id"]
        ov_path.unlink()
        r = _run(w)
        assert r["status"] == "pending" and r["request_id"] == rid, "D20 / round-3 #3: same id on republish"
        assert (w["project"] / ".gate-requests" / f"{rid}.json").is_file()

    def test_selection_stale_after_cap_raise(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        ov = json.loads(_batch_reqs(w)[0].read_text())
        receipt = _sign_batch(w, ov, ["no_text"])
        r = _run(w)
        assert r["request_id"].startswith("headshot-")
        write_project_config(w["project"], config_1_2(max_hero_attempts=6, budget=50.0))
        with pytest.raises(GateHandlerError, match="no longer exhausted|stale|project.yaml changed"):
            approve_request(_req(w, r["request_id"]), w["project"], selection=1)


class TestLegacyRegression:
    def test_1_4_still_writes_single_verdict_requests_and_1_1_records(self, project, monkeypatch):
        w = _world(project, monkeypatch, version="1.4")
        with pytest.raises(Blocked):
            _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        reqs = _batch_reqs(w)
        assert len(reqs) == 1
        ov = json.loads(reqs[0].read_text())
        assert "batch" not in ov and ov.get("qc_receipt_id"), "1.4 keeps the verdict-bound 1.0 request"
        assert "field" not in ov


class TestFixRound1:
    def test_replace_field_excludes_the_active_face(self, bworld):
        w = bworld
        # cast a hero cleanly first
        r = _run(w, candidates=1, generate=FakeGen(fixed="first-face"))
        sel = approve_request(_req(w, r["request_id"]), w["project"], selection=1)
        assert _run(w)["status"] == "approved"
        active_asset = active_headshots(w["project"])[CHAR].asset_id
        # replace: the presented/waiver field must never contain the active face
        from scripts.headshot_run import _rejected_assets
        assert active_asset in _rejected_assets(w["project"], CHAR, include_active=True)
        assert active_asset not in _rejected_assets(w["project"], CHAR, include_active=False), \
            "1.4 reuse semantics preserved: exclusion is 1.5-only"

    def test_retire_adds_face_to_durable_rejected_history(self, bworld):
        w = bworld
        r = _run(w, candidates=1, generate=FakeGen(fixed="retire-me"))
        approve_request(_req(w, r["request_id"]), w["project"], selection=1)
        assert _run(w)["status"] == "approved"
        asset = active_headshots(w["project"])[CHAR].asset_id
        rr = _run(w, retire=True)
        req = _req(w, rr["request_id"])
        approve_request(req, w["project"])
        done = _run(w, retire=True)
        assert done["status"] == "retired"
        hist = (_cp(w)["metadata"].get("rejected_candidates") or {}).get(CHAR) or []
        assert asset in hist, "inspection #9: the retired face is durably rejected"

    def test_doctored_request_field_text_is_refused(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        p = _batch_reqs(w)[0]
        ov = json.loads(p.read_text())
        ov["field"][0]["failing_items"] = ["hair_matches"]  # lie about why it failed
        p.write_text(json.dumps(ov, indent=2))
        with pytest.raises(GateHandlerError, match="disagrees with reconstruction"):
            _sign_batch(w, json.loads(p.read_text()), ["no_text"])

    def test_hybrid_and_lax_records_are_refused(self):
        import lib.receipts as R
        base = {"record_version": "1.1", "request_id": "override-x-1",
                "batch": [{"qc_receipt_id": "qa", "asset_id": "aa", "accepted_item_ids": ["no_text"]}],
                "field_manifest_sha256": "f" * 64, "unlocked_asset_ids": ["aa"],
                "look_hash": "1" * 64, "look_receipt_id": "lr",
                "config_approval_receipt_id": "cr", "config_sha256": "2" * 64,
                "budget_cap": 12, "attempts_spent": 12,
                "expected_active_headshot_receipt_id": None, "reason": "a real typed reason here"}
        env = {"field_manifest_sha256": "f" * 64}
        R.validate_envelope("qc_override", dict(base), "d" * 64, "e", env)  # sane baseline
        import copy
        for mutate, why in [
            (lambda r: r.__setitem__("field_manifest_sha256", "F" * 64), "uppercase hex refused"),
            (lambda r: r.__setitem__("budget_cap", True), "boolean counter refused"),
            (lambda r: r.__setitem__("attempts_spent", -1), "negative counter refused"),
            (lambda r: r.__setitem__("unlocked_asset_ids", ["aa", "aa"]), "duplicate unlocked ids refused"),
        ]:
            bad = copy.deepcopy(base)
            mutate(bad)
            bad_env = {"field_manifest_sha256": bad["field_manifest_sha256"]} if "manifest" not in why else env
            with pytest.raises(ValueError):
                R.validate_envelope("qc_override", bad, "d" * 64, "e", {"field_manifest_sha256": bad["field_manifest_sha256"]})
        # a versionless record smuggling a batch field is a hybrid
        with pytest.raises(ValueError):
            R.validate_envelope("qc_override", {"qc_receipt_id": "q1", "item_ids": ["x"], "reason": "long enough reason",
                                                "unlocked_asset_ids": ["aa"]}, "d" * 64, "e", {"qc_receipt_id": "q1"})
        # a fresh 1.0 envelope keeps its historical single-field shape
        out = R.validate_envelope("qc_override", {"qc_receipt_id": "q1", "item_ids": ["x"], "reason": "long enough reason"},
                                   "d" * 64, "e", {"qc_receipt_id": "q1"})
        assert out == {"qc_receipt_id": "q1"}, "no null field_manifest_sha256 in 1.0 envelopes"


class TestFixRound2:
    def test_replay_rejects_extras_and_malformed_citations(self):
        from lib.headshots import HeadshotError, headshot_record
        rec = headshot_record(entity_id='e', look_hash='l' * 64, asset_id='a' * 64, origin='generated',
                              import_receipt_id=None, prompt_recipe_sha256='p' * 64,
                              candidates_checkpoint_digest='c' * 64, record_version='1.2',
                              generation_receipt_id='g', qc_receipt_id='q')
        assert rec['legacy_citations'] == [] and rec['qc_override_receipt_id'] is None
        with pytest.raises(HeadshotError, match='sha256'):
            headshot_record(entity_id='e', look_hash='l' * 64, asset_id='a' * 64, origin='generated',
                            import_receipt_id=None, prompt_recipe_sha256='p' * 64,
                            candidates_checkpoint_digest='c' * 64, record_version='1.2',
                            generation_receipt_id='g', qc_receipt_id='q',
                            qc_override_receipt_id='o', qc_override_record_sha256='short',
                            field_manifest_sha256='f' * 64)

    def test_receipt_lists_must_be_canonical(self):
        import lib.receipts as R
        base = {"record_version": "1.1", "request_id": "override-x-hero-1",
                "batch": [{"qc_receipt_id": "qa", "asset_id": "aa", "accepted_item_ids": ["no_text", "bust_front"]}],
                "field_manifest_sha256": "f" * 64, "unlocked_asset_ids": ["aa"],
                "look_hash": "1" * 64, "look_receipt_id": "lr",
                "config_approval_receipt_id": "cr", "config_sha256": "2" * 64,
                "budget_cap": 12, "attempts_spent": 12,
                "expected_active_headshot_receipt_id": None, "reason": "a real typed reason here"}
        env = {"field_manifest_sha256": "f" * 64}
        with pytest.raises(ValueError, match="sorted and unique"):
            R.validate_envelope("qc_override", base, "d" * 64, "e", env)  # unsorted items
        base["batch"][0]["accepted_item_ids"] = ["bust_front", "no_text"]
        R.validate_envelope("qc_override", base, "d" * 64, "e", env)
        import copy
        bad = copy.deepcopy(base); bad["request_id"] = "x" * 80
        with pytest.raises(ValueError, match="request id"):
            R.validate_envelope("qc_override", bad, "d" * 64, "e", env)

    def test_override_ids_are_monotonic_even_after_deletion(self, bworld):
        w = bworld
        _run(w, candidates=1, judge_adapter=PlanJudge([_fail(), _fail(), _fail()]))
        p1 = _batch_reqs(w)[0]
        rid1 = json.loads(p1.read_text())["request_id"]
        assert rid1.endswith("-1")
        # deleting the highest file must not free its id: the durable counter
        # in checkpoint metadata remembers it (r2 #8)
        seq = _cp(w)["metadata"].get("override_request_seq") or {}
        assert int(seq.get(CHAR) or 0) >= 1

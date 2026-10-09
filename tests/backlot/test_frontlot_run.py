import hashlib, json, os, sys, threading, time
from pathlib import Path

import pytest

from backlot.claude_ops import Prepared
from backlot.claude_requests import Rejected, RequestStore

REPO = Path(__file__).resolve().parents[2]


def wait_state(store, rid, want, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        store.reconcile()
        if store.get(rid)["state"] == want:
            return
        time.sleep(0.1)
    raise AssertionError(store.get(rid))


def approved_paid(store, tmp_path, code="print('made 1')", film_root=None):
    p = Prepared("headshot_candidates", True, [sys.executable, "-c", code], {}, summary="Make 1", entity="hero-a")
    r = store.create(p, key="k", session="s", epoch="e", film_root=film_root)
    store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    return r


def test_free_request_runs_once_and_records_outcome(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    p = Prepared("look", False, [sys.executable, "-c", "print('hello')"], {}, summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    pid = store.launch(r["id"], repo=REPO, env=dict(os.environ))
    assert pid > 0 and store.get(r["id"])["pid_started"]
    wait_state(store, r["id"], "done")
    assert "hello" in store.outcome(r["id"])["tail"]
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=REPO, env=dict(os.environ))  # never twice


def test_failed_command_is_failed_not_done(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    p = Prepared("look", False, [sys.executable, "-c", "raise SystemExit(3)"], {}, summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    store.launch(r["id"], repo=REPO, env=dict(os.environ))
    wait_state(store, r["id"], "failed")


def test_a_failed_run_that_left_a_provider_submission_open_is_uncertain(tmp_path):
    film = tmp_path / "film"; film.mkdir()
    code = ("import json; open(%r, 'a').write(json.dumps({'reservation_id': 'res-1', 'state': 'submitting'}) + '\\n');"
            " raise SystemExit(1)") % str(film / "cost-reservations.jsonl")
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path, code=code, film_root=film)
    store.launch(r["id"], repo=REPO, env=dict(os.environ))
    wait_state(store, r["id"], "uncertain")
    assert store.outcome(r["id"])["unresolved"] == ["res-1"]


def test_an_unreadable_paid_call_ledger_makes_a_failed_run_uncertain(tmp_path):
    film = tmp_path / "film"; film.mkdir()
    (film / "cost-reservations.jsonl").write_text("{not json\n")
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path, code="raise SystemExit(1)", film_root=film)
    store.launch(r["id"], repo=REPO, env=dict(os.environ))
    wait_state(store, r["id"], "uncertain")
    assert store.outcome(r["id"])["unresolved"] == ["paid-call ledger unreadable"]


@pytest.mark.parametrize("reason", ["stop", "end", "addon-reload"])
def test_cancel_before_launch_wins(tmp_path, reason):
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path)
    assert store.cancel_unstarted(reason=reason) == [r["id"]]
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=REPO, env=dict(os.environ))
    assert not (store.dir / f"{r['id']}.claim").exists() and "pid" not in store.get(r["id"])


@pytest.mark.parametrize("reason", ["stop", "end", "addon-reload"])
@pytest.mark.parametrize("stage", ["claimed", "spawned"])
def test_cancel_during_launch_waits_for_the_lock_and_cannot_win(tmp_path, reason, stage):
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path)
    seen = {}

    def between(name):
        if name != stage:
            return
        t = threading.Thread(target=lambda: seen.setdefault("cancelled", store.cancel_unstarted(reason=reason)))
        t.start(); seen["thread"] = t
        time.sleep(0.3)
        seen["blocked"] = t.is_alive()  # the cancel is waiting on this request's lock

    store.launch(r["id"], repo=REPO, env=dict(os.environ), _between=between)
    seen["thread"].join(10)
    assert seen["blocked"] is True
    assert seen["cancelled"] == []
    assert store.get(r["id"])["state"] in ("running", "done")
    wait_state(store, r["id"], "done")


def test_exit_5_settles_as_failed_with_the_nothing_spent_note(tmp_path):
    film = tmp_path / "film"; film.mkdir()
    store = RequestStore(tmp_path / "meta", "film")
    r = approved_paid(store, tmp_path, code="raise SystemExit(5)", film_root=film)
    store.launch(r["id"], repo=REPO, env=dict(os.environ))
    wait_state(store, r["id"], "failed")
    assert "Nothing was spent" in store.get(r["id"])["note"]


def test_spawn_failure_records_failed_and_raises(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    p = Prepared("look", False, [sys.executable, "-c", "print(1)"], {}, summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=tmp_path / "no-such-dir", env=dict(os.environ))
    cur = store.get(r["id"])
    assert cur["state"] == "failed" and "couldn't start" in cur["note"]
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=REPO, env=dict(os.environ))


def test_bad_env_value_is_a_spawn_failure_too(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    p = Prepared("look", False, [sys.executable, "-c", "print(1)"], {}, summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    with pytest.raises(Rejected):
        store.launch(r["id"], repo=REPO, env={"BAD": 1})
    assert store.get(r["id"])["state"] == "failed"


def _claimed(store, tmp_path):
    p = Prepared("look", False, [sys.executable, "-c", "print(1)"], {}, summary="Look")
    r = store.create(p, key="k", session="s", epoch="e")
    return store.claim_for_launch(r["id"])


def _run_wrapper(store, rid):
    from scripts import frontlot_run
    return frontlot_run.main(["--store", str(store.base), "--request", rid, "--repo", str(REPO)])


def test_tampered_argv_does_not_run_and_settles_failed(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    marker = tmp_path / "ran"
    rec = _claimed(store, tmp_path)
    rec["argv"] = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]   # changed after Go, hash left as approved
    store._write(rec)
    assert _run_wrapper(store, rec["id"]) == 0
    assert not marker.exists()
    out = store.outcome(rec["id"])
    assert out["exit"] != 0 and "Nothing was spent" in out["tail"]
    store.reconcile()
    assert store.get(rec["id"])["state"] == "failed"


def test_wrapper_crash_before_the_run_is_uncertain_with_a_trace(tmp_path):
    store = RequestStore(tmp_path / "meta", "film")
    rec = _claimed(store, tmp_path)
    rec.pop("argv")
    store._write(rec)
    assert _run_wrapper(store, rec["id"]) == 1
    assert store.outcome(rec["id"])["exit"] is None
    assert "KeyError" in (store.dir / f"{rec['id']}.log").read_text()
    store.reconcile()
    assert store.get(rec["id"])["state"] == "uncertain"

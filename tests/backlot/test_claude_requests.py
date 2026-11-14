import fcntl, hashlib, json, os, threading

import pytest

from backlot.claude_ops import Prepared
from backlot.claude_requests import Rejected, RequestStore
from lib.run_common import EXIT_INPUT_CHANGED
from lib.run_lease import process_start_time


def paid(tmp_path, body=b"v1"):
    f = tmp_path / "cp.json"; f.write_bytes(body)
    return Prepared("headshot_candidates", True, ["py", "x"], {f: hashlib.sha256(body).hexdigest()},
                    summary="Make 3", entity="hero-a", estimate_usd=0.36)


@pytest.fixture
def store(tmp_path):
    return RequestStore(tmp_path / "meta", "film")


def approved(store, tmp_path, key="k"):
    r = store.create(paid(tmp_path), key=key, session="s", epoch="e")
    store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    return r


def launched(store, tmp_path, key="k", pid=999_999, started="x"):
    r = approved(store, tmp_path, key=key)
    store.claim_for_launch(r["id"]); store.mark_running(r["id"], pid=pid, started=started)
    return r


def test_create_is_idempotent_by_key(store, tmp_path):
    a = store.create(paid(tmp_path), key="k1", session="s", epoch="e")
    b = store.create(paid(tmp_path), key="k1", session="s", epoch="e")
    assert a["id"] == b["id"] and a["state"] == "waiting-for-ben"


def test_create_records_the_prepared_digest_not_a_second_read(store, tmp_path):
    p = paid(tmp_path)
    (tmp_path / "cp.json").write_bytes(b"edited after prepare")
    r = store.create(p, key="k", session="s", epoch="e")
    assert r["inputs"] == {str(tmp_path / "cp.json"): hashlib.sha256(b"v1").hexdigest()}


def test_free_request_starts_approved(store, tmp_path):
    p = Prepared("look", False, ["py"], {}, summary="Look")
    assert store.create(p, key="k", session="s", epoch="e")["state"] == "approved"


def test_only_one_decision_and_only_from_controller(store, tmp_path):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s", epoch="e", controller=False)
    assert store.decide(r["id"], go=True, session="s", epoch="e", controller=True)["state"] == "approved"
    with pytest.raises(Rejected) as err:
        store.decide(r["id"], go=False, session="s", epoch="e", controller=True)
    assert err.value.plain == "That card was already answered."


def test_stale_session_or_epoch_rejected(store, tmp_path):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s2", epoch="e", controller=True)
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s", epoch="e2", controller=True)


def test_changed_input_refuses_go(store, tmp_path):
    p = paid(tmp_path)
    r = store.create(p, key="k", session="s", epoch="e")
    next(iter(p.inputs)).write_bytes(b"v2")
    with pytest.raises(Rejected) as err:
        store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    assert "changed" in err.value.plain
    assert store.get(r["id"])["state"] == "cancelled"


def test_claim_happens_once(store, tmp_path):
    r = approved(store, tmp_path)
    assert store.claim_for_launch(r["id"])["state"] == "launching"
    with pytest.raises(Rejected):
        store.claim_for_launch(r["id"])


def test_cancel_never_touches_launching_or_running(store, tmp_path):
    a = store.create(paid(tmp_path), key="a", session="s", epoch="e")
    b = approved(store, tmp_path, key="b")
    store.claim_for_launch(b["id"])
    assert store.cancel_unstarted(reason="stop") == [a["id"]]
    assert store.get(b["id"])["state"] == "launching"


def test_cancel_before_claim_wins_and_the_claim_is_then_refused(store, tmp_path):
    r = approved(store, tmp_path)
    assert store.cancel_unstarted(reason="end") == [r["id"]]
    with pytest.raises(Rejected):
        store.claim_for_launch(r["id"])
    assert not (store.dir / f"{r['id']}.claim").exists()


def test_reconcile_marks_unknown_launches_uncertain(store, tmp_path):
    r = approved(store, tmp_path)
    store.claim_for_launch(r["id"])              # crash before pid recorded
    assert store.reconcile() == [r["id"]]
    assert store.get(r["id"])["state"] == "uncertain"


def test_reconcile_dead_pid_without_outcome_is_uncertain_with_outcome_is_done(store, tmp_path):
    r = launched(store, tmp_path)                 # 999_999 is not a live pid
    store.reconcile()
    assert store.get(r["id"])["state"] == "uncertain"
    r2 = launched(store, tmp_path, key="k2", pid=999_998)
    (store.dir / f"{r2['id']}.outcome.json").write_text(json.dumps({"exit": 0}))
    store.reconcile()
    assert store.get(r2["id"])["state"] == "done"


def test_a_late_outcome_settles_an_uncertain_request(store, tmp_path):
    r = launched(store, tmp_path)
    store.reconcile()
    assert store.get(r["id"])["state"] == "uncertain"
    (store.dir / f"{r['id']}.outcome.json").write_text(json.dumps({"exit": 0}))
    assert store.reconcile() == [r["id"]]
    assert store.get(r["id"])["state"] == "done"


def test_a_failed_paid_run_with_an_unresolved_submission_is_uncertain(store, tmp_path):
    r = launched(store, tmp_path)
    (store.dir / f"{r['id']}.outcome.json").write_text(json.dumps({"exit": 1, "unresolved": ["res-1"]}))
    store.reconcile()
    rec = store.get(r["id"])
    assert rec["state"] == "uncertain" and "res-1" in rec["note"]


def test_reconcile_treats_a_reused_pid_as_gone(store, tmp_path):
    r = launched(store, tmp_path, pid=os.getpid(), started="started at another time")  # live pid, other process
    store.reconcile()
    assert store.get(r["id"])["state"] == "uncertain"
    r2 = launched(store, tmp_path, key="k2", pid=os.getpid(), started=process_start_time(os.getpid()))
    store.reconcile()
    assert store.get(r2["id"])["state"] == "running"


def test_mark_running_requires_launching(store, tmp_path):
    r = approved(store, tmp_path)
    with pytest.raises(Rejected):
        store.mark_running(r["id"], pid=1, started=None)


def test_every_transition_is_in_the_spend_log(store, tmp_path):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    store.decide(r["id"], go=False, session="s", epoch="e", controller=True)
    assert store.spend_log == tmp_path / "meta" / "film.spend.jsonl"
    lines = [json.loads(x) for x in store.spend_log.read_text().splitlines()]
    assert [l["state"] for l in lines] == ["waiting-for-ben", "declined"]
    assert all(l["argv_sha256"] and l["request"] == r["id"] and l["estimate_usd"] == 0.36 for l in lines)


def test_money_writes_are_fsynced_before_they_return(store, tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    def spy(fd):
        calls.append(fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0")[0].decode())  # macOS
        return real(fd)
    monkeypatch.setattr(os, "fsync", spy)
    def names(seq):
        return [c.rsplit("/", 1)[-1] for c in seq]
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e"); n_create = len(calls)
    store.decide(r["id"], go=True, session="s", epoch="e", controller=True); n_go = len(calls) - n_create
    store.claim_for_launch(r["id"]); n_claim = len(calls) - n_create - n_go
    assert n_create >= 3 and n_go >= 3 and n_claim >= 4   # log, state, dir (+ claim file)
    first = names(calls[:n_create])
    log_i = first.index("film.spend.jsonl")
    state_i = next(i for i, n in enumerate(first) if n.endswith(".tmp") or n == f"{r['id']}.json")
    meta_i = first.index("meta", log_i)                    # the log's parent directory
    assert log_i < state_i and log_i < meta_i
    assert first.index("requests", state_i) > state_i      # directory after the state file


def test_concurrent_creates_with_one_key_make_one_record(store, tmp_path):
    p = paid(tmp_path)
    out, barrier = [], threading.Barrier(8)
    def go():
        barrier.wait(); out.append(RequestStore(tmp_path / "meta", "film").create(p, key="same", session="s", epoch="e")["id"])
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(set(out)) == 1 and len(store.all_requests()) == 1


def test_a_claim_file_without_the_state_write_settles_uncertain(store, tmp_path):
    r = approved(store, tmp_path)
    (store.dir / f"{r['id']}.claim").touch()               # crash after O_EXCL claim, before the state write
    assert store.reconcile() == [r["id"]]
    assert store.get(r["id"])["state"] == "uncertain"


def test_expiry_on_decide(store, tmp_path, monkeypatch):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    monkeypatch.setattr("backlot.claude_requests.EXPIRY_SECONDS", 0)
    with pytest.raises(Rejected):
        store.decide(r["id"], go=True, session="s", epoch="e", controller=True)
    assert store.get(r["id"])["state"] == "expired"


def test_expire_stale_without_any_click(store, tmp_path, monkeypatch):
    r = store.create(paid(tmp_path), key="k", session="s", epoch="e")
    f = store.create(Prepared("look", False, ["py"], {}, summary="Look"), key="f", session="s", epoch="e")
    monkeypatch.setattr("backlot.claude_requests.EXPIRY_SECONDS", 0)
    assert store.expire_stale() == [r["id"]]
    assert store.get(r["id"])["state"] == "expired" and store.get(f["id"])["state"] == "approved"


def test_outcome_notices_wait_until_marked(store, tmp_path):
    r = launched(store, tmp_path)
    (store.dir / f"{r['id']}.outcome.json").write_text(json.dumps({"exit": 0}))
    store.reconcile()
    assert [x["id"] for x in store.pending_notices()] == [r["id"]]
    again = RequestStore(tmp_path / "meta", "film")              # a restarted broker still sees it
    assert [x["id"] for x in again.pending_notices()] == [r["id"]]
    again.mark_notified(r["id"], "done")
    assert again.pending_notices() == []


def test_input_changed_refusal_settles_failed_even_with_unresolved_ids(store, tmp_path):
    # exit 5 = refused before anything reached a provider: never uncertain, never done
    r = launched(store, tmp_path)
    (store.dir / f"{r['id']}.outcome.json").write_text(json.dumps({"exit": EXIT_INPUT_CHANGED, "unresolved": ["res-1"]}))
    store.reconcile()
    rec = store.get(r["id"])
    assert rec["state"] == "failed" and rec["note"] == "Something it depended on changed after Go, so it didn't run. Nothing was spent."
    r2 = launched(store, tmp_path, key="k2", pid=999_998)
    (store.dir / f"{r2['id']}.outcome.json").write_text(json.dumps({"exit": EXIT_INPUT_CHANGED}))
    store.reconcile()
    assert store.get(r2["id"])["state"] == "failed"


def test_cancel_if_unclaimed_cancels_only_an_approved_request_without_a_claim(store, tmp_path):
    r = approved(store, tmp_path, key="k1")
    assert store.cancel_if_unclaimed(r["id"], reason="launch-failed") is True
    rec = store.get(r["id"])
    assert rec["state"] == "cancelled" and rec["note"] == "launch-failed"
    w = store.create(paid(tmp_path), key="k2", session="s", epoch="e")          # waiting-for-ben: not ours to cancel
    assert store.cancel_if_unclaimed(w["id"], reason="launch-failed") is False
    assert store.get(w["id"])["state"] == "waiting-for-ben"
    assert store.cancel_if_unclaimed("r-0000000000", reason="launch-failed") is False


def test_cancel_if_unclaimed_never_cancels_a_claimed_request(store, tmp_path):
    r = approved(store, tmp_path, key="k1")
    (store.dir / f"{r['id']}.claim").touch()                                     # claimed, state write lost
    assert store.cancel_if_unclaimed(r["id"], reason="launch-failed") is False
    assert store.get(r["id"])["state"] == "approved"
    s = launched(store, tmp_path, key="k2")
    assert store.cancel_if_unclaimed(s["id"], reason="launch-failed") is False
    assert store.get(s["id"])["state"] == "running"

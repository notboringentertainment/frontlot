"""Run requests from Front Lot's embedded Claude, and Ben's decisions on them.

One JSON state file per request, guarded by a per-request flock. Launch and
cancel take the same lock, so they cannot race (spec §4.3). A request is
claimed for launch with O_EXCL exactly once. Nothing is retried automatically;
anything whose fate is unknown becomes `uncertain`. Writes that money depends
on (the acknowledged request, the decision, the claim) are fsync'd, audit line
first, before the caller acknowledges or spawns.
"""
from __future__ import annotations

import fcntl, hashlib, json, os, time, uuid
from contextlib import contextmanager
from pathlib import Path

from backlot.claude_ops import Prepared, input_digest
from lib.run_common import EXIT_INPUT_CHANGED
from lib.run_lease import pid_is_running, process_start_time

EXPIRY_SECONDS = 30 * 60
UNSTARTED = {"waiting-for-ben", "approved"}
FINAL = {"done", "failed", "declined", "cancelled", "expired"}
NOTIFY = {"done", "failed", "uncertain", "expired", "cancelled"}


class Rejected(RuntimeError):
    def __init__(self, plain: str):
        super().__init__(plain)
        self.plain = plain


def _same_process(pid: int, started: str | None) -> bool:
    if not pid_is_running(pid):
        return False
    now = process_start_time(pid)
    return started is None or now is None or now == started  # a different start time = a reused pid


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RequestStore:
    def __init__(self, root: Path, film: str):
        self.base = Path(root) / film
        self.dir = self.base / "requests"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for d in (self.dir, self.base, Path(root)):   # make the new directories' entries durable
            _fsync_dir(d)
        self.spend_log = Path(root) / f"{film}.spend.jsonl"
        self._children: dict[str, object] = {}  # wrappers this process started (Task 6), reaped in reconcile

    # -- files --------------------------------------------------------------
    def _path(self, rid: str) -> Path:
        return self.dir / f"{rid}.json"

    @contextmanager
    def _locked(self, rid: str):
        with open(self.dir / f"{rid}.lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    @contextmanager
    def _key_locked(self, key: str):
        name = "key-" + hashlib.sha256(key.encode()).hexdigest()[:16] + ".lock"
        with open(self.dir / name, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _write(self, rec: dict, *, durable: bool = False, log: bool = True) -> None:
        line = {"at": time.time(), "request": rec["id"], "state": rec["state"], "op": rec["op"], "paid": rec["paid"],
                "summary": rec["summary"], "entity": rec.get("entity"), "estimate_usd": rec.get("estimate_usd"),
                "argv_sha256": rec["argv_sha256"], "session": rec["session"], "note": rec.get("note"),
                "exit": (rec.get("result") or {}).get("exit")}
        if log:                                        # audit line first: never a state without its record
            with open(self.spend_log, "a") as fh:
                fh.write(json.dumps(line) + "\n")
                if durable:
                    fh.flush(); os.fsync(fh.fileno())
            if durable:
                _fsync_dir(self.spend_log.parent)       # the log's own directory entry
        tmp = self._path(rec["id"]).with_suffix(".tmp")
        with open(tmp, "w") as fh:
            fh.write(json.dumps(rec, sort_keys=True))
            if durable:
                fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, self._path(rec["id"]))
        if durable:
            _fsync_dir(self.dir)

    def get(self, rid: str) -> dict | None:
        p = self._path(rid)
        return json.loads(p.read_text()) if p.exists() else None

    def all_requests(self) -> list[dict]:
        recs = [json.loads(p.read_text()) for p in self.dir.glob("r-*.json") if not p.name.endswith(".outcome.json")]
        return sorted(recs, key=lambda r: r["created"])

    def by_key(self, key: str) -> dict | None:
        return next((r for r in self.all_requests() if r.get("key") == key), None)

    def open_requests(self) -> list[dict]:
        return [r for r in self.all_requests() if r["state"] not in FINAL]

    # -- lifecycle ----------------------------------------------------------
    def create(self, prep: Prepared, *, key: str, session: str, epoch: str, film_root: Path | None = None) -> dict:
        with self._key_locked(key):                    # lookup and write under one lock: idempotent by key
            existing = self.by_key(key)
            if existing:
                return existing
            return self._create(prep, key=key, session=session, epoch=epoch, film_root=film_root)

    def _create(self, prep: Prepared, *, key: str, session: str, epoch: str, film_root: Path | None) -> dict:
        rid = "r-" + uuid.uuid4().hex[:10]
        rec = {"id": rid, "key": key, "op": prep.op, "paid": prep.paid, "argv": prep.argv,
               "argv_sha256": hashlib.sha256(json.dumps(prep.argv).encode()).hexdigest(),
               "inputs": {str(p): d for p, d in prep.inputs.items()},   # digests of the bytes prepare read
               "summary": prep.summary, "entity": prep.entity, "estimate_usd": prep.estimate_usd,
               "session": session, "epoch": epoch, "created": time.time(),
               "film_root": str(film_root) if film_root else None,
               "state": "waiting-for-ben" if prep.paid else "approved"}
        with self._locked(rid):
            self._write(rec, durable=True)
        return rec

    def decide(self, rid: str, *, go: bool, session: str, epoch: str, controller: bool) -> dict:
        if not controller:
            raise Rejected("Only the window in control can answer. Use Take control first.")
        with self._locked(rid):
            rec = self.get(rid)
            if rec is None:
                raise Rejected("That request is gone.")
            if rec["state"] == "waiting-for-ben" and time.time() - rec["created"] > EXPIRY_SECONDS:
                rec["state"] = "expired"; self._write(rec)
                raise Rejected("That card expired. Ask Claude again if you still want it.")
            if rec["state"] != "waiting-for-ben":
                raise Rejected("That card was already answered.")
            if rec["session"] != session or rec["epoch"] != epoch:
                raise Rejected("That card belongs to an earlier conversation and can't run now.")
            if go:
                for path, digest in rec["inputs"].items():
                    if input_digest(Path(path)) != digest:
                        rec["state"] = "cancelled"; rec["note"] = "input changed"; self._write(rec)
                        raise Rejected("Something this run depends on changed since the card appeared. Ask Claude to set it up again.")
            rec["state"] = "approved" if go else "declined"
            rec["decided"] = time.time()
            self._write(rec, durable=True)
            return rec

    def expire_stale(self) -> list[str]:
        done = []
        for rec in self.open_requests():
            if rec["state"] != "waiting-for-ben":
                continue
            with self._locked(rec["id"]):
                cur = self.get(rec["id"])
                if cur and cur["state"] == "waiting-for-ben" and time.time() - cur["created"] > EXPIRY_SECONDS:
                    cur["state"] = "expired"; self._write(cur); done.append(cur["id"])
        return done

    def cancel_unstarted(self, *, reason: str) -> list[str]:
        done = []
        for rec in self.open_requests():
            with self._locked(rec["id"]):
                cur = self.get(rec["id"])
                if cur and cur["state"] in UNSTARTED:
                    cur["state"] = "cancelled"; cur["note"] = reason; self._write(cur)
                    done.append(cur["id"])
        return done

    def _claim(self, rid: str) -> dict:
        """Caller holds the request lock."""
        rec = self.get(rid)
        if rec is None or rec["state"] != "approved":
            raise Rejected("This run can't start now.")
        try:
            fd = os.open(self.dir / f"{rid}.claim", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.fsync(fd); os.close(fd)
        except FileExistsError:
            raise Rejected("This run already started.") from None
        rec["state"] = "launching"; rec["claimed"] = time.time()
        self._write(rec, durable=True)              # also fsyncs the directory holding the claim file
        return rec

    def launch(self, rid: str, *, repo: Path, env: dict[str, str], _between=None) -> int:
        """Claim, spawn, and record the pid in one locked section (spec §4.3), so a
        Stop/End/reload cancel either wins before the claim or waits and loses."""
        import subprocess, sys
        step = _between or (lambda _stage: None)
        with self._locked(rid):
            rec = self._claim(rid)                      # approved -> launching, O_EXCL, durable
            step("claimed")
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(Path(repo) / "scripts" / "frontlot_run.py"),
                     "--store", str(self.base), "--request", rid, "--repo", str(repo)],
                    cwd=repo, env=env, start_new_session=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                rec["state"] = "failed"; rec["note"] = f"could not start: {exc}"; self._write(rec)
                raise Rejected("Front Lot couldn't start this run.") from None
            step("spawned")
            self._children[rid] = proc
            rec["state"] = "running"; rec["pid"] = proc.pid
            rec["pid_started"] = process_start_time(proc.pid); rec["started"] = time.time()
            self._write(rec)
            return proc.pid

    def claim_for_launch(self, rid: str) -> dict:
        with self._locked(rid):
            return self._claim(rid)

    def mark_running(self, rid: str, pid: int, started: str | None) -> None:
        with self._locked(rid):
            rec = self.get(rid)
            if rec is None or rec["state"] != "launching":
                raise Rejected("This run isn't starting.")
            rec["state"] = "running"; rec["pid"] = pid; rec["pid_started"] = started
            self._write(rec)

    def outcome(self, rid: str) -> dict | None:
        p = self.dir / f"{rid}.outcome.json"
        return json.loads(p.read_text()) if p.exists() else None

    def reconcile(self) -> list[str]:
        for rid, proc in list(self._children.items()):
            if proc.poll() is not None:      # reap our own wrappers so a finished pid is really gone
                self._children.pop(rid, None)
        changed = []
        for rec in self.open_requests():
            with self._locked(rec["id"]):
                cur = self.get(rec["id"])
                out = self.outcome(cur["id"])
                if cur["state"] in ("launching", "running", "uncertain") and out is not None:
                    unresolved = out.get("unresolved") or []
                    if out.get("exit") == 0:
                        new, note = "done", None
                    elif out.get("exit") == EXIT_INPUT_CHANGED:   # refused before any submission: nothing was spent
                        new, note = "failed", "Something it depended on changed after Go, so it didn't run. Nothing was spent."
                    elif unresolved:                      # a provider may still bill or deliver
                        new, note = "uncertain", "paid submission unresolved: " + ", ".join(unresolved)
                    else:
                        new, note = "failed", None
                    if (new, note) != (cur["state"], cur.get("note")):
                        cur["state"] = new; cur["result"] = out; cur["note"] = note
                        self._write(cur); changed.append(cur["id"])
                elif cur["state"] == "approved" and (self.dir / f"{cur['id']}.claim").exists():
                    cur["state"] = "uncertain"; cur["note"] = "may not have started"   # crashed between claim and state write
                    self._write(cur); changed.append(cur["id"])
                elif cur["state"] == "launching" and "pid" not in cur:
                    cur["state"] = "uncertain"; cur["note"] = "may not have started"
                    self._write(cur); changed.append(cur["id"])
                elif cur["state"] == "running" and not _same_process(cur["pid"], cur.get("pid_started")):
                    cur["state"] = "uncertain"; cur["note"] = "finished without a result"
                    self._write(cur); changed.append(cur["id"])
        return changed

    # -- notifications (at least once, across restarts) ------------------------------------------
    def pending_notices(self) -> list[dict]:
        return [r for r in self.all_requests() if r["state"] in NOTIFY and r.get("notified") != r["state"]]

    def mark_notified(self, rid: str, state: str) -> None:
        with self._locked(rid):
            rec = self.get(rid)
            if rec is not None and rec["state"] == state:
                rec["notified"] = state
                self._write(rec, log=False)             # bookkeeping, not a transition

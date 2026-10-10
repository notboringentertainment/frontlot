"""Run one claimed Front Lot request and record its outcome. Detached; never retries."""
from __future__ import annotations

import argparse, hashlib, json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


UNREADABLE = "paid-call ledger unreadable"
WRAPPER_CRASH = "run wrapper crashed"


def _open_reservations(film_root: str | None) -> set[str]:
    """Paid-call reservations still in flight. Raises when the ledger cannot be read: the caller must then
    treat the run's financial status as unknown (fail closed), never as 'nothing outstanding'."""
    if not film_root:
        raise RuntimeError("no film folder recorded for a paid request")
    from tools.cost_tracker import NONTERMINAL_STATES, load_reservations
    return {rid for rid, r in load_reservations(Path(film_root)).items() if r.get("state") in NONTERMINAL_STATES}


def _write_outcome(req_dir: Path, rid: str, code, tail: str, unresolved: list[str]) -> None:
    out = req_dir / f"{rid}.outcome.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"exit": code, "finished": time.time(), "tail": tail, "unresolved": unresolved}))
    tmp.replace(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--request", required=True)
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args(argv)
    req_dir = a.store / "requests"
    log = req_dir / f"{a.request}.log"
    state = {"code": None, "unresolved": []}
    try:
        return _run(a, req_dir, log, state)
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            with open(log, "ab") as fh:
                fh.write(("\n[wrapper crashed]\n" + tb).encode())
            # exit is None unless the child already finished: reconcile settles None as uncertain
            _write_outcome(req_dir, a.request, state["code"], tb[-4096:], state["unresolved"] or [WRAPPER_CRASH])
        except Exception:
            pass
        return 1


def _run(a, req_dir: Path, log: Path, state: dict) -> int:
    rec = json.loads((req_dir / f"{a.request}.json").read_text())
    if rec["state"] not in ("launching", "running"):
        return 2
    if hashlib.sha256(json.dumps(rec["argv"]).encode()).hexdigest() != rec.get("argv_sha256"):
        # exit 5 = refused, an approved input changed after Go, nothing was spent
        _write_outcome(req_dir, a.request, 5, "The approved command changed before it ran, so it didn't run. Nothing was spent.", [])
        return 0
    unresolved: list[str] = []
    before: set[str] | None = set()
    if rec.get("paid"):
        try:
            before = _open_reservations(rec.get("film_root"))
        except Exception:
            before = None
    with open(log, "wb") as fh:
        code = subprocess.run(rec["argv"], cwd=a.repo, stdout=fh, stderr=subprocess.STDOUT).returncode
    state["code"] = code
    if rec.get("paid"):
        try:
            after = _open_reservations(rec.get("film_root"))
            unresolved = sorted(after) if before is None else sorted(after - before)
            if before is None:
                unresolved = unresolved or [UNREADABLE]
        except Exception:
            unresolved = [UNREADABLE]   # status cannot be established: a failed exit becomes uncertain
    state["unresolved"] = unresolved
    tail = log.read_bytes()[-4096:].decode("utf-8", "replace")
    _write_outcome(req_dir, a.request, code, tail, unresolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())

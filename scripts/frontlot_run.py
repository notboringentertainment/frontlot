"""Run one claimed Front Lot request and record its outcome. Detached; never retries."""
from __future__ import annotations

import argparse, json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


UNREADABLE = "paid-call ledger unreadable"


def _open_reservations(film_root: str | None) -> set[str]:
    """Paid-call reservations still in flight. Raises when the ledger cannot be read: the caller must then
    treat the run's financial status as unknown (fail closed), never as 'nothing outstanding'."""
    if not film_root:
        raise RuntimeError("no film folder recorded for a paid request")
    from tools.cost_tracker import NONTERMINAL_STATES, load_reservations
    return {rid for rid, r in load_reservations(Path(film_root)).items() if r.get("state") in NONTERMINAL_STATES}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--request", required=True)
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args(argv)
    req_dir = a.store / "requests"
    rec = json.loads((req_dir / f"{a.request}.json").read_text())
    if rec["state"] not in ("launching", "running"):
        return 2
    unresolved: list[str] = []
    before: set[str] | None = set()
    if rec.get("paid"):
        try:
            before = _open_reservations(rec.get("film_root"))
        except Exception:
            before = None
    log = req_dir / f"{a.request}.log"
    with open(log, "wb") as fh:
        code = subprocess.run(rec["argv"], cwd=a.repo, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rec.get("paid"):
        try:
            after = _open_reservations(rec.get("film_root"))
            unresolved = sorted(after) if before is None else sorted(after - before)
            if before is None:
                unresolved = unresolved or [UNREADABLE]
        except Exception:
            unresolved = [UNREADABLE]   # status cannot be established: a failed exit becomes uncertain
    tail = log.read_bytes()[-4096:].decode("utf-8", "replace")
    out = req_dir / f"{a.request}.outcome.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"exit": code, "finished": time.time(), "tail": tail, "unresolved": unresolved}))
    tmp.replace(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

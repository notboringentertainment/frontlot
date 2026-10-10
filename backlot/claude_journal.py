"""Bounded, durable event journal for one film's Claude session (spec §3.1)."""
from __future__ import annotations

import json, os
from pathlib import Path
from typing import Callable


class Journal:
    def __init__(self, path: Path, keep: int = 2000):
        self.path = Path(path); self.keep = keep
        self.events: list[dict] = []
        raw = self.path.read_text() if self.path.exists() else ""
        for line in raw.splitlines():
            try:
                self.events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        self.events = self.events[-keep:]
        self.seq = self.events[-1]["seq"] if self.events else 0
        if raw and not raw.endswith("\n"):   # a torn last line (crash mid-write): never append onto it
            self._rewrite()

    def _rewrite(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in self.events))
        os.replace(tmp, self.path)

    def append(self, event: dict) -> int:
        self.seq += 1
        rec = {"seq": self.seq, "event": event}
        self.events.append(rec)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        if len(self.events) > self.keep:
            self.events = self.events[-self.keep:]
            self._rewrite()
        return self.seq

    def since(self, seq: int, snapshot: Callable[[], dict]):
        gap = bool(self.events) and seq < self.events[0]["seq"] - 1
        if gap or seq > self.seq:
            return [], {**snapshot(), "kind": "snapshot", "cursor": self.seq}
        return [e for e in self.events if e["seq"] > seq], None

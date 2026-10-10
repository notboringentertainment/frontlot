"""Crash-safe file primitives: atomic JSON/bytes writes, fsync'd JSONL append, atomic move.

Every write here is durable at return: data is fsync'd, and renames are
followed by an fsync of the containing directory so the new entry survives
a power loss. Failures always raise; nothing is swallowed.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write ``data`` to ``path`` via a unique temp file in the same directory + os.replace."""
    path = Path(path)
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(parent)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path | str, obj: Any) -> None:
    payload = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def append_jsonl(path: Path | str, obj: Any) -> None:
    """Append one JSON line to ``path`` with O_APPEND and fsync."""
    path = Path(path)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if "\n" in line:
        raise ValueError("JSONL record must serialize to a single line")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path | str) -> list[Any]:
    """Read all records from a JSONL file (empty list if missing). Malformed lines raise."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: malformed JSONL line") from exc
    return records


def atomic_move(src: Path | str, dst: Path | str) -> None:
    """Atomically rename ``src`` to ``dst`` (same filesystem) and fsync the destination directory."""
    src = Path(src)
    dst = Path(dst)
    os.replace(src, dst)
    _fsync_dir(dst.parent)
    if src.parent != dst.parent:
        _fsync_dir(src.parent)

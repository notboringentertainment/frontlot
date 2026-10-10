import json
import os

import pytest

from lib import state_io


def test_atomic_write_json_roundtrip_and_no_temp_left(tmp_path):
    target = tmp_path / "state.json"
    state_io.atomic_write_json(target, {"b": 1, "a": [1, 2]})
    assert json.loads(target.read_text()) == {"a": [1, 2], "b": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_bytes_replaces_existing(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"old")
    state_io.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_cleans_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "x.json"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state_io.os, "replace", boom)
    with pytest.raises(OSError):
        state_io.atomic_write_bytes(target, b"data")
    assert list(tmp_path.iterdir()) == []


def test_append_jsonl_appends_lines_and_reads_back(tmp_path):
    log = tmp_path / "log.jsonl"
    state_io.append_jsonl(log, {"n": 1})
    state_io.append_jsonl(log, {"n": 2, "s": "é"})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert state_io.read_jsonl(log) == [{"n": 1}, {"n": 2, "s": "é"}]


def test_read_jsonl_missing_is_empty_and_malformed_raises(tmp_path):
    assert state_io.read_jsonl(tmp_path / "nope.jsonl") == []
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"ok": 1}\n{not json\n')
    with pytest.raises(ValueError):
        state_io.read_jsonl(bad)


def test_atomic_move_across_directories(tmp_path):
    src_dir = tmp_path / "a"
    dst_dir = tmp_path / "b"
    src_dir.mkdir()
    dst_dir.mkdir()
    src = src_dir / "f.txt"
    src.write_text("payload")
    dst = dst_dir / "g.txt"
    state_io.atomic_move(src, dst)
    assert not src.exists()
    assert dst.read_text() == "payload"
    assert os.listdir(src_dir) == []

from backlot.claude_journal import Journal

SNAP = lambda: {"state": "ready", "hello": None, "rows": [], "cards": []}


def test_since_returns_tail(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=5)
    for i in range(3):
        j.append({"kind": "row", "i": i})
    events, snap = j.since(1, SNAP)
    assert [e["event"]["i"] for e in events] == [1, 2] and snap is None


def test_gap_returns_snapshot(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=3)
    for i in range(10):
        j.append({"kind": "row", "i": i})
    events, snap = j.since(1, SNAP)
    assert events == [] and snap["kind"] == "snapshot" and snap["state"] == "ready" and snap["cursor"] == 10


def test_cursor_from_the_future_returns_snapshot(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=5); j.append({"kind": "row"})
    events, snap = j.since(99, SNAP)
    assert events == [] and snap["cursor"] == 1


def test_survives_reopen(tmp_path):
    j = Journal(tmp_path / "e.jsonl", keep=5); j.append({"kind": "row"})
    j2 = Journal(tmp_path / "e.jsonl", keep=5)
    assert j2.append({"kind": "row"}) == 2

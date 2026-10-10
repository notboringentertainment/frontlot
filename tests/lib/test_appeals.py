import pytest

from lib.appeals import Appeal, AppealReadError, read_appeals

OPEN = """# Appeal: The chair is green.
type: appeal
mode: hitl
appeal: overrule
writeros-record: mem_0123456789abcdef
overrules: [resolved/vector-color.md]
affects: [Vector Chair]
raised: 2026-10-09T21:00:00.000Z
created: 2026-10-09
claimed:
resolved:

## Question
WriterOS changed something Story-drive decided. The WriterOS version is canon now. Front Lot closes this once it has applied the change.

## Story-drive says
The chair is blue.

## WriterOS now says
The chair is green.

## Where it came from
writeros:looks/location/vector-chair

## Answer

"""

CLOSED = (
    OPEN.replace("resolved:\n", "resolved: 2026-10-10\n", 1).replace("## Answer\n\n", "## Answer — applied (in Front Lot)\nDone.\n")
)


def make(tmp_path, open_files=None, closed_files=None):
    wf = tmp_path / "wayfinder"
    (wf / "tickets").mkdir(parents=True)
    (wf / "resolved").mkdir()
    for n, t in (open_files or {}).items():
        (wf / "tickets" / n).write_text(t, encoding="utf-8")
    for n, t in (closed_files or {}).items():
        (wf / "resolved" / n).write_text(t, encoding="utf-8")
    return tmp_path


def test_reads_open_and_closed_and_ignores_other_tickets(tmp_path):
    root = make(
        tmp_path,
        {"appeal-0123456789ab.md": OPEN, "plain.md": "# Pick a color\ntype: decision\n\n## Question\nx\n"},
        {"appeal-closed.md": CLOSED, "old.md": "# Old\ntype: decision\n"},
    )
    appeals = read_appeals(root)
    assert [a.path.name for a in appeals] == ["appeal-0123456789ab.md", "appeal-closed.md"]
    o, c = appeals
    assert isinstance(o, Appeal)
    assert (o.title, o.kind, o.writeros_record) == ("The chair is green.", "overrule", "mem_0123456789abcdef")
    assert o.affects == frozenset({"vector chair"})
    assert o.story_drive_says == "The chair is blue."
    assert o.writeros_says == "The chair is green."
    assert o.outcome is None
    assert c.outcome == "applied"


def test_folder_decides_open_vs_closed(tmp_path):
    root = make(tmp_path, {"a.md": CLOSED}, {"b.md": OPEN})
    a, b = read_appeals(root)
    assert a.outcome is None
    assert b.outcome == "closed"


def test_affects_lowercased_multiple_and_empty(tmp_path):
    text = OPEN.replace("[Vector Chair]", "[Vector Parlor,  Vector Salon , mem_x]")
    root = make(tmp_path, {"a.md": text, "b.md": OPEN.replace("[Vector Chair]", "[]")})
    a, b = read_appeals(root)
    assert a.affects == frozenset({"vector parlor", "vector salon", "mem_x"})
    assert "vector parlor" in a.affects
    assert b.affects == frozenset()


def test_new_appeal_and_escaped_heading_lines(tmp_path):
    text = (
        OPEN.replace("appeal: overrule", "appeal: new")
        .replace("overrules: [resolved/vector-color.md]", "overrules: []")
        .replace("The chair is blue.", "Nothing — Story-drive never decided this.")
        .replace("The chair is green.\n\n## Where", "\\# Green\nThe chair is green.\n\n## Where")
    )
    (a,) = read_appeals(make(tmp_path, {"a.md": text}))
    assert a.kind == "new"
    assert a.story_drive_says == "Nothing — Story-drive never decided this."
    assert a.writeros_says == "# Green\nThe chair is green."


def test_symlinked_ticket_refused(tmp_path):
    root = make(tmp_path)
    real = tmp_path / "elsewhere.md"
    real.write_text(OPEN)
    (root / "wayfinder" / "tickets" / "a.md").symlink_to(real)
    with pytest.raises(AppealReadError, match="a.md"):
        read_appeals(root)


def test_symlinked_tickets_folder_refused(tmp_path):
    root = make(tmp_path)
    (root / "wayfinder" / "tickets").rmdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.md").write_text(OPEN)
    (root / "wayfinder" / "tickets").symlink_to(other)
    with pytest.raises(AppealReadError, match="tickets"):
        read_appeals(root)


def test_symlinked_wayfinder_folder_refused(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "wayfinder").symlink_to(tmp_path / "real")
    with pytest.raises(AppealReadError, match="can't read your Story-drive folder"):
        read_appeals(tmp_path)


def test_missing_wayfinder_folder_stops_with_plain_reason(tmp_path):
    with pytest.raises(AppealReadError) as e:
        read_appeals(tmp_path)
    assert str(e.value).startswith(
        "Front Lot can't read your Story-drive folder, so it can't check for open appeals: "
    )


def test_missing_root_stops_with_plain_reason(tmp_path):
    with pytest.raises(AppealReadError, match="can't read your Story-drive folder"):
        read_appeals(tmp_path / "gone")


def test_wayfinder_is_a_file(tmp_path):
    (tmp_path / "wayfinder").write_text("x")
    with pytest.raises(AppealReadError, match="can't read your Story-drive folder"):
        read_appeals(tmp_path)


def test_missing_writeros_record_names_the_file(tmp_path):
    root = make(tmp_path, {"broken.md": OPEN.replace("writeros-record: mem_0123456789abcdef\n", "")})
    with pytest.raises(AppealReadError, match="broken.md"):
        read_appeals(root)


@pytest.mark.parametrize(
    "old,new",
    [
        ("appeal: overrule", "appeal: maybe"),
        ("affects: [Vector Chair]", "affects: Vector Chair"),
        ("## Story-drive says", "## Something else"),
        ("# Appeal: The chair is green.\n", ""),
    ],
)
def test_malformed_appeal_raises(tmp_path, old, new):
    root = make(tmp_path, {"bad.md": OPEN.replace(old, new, 1)}, None)
    with pytest.raises(AppealReadError, match="bad.md"):
        read_appeals(root)


def test_unreadable_bytes_raise(tmp_path):
    root = make(tmp_path)
    (root / "wayfinder" / "tickets" / "bin.md").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(AppealReadError, match="bin.md"):
        read_appeals(root)


def test_directory_named_md_refused(tmp_path):
    root = make(tmp_path)
    (root / "wayfinder" / "tickets" / "dir.md").mkdir()
    with pytest.raises(AppealReadError, match="dir.md"):
        read_appeals(root)


def test_missing_tickets_folder_alone_is_no_appeals(tmp_path):
    (tmp_path / "wayfinder").mkdir()
    assert read_appeals(tmp_path) == []

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


def test_missing_tickets_folder_raises(tmp_path):
    (tmp_path / "wayfinder" / "resolved").mkdir(parents=True)
    with pytest.raises(AppealReadError, match="tickets"):
        read_appeals(tmp_path)


def test_missing_resolved_folder_reads_fine(tmp_path):
    (tmp_path / "wayfinder" / "tickets").mkdir(parents=True)
    (tmp_path / "wayfinder" / "tickets" / "a.md").write_text(OPEN)
    assert len(read_appeals(tmp_path)) == 1


def test_non_applied_heading_is_closed(tmp_path):
    text = CLOSED.replace("applied (in Front Lot)", "dismissed by hand")
    (a,) = read_appeals(make(tmp_path, None, {"a.md": text}))
    assert a.outcome == "closed"


@pytest.mark.parametrize("sub", ["tickets", "resolved"])
def test_icloud_placeholder_raises(tmp_path, sub):
    root = make(tmp_path)
    (root / "wayfinder" / sub / ".appeal-x.md.icloud").write_bytes(b"")
    with pytest.raises(AppealReadError, match=r"iCloud.*appeal-x\.md\.icloud") as err:
        read_appeals(root)
    assert "In Finder, right-click the Story-drive folder and choose Download Now." in str(err.value)


@pytest.mark.parametrize("sub", ["tickets", "resolved"])
def test_other_icloud_placeholders_are_ignored(tmp_path, sub):
    root = make(tmp_path, {"appeal-0123456789ab.md": OPEN})
    for name in (".appeal-x.md.bak.icloud", ".notes.txt.icloud", "x.md.icloud"):
        (root / "wayfinder" / sub / name).write_bytes(b"")
    assert [a.path.name for a in read_appeals(root)] == ["appeal-0123456789ab.md"]


def test_indented_escaped_heading_lines_are_unescaped(tmp_path):
    text = OPEN.replace("The chair is green.\n\n## Where", "  \\# Green\n\t\\## Shade\nThe chair is green.\n\n## Where")
    (a,) = read_appeals(make(tmp_path, {"a.md": text}))
    assert a.writeros_says == "# Green\n\t## Shade\nThe chair is green."


def test_open_only_read_skips_resolved(tmp_path):
    root = make(tmp_path, {"appeal-0123456789ab.md": OPEN}, {"bad.md": OPEN.replace("appeal: overrule", "appeal: maybe")})
    (root / "wayfinder" / "resolved" / ".appeal-old.md.icloud").write_bytes(b"")
    assert [a.path.name for a in read_appeals(root, include_resolved=False)] == ["appeal-0123456789ab.md"]
    with pytest.raises(AppealReadError):
        read_appeals(root)
    import shutil

    shutil.rmtree(root / "wayfinder" / "tickets")
    with pytest.raises(AppealReadError, match="tickets"):
        read_appeals(root, include_resolved=False)


# ---- Task C2: check_appeals / job_names (Front Lot's spend-time check) ----

from lib.appeals import OpenAppealError, check_appeals, job_names  # noqa: E402


def film_with_appeals(tmp_path, open_files=None, closed_files=None):
    """A film folder whose project.yaml names a Story-drive folder with these appeals."""
    writing = make(tmp_path / "writing", open_files, closed_files)
    film = tmp_path / "film"
    film.mkdir()
    (film / "project.yaml").write_text(f"wayfinder_root: {writing}\n")
    return film


def test_open_appeal_touching_the_job_stops_it(tmp_path):
    film = film_with_appeals(tmp_path, {"appeal-0123456789ab.md": OPEN})
    with pytest.raises(OpenAppealError) as caught:
        check_appeals(film, {"vector chair"})
    assert str(caught.value) == (
        "WriterOS changed canon this job uses: The chair is green (Story-drive said: The chair is blue; "
        "ticket appeal-0123456789ab.md). Apply the change before generating? Nothing is spent until you answer."
    )
    assert caught.value.appeal.path.name == "appeal-0123456789ab.md"


def test_new_appeal_message_and_first_lines_only(tmp_path):
    text = (
        OPEN.replace("appeal: overrule", "appeal: new")
        .replace("The chair is blue.\n", "Nothing — Story-drive never decided this.\n")
        .replace("## WriterOS now says\nThe chair is green.\n", "## WriterOS now says\nThe chair is green.\nSecond line.\n")
    )
    film = film_with_appeals(tmp_path, {"appeal-a.md": text})
    with pytest.raises(OpenAppealError, match=r"^WriterOS changed canon this job uses: The chair is green \(Story-drive said: "
                       r"Nothing — Story-drive never decided this; ticket appeal-a\.md\)\. Apply"):
        check_appeals(film, {"vector chair"})


def test_several_matching_appeals_name_the_first_and_count_the_rest(tmp_path):
    film = film_with_appeals(tmp_path, {"appeal-b.md": OPEN, "appeal-a.md": OPEN, "appeal-c.md": OPEN})
    with pytest.raises(OpenAppealError) as caught:
        check_appeals(film, {"vector chair"})
    assert caught.value.appeal.path.name == "appeal-a.md"
    assert str(caught.value).endswith("Nothing is spent until you answer. 2 more open appeals also touch this job.")
    film2 = film_with_appeals(tmp_path / "two", {"appeal-b.md": OPEN, "appeal-a.md": OPEN})
    with pytest.raises(OpenAppealError, match=r"answer\. 1 more open appeal also touches this job\.$"):
        check_appeals(film2, {"vector chair"})


def test_open_appeal_about_something_else_does_not_stop(tmp_path):
    film = film_with_appeals(tmp_path, {"appeal-a.md": OPEN})
    check_appeals(film, {"vector station", "vector-dock"})
    check_appeals(film, set())


def test_closed_appeal_never_stops(tmp_path):
    film = film_with_appeals(tmp_path, None, {"appeal-a.md": CLOSED, "appeal-b.md": OPEN})
    check_appeals(film, {"vector chair"})


def test_hyphen_space_and_case_variants_match(tmp_path):
    text = OPEN.replace("[Vector Chair]", "[vector-dock, Vector Parlor]")
    film = film_with_appeals(tmp_path, {"appeal-a.md": text})
    for names in ({"Vector Dock"}, {" vector-parlor "}, {"VECTOR-DOCK"}):
        with pytest.raises(OpenAppealError):
            check_appeals(film, names)


def test_no_wayfinder_root_skips_the_check(tmp_path):
    film = tmp_path / "film"
    film.mkdir()
    (film / "project.yaml").write_text("budget_usd_cap: 1.0\n")
    check_appeals(film, {"vector chair"})
    (film / "project.yaml").write_text("wayfinder_root: ''\n")
    check_appeals(film, {"vector chair"})


def test_unreachable_story_drive_folder_stops_spending(tmp_path):
    film = tmp_path / "film"
    film.mkdir()
    (film / "project.yaml").write_text(f"wayfinder_root: {tmp_path / 'evicted'}\n")
    with pytest.raises(AppealReadError, match="Front Lot can't read your Story-drive folder"):
        check_appeals(film, {"vector chair"})


def test_malformed_appeal_stops_spending_even_when_unrelated(tmp_path):
    film = film_with_appeals(tmp_path, {"appeal-a.md": OPEN.replace("appeal: overrule", "appeal: maybe")})
    with pytest.raises(AppealReadError, match="malformed"):
        check_appeals(film, {"vector station"})


def test_job_names_collects_look_refs_references_and_entities():
    inputs = {
        "reference_manifest": [
            {"asset_id": "a", "path": "p", "role": "hero", "visual_bible_entity_id": "Vector-Dock"},
            {"asset_id": "b", "path": "q", "role": "hero"},
            "not a dict",
        ],
        "entities": [" Vector Station ", 7, ""],
    }
    verified = {"look_refs": [{"entity_kind": "location", "entity_id": "vector-chair", "look_hash": "0" * 64}]}
    assert job_names(inputs, verified) == {"vector dock", "vector station", "vector chair"}
    assert job_names({}, {"look_refs": None}) == set()
    assert job_names({"entities": "Vector Station"}, {}) == set()


# ---- apply_appeal ----
from lib.appeals import ApplyAppealError, apply_appeal  # noqa: E402


def test_apply_closes_in_place_and_moves(tmp_path):
    root = make(tmp_path, {"appeal-0123456789ab.md": OPEN})
    new, step = apply_appeal(root, "appeal-0123456789ab.md", "2026-10-10")
    assert step == (
        "Front Lot uses the WriterOS version from today. "
        "Next, ratify the updated look in Front Lot so its references match."
    )
    assert "look_run" not in step and "--" not in step
    assert new == (root / "wayfinder" / "resolved" / "appeal-0123456789ab.md").resolve()
    assert not (root / "wayfinder" / "tickets" / "appeal-0123456789ab.md").exists()
    assert list((root / "wayfinder" / "tickets").iterdir()) == []
    text = new.read_text(encoding="utf-8")
    expected = (
        OPEN.replace("resolved:\n", "resolved: 2026-10-10\n", 1).replace(
            "## Answer\n",
            "## Answer — applied (in Front Lot)\nFront Lot uses the WriterOS version from today. "
            "Next, ratify the updated look in Front Lot so its references match.\n",
        )
    )
    assert text == expected
    (a,) = read_appeals(root)
    assert (a.outcome, a.path.name) == ("applied", "appeal-0123456789ab.md")


def test_apply_names_a_generic_step_for_non_looks(tmp_path):
    other = OPEN.replace("writeros:looks/location/vector-chair", "writeros:facts/vector-chair")
    root = make(tmp_path, {"appeal-1.md": other})
    text = apply_appeal(root, "appeal-1.md", "2026-10-10")[0].read_text(encoding="utf-8")
    assert (
        "Front Lot uses the WriterOS version from today. "
        "Next, refresh Front Lot's canon from Story-drive so its references match.\n"
    ) in text


@pytest.mark.parametrize("name", ["../x.md", "sub/appeal-1.md", "appeal-1.txt", ".appeal-1.md", ""])
def test_apply_refuses_bad_names(tmp_path, name):
    root = make(tmp_path, {"appeal-1.md": OPEN})
    with pytest.raises(ApplyAppealError, match="not a ticket file name"):
        apply_appeal(root, name, "2026-10-10")


def test_apply_refuses_non_appeal_missing_and_closed(tmp_path):
    root = make(tmp_path, {"plain.md": "# Pick\ntype: decision\n\n## Answer\n\n"}, {"appeal-done.md": CLOSED})
    with pytest.raises(ApplyAppealError, match="not an appeal"):
        apply_appeal(root, "plain.md", "2026-10-10")
    with pytest.raises(ApplyAppealError, match="not an appeal"):
        apply_appeal(root, "appeal-nope.md", "2026-10-10")
    with pytest.raises(ApplyAppealError, match="already closed"):
        apply_appeal(root, "appeal-done.md", "2026-10-10")
    assert (root / "wayfinder" / "tickets" / "plain.md").exists()


def test_apply_refuses_to_overwrite_and_leaves_ticket_untouched(tmp_path):
    root = make(tmp_path, {"appeal-1.md": OPEN}, {"appeal-1.md": "# other\ntype: decision\n"})
    with pytest.raises(ApplyAppealError, match="did not overwrite"):
        apply_appeal(root, "appeal-1.md", "2026-10-10")
    assert (root / "wayfinder" / "tickets" / "appeal-1.md").read_text(encoding="utf-8") == OPEN
    assert (root / "wayfinder" / "resolved" / "appeal-1.md").read_text(encoding="utf-8") == "# other\ntype: decision\n"


def test_apply_refuses_answered_ticket_and_bad_date(tmp_path):
    answered = OPEN.replace("## Answer\n\n", "## Answer\nsomething\n")
    root = make(tmp_path, {"appeal-1.md": answered})
    with pytest.raises(ApplyAppealError, match="already has text"):
        apply_appeal(root, "appeal-1.md", "2026-10-10")
    with pytest.raises(ApplyAppealError, match="YYYY-MM-DD"):
        apply_appeal(root, "appeal-1.md", "tomorrow")


def test_apply_refuses_symlinked_ticket(tmp_path):
    root = make(tmp_path)
    real = tmp_path / "elsewhere.md"
    real.write_text(OPEN, encoding="utf-8")
    (root / "wayfinder" / "tickets" / "appeal-1.md").symlink_to(real)
    with pytest.raises(AppealReadError):
        apply_appeal(root, "appeal-1.md", "2026-10-10")
    assert real.read_text(encoding="utf-8") == OPEN


def test_apply_rollback_still_removes_target_when_temp_cleanup_fails(tmp_path, monkeypatch):
    from pathlib import Path

    root = make(tmp_path, {"appeal-1.md": OPEN})
    wf = root / "wayfinder"
    ticket = (wf / "tickets" / "appeal-1.md").resolve()
    real_unlink = Path.unlink

    def flaky_unlink(self, *a, **k):
        if self.name == ".appeal-1.md.tmp" or self == ticket:
            raise OSError("busy")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    with pytest.raises(ApplyAppealError, match="could not close"):
        apply_appeal(root, "appeal-1.md", "2026-10-10")
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert ticket.read_text(encoding="utf-8") == OPEN
    assert not (wf / "resolved" / "appeal-1.md").exists()


def test_apply_failure_midway_leaves_open_ticket_and_retry_works(tmp_path, monkeypatch):
    import os

    root = make(tmp_path, {"appeal-1.md": OPEN})
    real_link = os.link

    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr("lib.appeals.os.link", boom)
    with pytest.raises(ApplyAppealError, match="could not close"):
        apply_appeal(root, "appeal-1.md", "2026-10-10")
    wf = root / "wayfinder"
    assert (wf / "tickets" / "appeal-1.md").read_text(encoding="utf-8") == OPEN
    assert list((wf / "resolved").iterdir()) == []
    monkeypatch.setattr("lib.appeals.os.link", real_link)
    new, _ = apply_appeal(root, "appeal-1.md", "2026-10-10")
    assert new.exists() and list((wf / "tickets").iterdir()) == []

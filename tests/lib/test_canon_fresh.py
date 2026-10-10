import hashlib
import io
import json

import pytest

from lib.canon_fresh import CanonReadError, read_canon_changes, warn_scoped_out

SCOPED_OUT = """# What does the glass foundry look like?
id: wf-0001
type: grill
resolved: 2026-10-09

## Question
Pick the look.

## Answer — scoped out

Scoped out: the foundry is no longer in the story.

## Look spec

```yaml
entity_kind: location
entity_id: glass-foundry
```
"""

PINNED = """# Which city?
type: decision
resolved: 2026-09-01

## Answer
Harbor town.
"""

LATER = """# Lay the opening sequence
type: decision
resolved: 2026-10-04

## Answer
Done.
"""

OLDER_UNPINNED = """# An old note
type: decision
resolved: 2026-08-01

## Answer
Old.
"""


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make(tmp_path, *, cast_locations=("glass-foundry", "lantern-pier"), wayfinder=True):
    story = tmp_path / "story"
    resolved = story / "wayfinder" / "resolved"
    resolved.mkdir(parents=True)
    (story / "Canon Note.md").write_text("canon v1\n", encoding="utf-8")
    (resolved / "which-city.md").write_text(PINNED, encoding="utf-8")
    (resolved / "old-note.md").write_text(OLDER_UNPINNED, encoding="utf-8")
    sources = [
        {"path": str(story / "Canon Note.md"), "sha256": _sha(story / "Canon Note.md")},
        {"path": str(resolved / "which-city.md"), "sha256": _sha(resolved / "which-city.md")},
        {"path": str(story / "gone.md"), "sha256": "0" * 64},
        {"path": "/elsewhere/outside.md", "sha256": "0" * 64},
    ]
    packet = {
        "source_documents": sources,
        "locations": [{"id": "glass-foundry", "name": "The glass foundry"}, {"id": "lantern-pier", "name": "Lantern pier"}],
        "characters": [{"id": "test-hero", "name": "Test Hero"}],
        "provenance": {"ingested_at": "2026-09-29"},
    }
    film = tmp_path / "film"
    film.mkdir()
    (film / "project.yaml").write_text(f"wayfinder_root: {story}\n" if wayfinder else "title: x\n", encoding="utf-8")
    (film / "checkpoint_canon_ingest.json").write_text(json.dumps({"artifacts": {"canon_packet": packet}}), encoding="utf-8")
    (film / "checkpoint_proposal.json").write_text(json.dumps({"artifacts": {"proposal_packet": {"cast": {
        "character_ids": ["test-hero"], "location_ids": list(cast_locations)}}}}), encoding="utf-8")
    return film, story


def test_unchanged_canon_reports_nothing_but_the_missing_pin(tmp_path):
    film, _ = make(tmp_path)
    c = read_canon_changes(film)
    assert c.snapshot_date == "2026-09-29"
    assert [(s.path, s.state) for s in c.changed_sources] == [("gone.md", "missing")]
    assert c.new_decisions == [] and c.mismatches == []


def test_edits_new_decisions_and_scope_outs_are_reported(tmp_path):
    film, story = make(tmp_path)
    (story / "Canon Note.md").write_text("canon v2\n", encoding="utf-8")
    resolved = story / "wayfinder" / "resolved"
    (resolved / "glass-foundry-look.md").write_text(SCOPED_OUT, encoding="utf-8")
    (resolved / "opening.md").write_text(LATER, encoding="utf-8")
    c = read_canon_changes(film)
    assert ("Canon Note.md", "changed") in [(s.path, s.state) for s in c.changed_sources]
    assert [(d.file, d.resolved, d.scoped_out) for d in c.new_decisions] == [
        ("opening.md", "2026-10-04", False),
        ("glass-foundry-look.md", "2026-10-09", True),
    ]
    assert len(c.mismatches) == 1
    m = c.mismatches[0]
    assert (m.kind, m.entity_id, m.ticket) == ("location", "glass-foundry", "glass-foundry-look.md")
    assert '"The glass foundry"' in m.message and "2026-10-09" in m.message
    assert c.to_dict()["unchanged"] is False


def test_scope_out_of_something_not_in_the_cast_is_no_mismatch(tmp_path):
    film, story = make(tmp_path, cast_locations=("lantern-pier",))
    (story / "wayfinder" / "resolved" / "glass-foundry-look.md").write_text(SCOPED_OUT, encoding="utf-8")
    c = read_canon_changes(film)
    assert c.mismatches == []
    assert "glass-foundry" in c.scoped_out


def test_reader_never_writes(tmp_path):
    film, story = make(tmp_path)
    (story / "wayfinder" / "resolved" / "glass-foundry-look.md").write_text(SCOPED_OUT, encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    read_canon_changes(film)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_film_without_story_drive_folder_is_skipped(tmp_path):
    film, _ = make(tmp_path, wayfinder=False)
    assert read_canon_changes(film) is None


def test_missing_snapshot_is_a_plain_error(tmp_path):
    film, _ = make(tmp_path)
    (film / "checkpoint_canon_ingest.json").unlink()
    with pytest.raises(CanonReadError, match="no approved canon snapshot"):
        read_canon_changes(film)


def test_warn_scoped_out_warns_only_for_job_names(tmp_path):
    film, story = make(tmp_path)
    (story / "wayfinder" / "resolved" / "glass-foundry-look.md").write_text(SCOPED_OUT, encoding="utf-8")
    out = io.StringIO()
    assert warn_scoped_out(film, {"Glass Foundry"}, stream=out)
    assert "Heads up" in out.getvalue() and "glass-foundry" in out.getvalue()
    assert warn_scoped_out(film, {"lantern-pier"}, stream=io.StringIO()) == []


def test_warn_scoped_out_never_raises(tmp_path):
    film, _ = make(tmp_path)
    (film / "checkpoint_canon_ingest.json").unlink()
    assert warn_scoped_out(film, {"glass-foundry"}, stream=io.StringIO()) == []


def test_next_step_is_given_with_every_mismatch(tmp_path):
    film, story = make(tmp_path)
    (story / "wayfinder" / "resolved" / "glass-foundry-look.md").write_text(SCOPED_OUT, encoding="utf-8")
    (m,) = read_canon_changes(film).mismatches
    assert "Nothing is blocked" in m.next_step

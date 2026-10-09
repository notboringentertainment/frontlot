# tests/backlot/test_claude_ops.py
import hashlib, json, os
from pathlib import Path

import pytest

from backlot import claude_ops as ops

LOOK = "a" * 64


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; film = repo / "projects" / "film"; work = film / "frontlot-work"
    work.mkdir(parents=True)
    (film / "checkpoint_headshots.json").write_text("{}")
    (film / "checkpoint_visual_bible.json").write_text("{}")
    # The real lookups read signed canon, receipts and config; these seams keep the adapter tests pure.
    monkeypatch.setattr(ops, "_active_look_hash", lambda film_root, entity: LOOK)
    monkeypatch.setattr(ops, "_brief_revision", lambda film_root, shot: "rev-1")
    monkeypatch.setattr(ops, "_config_digest", lambda film_root: "c" * 64)
    monkeypatch.setattr(ops, "_active_headshot", lambda film_root, entity: "hs-1")
    monkeypatch.setattr(ops, "_hero_budget", lambda film_root, entity, look: (3, 1))  # 3 attempts left, 1 unjudged
    monkeypatch.setattr(ops, "_sheet_cap", lambda film_root: 2)
    return repo, film, work, tmp_path / "snap"


def prep(world, op, **params):
    repo, film, work, snap = world
    return ops.prepare(op, params, repo=repo, film_slug="film", film_root=film, snapshot_dir=snap)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_project_is_inserted_by_front_lot_not_claude(world):
    p = prep(world, "look", entity="hero-a", kind="character", dry_run=True)
    assert p.paid is False
    assert p.argv[p.argv.index("--project") + 1] == "film"
    with pytest.raises(ops.OpError):
        prep(world, "look", entity="hero-a", project="other-film")


def test_look_dry_run_is_labelled_as_a_preview_not_a_lock(world):
    # Early real run: a dry run showed as "Lock the look for hero-a"; Claude had to explain nothing was locked.
    assert prep(world, "look", entity="hero-a", dry_run=True).summary == "Preview the look for hero-a (nothing is locked)"
    assert prep(world, "look", entity="hero-a").summary == "Lock the look for hero-a"


def test_unknown_op_and_bad_entity_refused(world):
    with pytest.raises(ops.OpError):
        prep(world, "rm_rf")
    with pytest.raises(ops.OpError):
        prep(world, "look", entity="../x")


def test_headshot_candidates_freezes_checkpoint_look_and_config_and_shows_the_full_budget(world):
    repo, film, *_ = world
    p = prep(world, "headshot_candidates", entity="hero-a", candidates=3)
    cp = film / "checkpoint_headshots.json"
    assert p.paid is True and p.inputs == {cp: sha(b"{}")}
    assert f"--expect-input-sha={cp}={sha(b'{}')}" in p.argv
    assert p.argv[p.argv.index("--expect-look-hash") + 1] == LOOK
    assert p.argv[p.argv.index("--expect-config-sha") + 1] == "c" * 64
    assert p.estimate_usd == 0.41   # 3 attempts left × (0.07 + 0.05) + 1 unjudged attempt × 0.05
    with pytest.raises(ops.OpError):
        prep(world, "headshot_candidates", entity="hero-a", candidates=5)  # script cap is 4


def test_headshot_finish_is_paid_and_priced_like_a_regeneration(world):
    p = prep(world, "headshot_finish", entity="hero-a")
    assert p.paid is True and "--finish" in p.argv and "--expect-config-sha" in p.argv
    assert p.estimate_usd == 0.46


def test_headshot_import_only_stages_and_needs_origin_tool(world):
    repo, film, work, snap = world
    (work / "face.png").write_bytes(b"png")
    with pytest.raises(ops.OpError):
        prep(world, "headshot_import", entity="hero-a", image="face.png")
    p = prep(world, "headshot_import", entity="hero-a", image="face.png", origin_tool="midjourney")
    assert p.paid is False and p.argv[p.argv.index("--origin-tool") + 1] == "midjourney"
    assert p.argv[p.argv.index("--import") + 1] == str(p.snapshot["image"])
    assert any(a.startswith("--expect-input-sha=") for a in p.argv)
    (film / "checkpoint_headshots.json").write_text(json.dumps({"metadata": {"run_state": {"hero-a": {"mode": "select"}}}}))
    with pytest.raises(ops.OpError, match="in progress"):
        prep(world, "headshot_import", entity="hero-a", image="face.png", origin_tool="midjourney")


def test_sheet_freezes_bible_look_config_and_headshot(world):
    repo, film, *_ = world
    p = prep(world, "sheet", entity="hero-a", roles=["turnaround", "expressions", "wardrobe"])
    assert p.paid is True and film / "checkpoint_visual_bible.json" in p.inputs
    assert p.argv[p.argv.index("--expect-headshot") + 1] == "hs-1"
    assert p.argv[p.argv.index("--roles") + 1] == "turnaround,expressions,wardrobe"
    assert p.estimate_usd == 0.86
    only = prep(world, "sheet", entity="hero-a", roles=["wardrobe"])   # mandatory roles may regenerate: priced too
    assert only.estimate_usd == 0.86


def test_snapshot_digest_is_of_the_bytes_snapshotted(world):
    repo, film, work, snap = world
    body = json.dumps({"prompt": "x"}).encode()
    (work / "s.json").write_bytes(body)
    p = prep(world, "shot_generate", shot_id="Shot_01", settings="s.json", tool="seedream_image")
    snap_path = p.snapshot["settings"]
    assert snap_path.read_bytes() == body and p.inputs == {snap_path: sha(body)}
    assert str(snap_path) in p.argv and str(work / "s.json") not in p.argv
    assert p.argv[p.argv.index("generate") - 1] == str(film)  # positional project path
    assert p.argv[p.argv.index("--expect-brief-revision") + 1] == "rev-1"
    assert p.estimate_usd is None   # "cost unknown": the real inputs are built inside the script


def test_work_files_never_follow_symlinks_or_leave_the_work_area(world):
    repo, film, work, snap = world
    (film / "secret.json").write_text("{}")
    os.symlink(film / "secret.json", work / "link.json")
    (work / "sub").mkdir(); os.symlink(film, work / "sub" / "up")
    for name in ("link.json", "sub/up/secret.json", "../secret.json", "/etc/passwd"):
        with pytest.raises(ops.OpError):
            prep(world, "shot_generate", shot_id="s1", settings=name, tool="seedream_image")
    with pytest.raises(ops.OpError):
        prep(world, "shot_inspect", shot_id="../s1")


def test_shot_prepare_refuses_brief_paths_outside_the_film(world):
    repo, film, work, snap = world
    (film / "canon").mkdir(); (film / "canon" / "note.md").write_text("canon")
    (film / "a.png").write_bytes(b"png")

    def brief(sources, ref="a.png"):
        (work / "b.json").write_text(json.dumps({"shot_id": "s1", "source_paths": sources,
                                                 "reference_manifest": [{"path": ref}]}))
        return prep(world, "shot_prepare", brief="b.json", note="Ben asked for this shot")

    assert brief(["canon/note.md"]).argv[-2:] == ["--note", "Ben asked for this shot"]
    for bad in ([str(Path.home() / ".openmontage/gates/key")], ["../../outside.md"], ["frontlot-work/b.json"]):
        with pytest.raises(ops.OpError):
            brief(bad)
    with pytest.raises(ops.OpError):
        brief(["canon/note.md"], ref="/etc/hosts")


def test_shot_notes_are_required(world):
    with pytest.raises(ops.OpError):
        prep(world, "shot_stop", shot_id="s1")
    p = prep(world, "shot_stop", shot_id="s1", note="Ben asked to stop this shot")
    assert p.argv[-2:] == ["--note", "Ben asked to stop this shot"]


def test_case_changed_work_area_prefix_is_still_the_work_area(world):
    repo, film, work, snap = world
    probe = film / "ProbeCase"; probe.mkdir()
    if not (film / "probecase").exists():
        pytest.skip("case-sensitive filesystem")
    (work / "x.md").write_text("claude wrote this")
    for spelled in ("FrontLot-Work/x.md", "FRONTLOT-WORK/x.md"):
        (work / "b.json").write_text(json.dumps({"shot_id": "s1", "source_paths": [spelled]}))
        with pytest.raises(ops.OpError, match="work area"):
            prep(world, "shot_prepare", brief="b.json", note="n")


def test_a_named_pipe_in_the_work_area_is_refused_not_hung(world):
    repo, film, work, snap = world
    os.mkfifo(work / "pipe.json")
    with pytest.raises(ops.OpError):
        prep(world, "shot_generate", shot_id="s1", settings="pipe.json", tool="seedream_image")


def test_flag_like_ids_are_refused(world):
    with pytest.raises(ops.OpError):
        prep(world, "sheet_finish", entity="--finish")
    with pytest.raises(ops.OpError):
        prep(world, "shot_select", shot_id="s1", take_id="-h", note="n")
    assert prep(world, "shot_select", shot_id="s1", take_id="take-1", note="n").argv[-4:-2] == ["s1", "take-1"]


def test_brief_lists_must_be_lists_of_strings(world):
    repo, film, work, snap = world
    for body in ({"source_paths": "canon/note.md"}, {"source_paths": {"a": 1}}, {"source_paths": [1]},
                 {"reference_manifest": "abc"}, {"reference_manifest": ["a.png"]}):
        (work / "b.json").write_text(json.dumps(body))
        with pytest.raises(ops.OpError):
            prep(world, "shot_prepare", brief="b.json", note="n")


def test_unreadable_brief_revision_is_a_plain_error(world, monkeypatch):
    import lib.supervised_production as sp
    (world[2] / "s.json").write_text("{}")
    monkeypatch.undo()  # use the real _brief_revision
    monkeypatch.setattr(sp, "read_brief", lambda film, shot: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(ops.OpError):
        ops._brief_revision(world[1], "s1")

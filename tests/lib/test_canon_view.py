import json
from pathlib import Path

from lib.canon_view import VIEW_DIR, build_view, plan_view


def _ref(asset: str) -> dict:
    return {"asset_id": asset, "path": f"canon/visual/objects/{asset}.png", "role": "x", "provenance": {}}


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "canon/visual/objects").mkdir(parents=True)
    for a in ("aa", "bb", "cc", "dd", "ee"):
        (p / f"canon/visual/objects/{a}.png").write_bytes(b"png")
    bible = {
        "characters": [
            {"id": "marlow-vex", "status": "approved", "hero": _ref("aa"),
             "sheet": {"front": _ref("bb"), "wardrobe": _ref("cc")}},
            {"id": "juno-pike", "status": "draft", "hero": _ref("dd"), "sheet": {"front": _ref("dd")}},
        ],
        "locations": [{"id": "salt-flats", "status": "approved", "establishing": _ref("ee"), "angles": [_ref("aa")]}],
    }
    (p / "checkpoint_visual_bible.json").write_text(json.dumps({"artifacts": {"visual_bible": bible}}))
    packet = {"characters": [
        {"entity_id": "juno-pike", "hero": _ref("dd"), "approval_receipt_id": "r-1"},
        {"entity_id": "marlow-vex", "hero": _ref("bb"), "approval_receipt_id": "r-2"},
        {"entity_id": "orin-tal", "hero": _ref("cc")},
    ]}
    (p / "checkpoint_headshots.json").write_text(json.dumps({"artifacts": {"headshot_packet": packet}}))
    return p


def test_plan_lists_only_approved(tmp_path):
    plan = dict(plan_view(_project(tmp_path)))
    assert plan[f"{VIEW_DIR}/marlow-vex/hero.png"] == "canon/visual/objects/aa.png"
    assert plan[f"{VIEW_DIR}/marlow-vex/front.png"] == "canon/visual/objects/bb.png"
    assert plan[f"{VIEW_DIR}/marlow-vex/wardrobe.png"] == "canon/visual/objects/cc.png"
    assert f"{VIEW_DIR}/marlow-vex/profile.png" not in plan
    assert plan[f"{VIEW_DIR}/salt-flats/establishing.png"] == "canon/visual/objects/ee.png"
    assert plan[f"{VIEW_DIR}/salt-flats/angle_1.png"] == "canon/visual/objects/aa.png"
    # draft bible entry falls back to its approved headshot only
    assert plan[f"{VIEW_DIR}/juno-pike/hero.png"] == "canon/visual/objects/dd.png"
    assert not any(k.startswith(f"{VIEW_DIR}/juno-pike/front") for k in plan)
    # unapproved headshot never appears
    assert not any("orin-tal" in k for k in plan)


def test_build_makes_relative_symlinks_and_is_idempotent(tmp_path):
    p = _project(tmp_path)
    build_view(p)
    link = p / VIEW_DIR / "marlow-vex/front.png"
    assert link.is_symlink() and not Path(link.readlink()).is_absolute()
    assert link.resolve() == (p / "canon/visual/objects/bb.png").resolve()
    assert "marlow-vex" in (p / VIEW_DIR / "INDEX.md").read_text()
    # revoke: remove approval, rebuild, stale links vanish
    cp = json.loads((p / "checkpoint_visual_bible.json").read_text())
    cp["artifacts"]["visual_bible"]["locations"][0]["status"] = "superseded"
    (p / "checkpoint_visual_bible.json").write_text(json.dumps(cp))
    build_view(p)
    assert not (p / VIEW_DIR / "salt-flats").exists()
    assert link.is_symlink()

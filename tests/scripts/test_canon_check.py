import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests" / "lib"))

import canon_check  # noqa: E402
from test_canon_fresh import SCOPED_OUT, make  # noqa: E402


def test_prints_mismatches_and_new_decisions(tmp_path, monkeypatch, capsys):
    film, story = make(tmp_path)
    (story / "wayfinder" / "resolved" / "glass-foundry-look.md").write_text(SCOPED_OUT, encoding="utf-8")
    monkeypatch.setattr(canon_check, "resolve_project_root", lambda slug: film)
    assert canon_check.main(["--project", "film"]) == 0
    out = capsys.readouterr().out
    assert "Approved canon snapshot: 2026-09-29." in out
    assert 'Mismatch: The approved trailer cast lists "The glass foundry"' in out
    assert "(scoped out) [wayfinder/resolved/glass-foundry-look.md]" in out
    assert "- gone.md (missing)" in out


def test_unreadable_snapshot_fails_plainly(tmp_path, monkeypatch, capsys):
    film, _ = make(tmp_path)
    (film / "checkpoint_canon_ingest.json").unlink()
    monkeypatch.setattr(canon_check, "resolve_project_root", lambda slug: film)
    assert canon_check.main(["--project", "film"]) == 1
    assert "no approved canon snapshot" in capsys.readouterr().err

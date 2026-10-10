import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import appeal_answer  # noqa: E402
from lib.appeals import read_appeals  # noqa: E402

APPEAL = """# Appeal: The chair is green.
type: appeal
mode: hitl
appeal: new
writeros-record: mem_0123456789abcdef
affects: [Vector Chair]
raised: 2026-10-09T21:00:00.000Z
created: 2026-10-09
claimed:
resolved:

## Question
Q.

## Story-drive says
Nothing yet.

## WriterOS now says
The chair is green.

## Where it came from
writeros:looks/location/vector-chair

## Answer

"""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    film = projects / "vector-film"
    film.mkdir(parents=True)
    drive = tmp_path / "drive"
    (drive / "wayfinder" / "tickets").mkdir(parents=True)
    (drive / "wayfinder" / "tickets" / "appeal-1.md").write_text(APPEAL, encoding="utf-8")
    (film / "project.yaml").write_text(f"wayfinder_root: {drive.resolve()}\n", encoding="utf-8")
    monkeypatch.setattr("lib.paths.PROJECTS_DIR", projects)
    return drive


def test_cli_closes_the_appeal(setup, capsys):
    assert appeal_answer.main(["--project", "vector-film", "--ticket", "appeal-1.md"]) == 0
    assert "closed" in capsys.readouterr().out
    (a,) = read_appeals(setup)
    assert a.outcome == "applied"


def test_cli_refuses_plainly(setup, capsys):
    assert appeal_answer.main(["--project", "vector-film", "--ticket", "appeal-9.md"]) == 1
    assert "not an appeal" in capsys.readouterr().err


def test_cli_refuses_project_without_story_drive_folder(setup, capsys, tmp_path):
    (tmp_path / "projects" / "vector-film" / "project.yaml").write_text("name: x\n", encoding="utf-8")
    assert appeal_answer.main(["--project", "vector-film", "--ticket", "appeal-1.md"]) == 1
    assert "no Story-drive folder" in capsys.readouterr().err

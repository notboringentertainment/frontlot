"""S0 has a command: ``scripts/pipeline_migrate.py --project <slug> --to 1.6``.

The shot-ready plan's one mandatory setup step is a signed
``pipeline_migration`` receipt, and the library call that publishes its gate
request had no production caller. This command is that caller and nothing
else: it writes the request, prints the id and the signing command, and exits
non-zero with the coverage blockers when the project may not be migrated. It
signs nothing, and there is no non-TTY decision path in it.

Invented ids only.
"""

from __future__ import annotations

import json

import pytest

from scripts import pipeline_migrate
from tests.tools._authored_film_helpers import make_verified_project

SLUG = "proj-zephyr"


@pytest.fixture
def project(tmp_path, monkeypatch):
    return make_verified_project(tmp_path, monkeypatch, SLUG)


def request_path(project):
    return project / ".gate-requests" / "pipeline-migration-1-6.json"


class TestPipelineMigrateCommand:
    def test_a_quiet_project_gets_a_pending_request_and_the_signing_command(self, project, capsys):
        assert pipeline_migrate.main(["--project", SLUG, "--to", "1.6"]) == 0
        out = capsys.readouterr().out
        assert "pipeline-migration-1-6" in out
        assert "scripts/gate_sign.py --project proj-zephyr --request pipeline-migration-1-6" in out

        request = json.loads(request_path(project).read_text())
        assert request["kind"] == "pipeline_migration"
        assert request["approval_record"]["version"] == "1.6"
        assert request["approval_record"]["pipeline_name"] == "authored-film"
        kinds = [json.loads(line)["kind"] for line in
                 (project / "approvals.jsonl").read_text().splitlines() if line.strip()]
        assert "pipeline_migration" not in kinds, "the command signed the migration itself"

    def test_a_mid_flight_project_is_refused_with_its_blockers_and_no_request(self, project, capsys):
        requests = project / ".gate-requests"
        requests.mkdir()
        (requests / "headshot-char-one-1.json").write_text(
            json.dumps({"request_id": "headshot-char-one-1", "kind": "headshot"})
        )
        assert pipeline_migrate.main(["--project", SLUG, "--to", "1.6"]) == 1
        err = capsys.readouterr().err
        assert "mid-flight" in err and "headshot-char-one-1.json is still pending" in err
        assert not request_path(project).exists()

    def test_an_unknown_project_or_version_is_a_refusal_not_a_traceback(self, project, capsys):
        assert pipeline_migrate.main(["--project", SLUG, "--to", "7.7"]) == 1
        assert "no manifest" in capsys.readouterr().err
        assert pipeline_migrate.main(["--project", "proj-nowhere", "--to", "1.6"]) == 1
        assert "no registered project directory" in capsys.readouterr().err

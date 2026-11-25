"""Explicit signed production fixture for offline provider contract tests."""
import hashlib
import pytest
import yaml


@pytest.fixture
def generation_tmp_path(tmp_path_factory):
    root=tmp_path_factory.mktemp('provider-test')/'invented-film'
    root.mkdir()
    return root


@pytest.fixture
def verified_generation_project(tmp_path,monkeypatch):
    """Keep payload tests inside a registered, genuinely signed old film.

    This is opt-in: tests of missing or invalid production attribution must
    continue to start without a valid production.
    """
    from lib import events,gates,paths,receipts
    from lib.canonical_json import record_sha256
    from tests.tools._authored_film_helpers import PROJECT_CONFIG
    monkeypatch.setattr(paths,'PROJECTS_DIR',tmp_path.parent)
    monkeypatch.setattr(events,'PROJECTS_DIR',tmp_path.parent)
    monkeypatch.chdir(tmp_path)
    raw=yaml.safe_dump(PROJECT_CONFIG,sort_keys=True).encode()
    (tmp_path/'project.yaml').write_bytes(raw)
    record={'config_sha256':hashlib.sha256(raw).hexdigest()}
    with gates.handler_context():
        token=gates.mint_gate_token(project_id=tmp_path.name,stage='proposal',scope='config',
            record_sha256=record_sha256(record),user_response='approve')
    receipts.record_human_approval(tmp_path,tmp_path.name,'proposal','config',record,token,'config',entity_id='project-config')
    return tmp_path

"""Ben's trailer-test decision changes requirements, never invents consent."""
import hashlib
import json
from pathlib import Path

import pytest
from lib import trailer_edit
from tests.lib.look_lock_helpers import approve_request
from tests.lib.test_trailer_edit import human_terminal


@pytest.fixture(autouse=True)
def no_service_contact(monkeypatch):
    calls=[]
    def refuse(*args,**kwargs):
        calls.append((args,kwargs));raise AssertionError('No service belongs in a contract operation.')
    monkeypatch.setattr('requests.sessions.Session.request',refuse)
    yield calls
    assert calls==[]


def test_trimmed_definition_names_bens_decision_and_only_real_reviews():
    from lib.pipeline_loader import load_pipeline_readonly
    manifest=load_pipeline_readonly('authored-film@1.7')
    assert manifest['validation_profile']=='trailer-test'
    assert 'trim for the trailer test' in manifest['description']
    assert [s['name'] for s in manifest['stages']]==['canon_ingest','script','edit','colour_mix','compose']
    assert all(s['human_approval_default'] is True for s in manifest['stages'])
    for label in ['three treatment options','complete cast','cast portraits','approved poster',
                  'lines and title cards','shot list','footage and sound']:
        assert label in manifest['description'].lower()
    assert 'every protected line' in manifest['description'].lower()


def test_the_signed_full_trailer_definition_is_not_rewritten():
    path=Path(__file__).resolve().parents[2]/'pipeline_defs/authored-film@1.6.yaml'
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='95f4a217a6c3971b4193737043ff08543455f7f95f89e1800b70cdafb8b0c0b0'


@pytest.fixture
def trimmed(tmp_path,monkeypatch):
    root=trailer_edit.create_trailer(tmp_path/'projects','Invented signed trailer test',12,10)
    monkeypatch.setenv('OPENMONTAGE_PROJECTS_DIR',str(root.parent))
    request=json.loads(next((root/'.gate-requests').glob('*.json')).read_text())
    approve_request(request,root)
    return root


def test_creation_signs_the_trimmed_decision_without_approving_a_stage(trimmed):
    from lib.trailer_contract import signed_trailer_pin
    from lib.receipts import verified_approvals
    assert signed_trailer_pin(trimmed).version=='1.7'
    record=verified_approvals(trimmed,'pipeline_migration')[-1]['record']
    assert record['trailer_test_contract']['decision']=='trim for the trailer test'
    assert not list(trimmed.glob('checkpoint_*.json'))


@pytest.mark.parametrize('stage',['canon_ingest','script','edit','colour_mix','compose'])
def test_bare_flag_cannot_finish_any_trimmed_stage(trimmed,stage):
    from lib.checkpoint import write_checkpoint,CheckpointValidationError
    with pytest.raises(CheckpointValidationError,match='signed approval'):
        write_checkpoint(trimmed.parent,trimmed.name,stage,'completed',{},human_approved=True)
    assert not list(trimmed.glob('checkpoint_*.json'))


def test_trimmed_creation_still_refuses_an_unsigned_project_field(tmp_path):
    from lib.pipeline_pin import prepare_migration_request,PipelinePinError
    root=tmp_path/'ordinary';root.mkdir()
    (root/'project.json').write_text(json.dumps({'project_id':'ordinary','production_kind':'trailer',
        'trailer':__import__('lib.trailer_contract',fromlist=['make_contract']).make_contract(12)}))
    with pytest.raises(PipelinePinError,match='signed trailer creation'):
        prepare_migration_request(root,root.name,'authored-film','1.7')

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
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
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
    with pytest.raises(CheckpointValidationError,match='signed approval') as refusal:
        write_checkpoint(trimmed.parent,trimmed.name,stage,'completed',{},human_approved=True)
    assert 'not available yet' not in str(refusal.value), 'The five remaining reviews have real signing controls.'
    assert not list(trimmed.glob('checkpoint_*.json'))


def test_trimmed_creation_still_refuses_an_unsigned_project_field(tmp_path):
    from lib.pipeline_pin import prepare_migration_request,PipelinePinError
    root=tmp_path/'ordinary';root.mkdir()
    (root/'project.json').write_text(json.dumps({'project_id':'ordinary','production_kind':'trailer',
        'trailer':__import__('lib.trailer_contract',fromlist=['make_contract']).make_contract(12)}))
    with pytest.raises(PipelinePinError,match='signed trailer creation'):
        prepare_migration_request(root,root.name,'authored-film','1.7')


def checked_paper(root):
    from lib import story_ingest
    result=story_ingest.supply(root,b'A lamp burns. Keep the light on.',protected=['Keep the light on.'])
    approve_request(result['gate'],root)
    from tests.lib.test_trailer_edit import paper
    value=paper();value['shots'][0]['duration_frames']=288
    value['lines']=[{'text':'Keep the light on.','start_frame':0,'duration_frames':24,'source_ref':'supplied-story'}]
    return trailer_edit.save_revision(root,value,None)


def test_signed_whole_paper_finishes_only_its_own_trimmed_stage(trimmed):
    from lib import trailer_paper_review as review
    from lib.checkpoint import get_completed_stages,write_checkpoint,CheckpointValidationError
    saved=checked_paper(trimmed)
    assert get_completed_stages(trimmed.parent,trimmed.name,'authored-film@1.7')==['canon_ingest']
    gate=review.request_review(trimmed,saved['edit_revision_sha256']);approve_request(gate,trimmed)
    assert review.complete(trimmed)=='Your signed paper-cut review is recorded.'
    assert get_completed_stages(trimmed.parent,trimmed.name,'authored-film@1.7')==['canon_ingest','script']
    for stage in ('proposal','look_lock','headshots','visual_bible','scene_plan','assets'):
        assert not (trimmed/f'checkpoint_{stage}.json').exists()
    cp=json.loads((trimmed/'checkpoint_script.json').read_text())
    assert set(cp['artifacts'])=={'script','scene_plan'}
    changed=json.loads(json.dumps(saved['paper_cut']));changed['beats'][0]['text']='A different opening.'
    trailer_edit.save_revision(trimmed,changed,saved['edit_revision_sha256'])
    assert review.state(trimmed)=='Needs a fresh look'
    with pytest.raises(ValueError,match='fresh|signature'):review.complete(trimmed)


def test_a_signed_no_is_not_a_paper_approval(trimmed):
    from lib import trailer_paper_review as review,gates,receipts
    from lib.canonical_json import record_sha256
    checked_paper(trimmed);record=review.material(trimmed);digest=record_sha256(record)
    with gates.handler_context():
        token=gates.mint_gate_token(trimmed.name,'script','trailer-paper',digest,user_response='no')
    receipts.record_human_approval(trimmed,trimmed.name,'script','trailer-paper',record,token,'artifact_review',entity_id='paper-cut',artifact={
        'artifact_type':'trailer_paper_cut','artifact_version':'1.0','artifact_digest':digest,'migration_status':'reviewed'})
    assert review.state(trimmed)=='Needs a fresh look'
    with pytest.raises(ValueError,match='fresh review'):review.complete(trimmed)


def test_outstanding_reviews_check_only_predecessors_and_keep_unsigned_paper_outstanding(trimmed,monkeypatch):
    from lib import checkpoint,trailer_contract
    checked_paper(trimmed)
    reads=[];original=checkpoint.read_checkpoint
    def counted(*args,**kwargs):reads.append(args[2]);return original(*args,**kwargs)
    monkeypatch.setattr(checkpoint,'read_checkpoint',counted)
    assert checkpoint.get_completed_stages(trimmed.parent,trimmed.name,'authored-film@1.7',before_stage='script')==['canon_ingest']
    assert reads==['canon_ingest']
    before=trailer_contract.outstanding_stage_reviews(trimmed,'compose')
    assert any(line.startswith('Final story comparison:') for line in before)
    after=trailer_contract.outstanding_stage_reviews(trimmed,'compose',final_story_signed=True)
    assert not any(line.startswith('Final story comparison:') for line in after)
    assert any(line.startswith('Paper cut:') for line in after), 'A signed file never substitutes for the paper review.'


def test_final_story_answers_cannot_invent_a_missing_protected_voice(trimmed):
    from lib.trailer_final_story import questions,validate
    from tests.lib.test_trimmed_trailer_delivery import final_answers
    checked_paper(trimmed)
    asked=questions(trimmed)
    assert asked['protected_lines']==['Keep the light on.']
    with pytest.raises(ValueError,match='final story comparison'):validate(trimmed,None)
    answer=final_answers(asked)
    answer['canon_pass']['protected_lines'][0]['audible']=False
    with pytest.raises(ValueError,match='heard verbatim'):validate(trimmed,answer)
    answer=final_answers(asked)
    with pytest.raises(ValueError,match='no kept spoken take.*Keep the light on'):validate(trimmed,answer)
    answer['story_sha256']='a'*64
    with pytest.raises(ValueError,match='story changed'):validate(trimmed,answer)


def test_repeated_review_reads_reuse_the_checked_definition_but_still_check_its_bytes(trimmed,monkeypatch,tmp_path):
    from lib import pipeline_loader,trailer_contract,trailer_records
    checked_paper(trimmed)
    pin=trailer_contract.signed_trailer_pin(trimmed)
    artifacts=trailer_records.paper_artifacts(trimmed)
    original=pipeline_loader.load_pipeline;loads=[]
    def counted(*args,**kwargs):loads.append(1);return original(*args,**kwargs)
    pipeline_loader._load_pipeline_cached.cache_clear()
    monkeypatch.setattr(pipeline_loader,'load_pipeline',counted)
    records=[trailer_contract.approval_record(trimmed,pin,'script',artifacts) for _ in range(5)]
    assert all(record==records[0] for record in records)
    assert len(loads)<=1, 'Polling a saved review must not reparse and revalidate the immutable definition each time.'
    replacement=tmp_path/'changed-definition.yaml';replacement.write_bytes((Path(__file__).resolve().parents[2]/'pipeline_defs/authored-film@1.7.yaml').read_bytes()+b'\n# changed bytes\n')
    original_path=pipeline_loader.manifest_path
    monkeypatch.setattr(pipeline_loader,'manifest_path',lambda ref,defs_dir=None:replacement if ref=='authored-film@1.7' else original_path(ref,defs_dir))
    from lib.pipeline_pin import PipelinePinError
    with pytest.raises(PipelinePinError,match='changed manifest needs a new human-approved migration receipt'):
        trailer_contract.signed_trailer_pin(trimmed)

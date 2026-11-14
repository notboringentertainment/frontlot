import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from lib.trailer_edit import create_trailer, save_revision, read_revision, paper_state
from tests.lib.look_lock_helpers import approve_request


@pytest.fixture(autouse=True)
def human_terminal(monkeypatch):
    from scripts import gate_approve
    monkeypatch.setattr(gate_approve,"require_tty",lambda:None)


def trailer(tmp_path):
    root = create_trailer(tmp_path, 'Night Window', 4, 25)
    req = json.loads(next((root/'.gate-requests').glob('*.json')).read_text())
    approve_request(req, root)
    return root


def paper():
    return {'beats': [{'kind': k, 'text': k} for k in ['hook','escalation','reversal','invitation']],
            'lines': [], 'cards': [], 'shots': [{'purpose':'Open a question', 'duration_frames':96,
                        'references':[], 'difficulty':'Simple'}]}


def test_creation_is_signed_without_spending_or_stage_approval(tmp_path):
    from lib.trailer_contract import signed_trailer_pin
    from backlot.state import load_board_state
    root = trailer(tmp_path)
    assert signed_trailer_pin(root).version == '1.6'
    s = load_board_state(root)
    assert s['is_trailer'] and not s.get('trailer_setup_pending')
    assert all(not x['human_approved'] and x['status'] != 'completed' for x in s['stages'])
    assert not list(root.glob('checkpoint*'))
    assert not (root/'generation_receipts.jsonl').exists()


@pytest.mark.parametrize('value', [0,-1,121,float('nan'), 'bad',1.01])
def test_bad_lengths(tmp_path,value):
    with pytest.raises(ValueError,match='whole frames'):
        create_trailer(tmp_path,'Night Window',value,25)
    assert not list(tmp_path.iterdir())


def test_timing_history_and_staleness(tmp_path):
    root=trailer(tmp_path)
    a=save_revision(root,paper(),None)
    p=paper(); p['shots'][0]['duration_frames']=72
    b=save_revision(root,p,a['edit_revision_sha256'])
    assert read_revision(root,a['edit_revision_sha256']) == a
    assert b['timing_message'] == 'The sequence is 24 frames short of the target.'
    (root/'checkpoint_script.json').write_text('{"changed":true}')
    assert paper_state(root)['needs_review']


def test_interruption_and_concurrent_saves(tmp_path,monkeypatch):
    import lib.trailer_edit as edit
    root=trailer(tmp_path); a=save_revision(root,paper(),None)
    real=edit.os.replace
    def fail(*args): raise OSError('interrupted')
    monkeypatch.setattr(edit.os,'replace',fail)
    interrupted=paper();interrupted['beats'][0]['text']='A different hook'
    with pytest.raises(OSError): save_revision(root,interrupted,a['edit_revision_sha256'])
    assert read_revision(root)==a
    monkeypatch.setattr(edit.os,'replace',real)
    p=paper();p['shots'][0]['purpose']='Changed'
    def save(_):
        try: return save_revision(root,p,a['edit_revision_sha256'])
        except ValueError: return None
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(save,range(2)))
    assert sum(x is not None for x in results)==1
    assert read_revision(root,a['edit_revision_sha256'])==a


def test_placeholder_never_completes(tmp_path):
    from lib.trailer_contract import validate_stage_contract,require_contract
    root=trailer(tmp_path); a=save_revision(root,paper(),None)
    assert a['timeline']['picture'][0]['kind']=='placeholder'
    assert a['cuts']==[]
    with pytest.raises(ValueError,match='unfinished'):
        validate_stage_contract(require_contract(root),'edit',{'edit_decisions':a})


def signed_story(root, canon=None):
    from lib.pipeline_pin import pinned_pipeline
    from lib.trailer_contract import approval_record
    from lib.canonical_json import record_sha256
    from lib import gates,receipts
    canon=canon or {'version':'1.0','project_title':'Night Window','format':'trailer','source_documents':[{'path':'invented.md','doc_type':'outline','authority':'writing_document','sha256':'a'*64}], 'tone':{'tone_words':['tense']},'open_questions':[], 'locks':[{'id':'ending','decision':'The keeper is the thief','spoiler':True,'source_path':'invented.md','source_type':'writer_statement'}],
           'structure':{'beats':[{'id':'opening','summary':'A light in the tower.'}]}, 'protected_lines':[{'line':'Keep the light on.','source_path':'invented.md'}]}
    artifacts={'canon_packet':canon}
    pin=pinned_pipeline(root,'authored-film')
    record=approval_record(root,pin,'canon_ingest',artifacts)
    digest=record_sha256(record)
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,'canon_ingest','trailer-stage',digest,user_response='approve')
    receipts.record_human_approval(root,root.name,'canon_ingest','trailer-stage',record,token,'artifact_review',artifact={
        'artifact_type':'trailer_stage','artifact_version':'1.0','artifact_digest':digest,'migration_status':'reviewed'})
    cp={'version':'1.0','project_id':root.name,'pipeline_type':'authored-film','pipeline':pin.to_dict(),
        'predecessors':[],'stage':'canon_ingest','status':'completed','timestamp':'2026-01-01T00:00:00Z',
        'checkpoint_policy':'guided','human_approved':True,'human_approval_required':True,'artifacts':artifacts}
    (root/'checkpoint_canon_ingest.json').write_text(json.dumps(cp))


@pytest.mark.parametrize('violation',['line','card'])
def test_protected_dialogue_and_spoiler_cards(tmp_path,violation):
    root=trailer(tmp_path);signed_story(root)
    p=paper();p['lines']=[{'text':'Keep the light on.','source_ref':'opening','start_frame':0,'duration_frames':24}]
    save_revision(root,p,None)
    previous=read_revision(root)
    if violation=='line': p['lines'][0]['text']='Turn the light on.'
    else: p['cards']=[{'text':'The keeper is the thief','source_ref':'ending','start_frame':24,'duration_frames':24}]
    with pytest.raises(ValueError,match='protected|reveals'):
        save_revision(root,p,previous['edit_revision_sha256'])
    assert read_revision(root)==previous


def test_creation_tampering_is_refused_at_signing(tmp_path):
    from scripts.gate_approve import construct,GateHandlerError
    root=create_trailer(tmp_path,'Night Window',4,25)
    req=json.loads(next((root/'.gate-requests').glob('*.json')).read_text())
    marker=json.loads((root/'project.json').read_text());marker['spending_ceiling_usd']=200
    (root/'project.json').write_text(json.dumps(marker))
    with pytest.raises(GateHandlerError,match='settings changed'): construct(root,req)


def test_upstream_target_change_keeps_history(tmp_path):
    from lib.checkpoint import init_project
    root=trailer(tmp_path);old=save_revision(root,paper(),None)
    init_project(root.name,title='Night Window',pipeline_type='authored-film@1.6',pipeline_dir=root.parent,target_duration_seconds=5,review_target_change=True)
    assert paper_state(root)['needs_review']
    assert read_revision(root,old['edit_revision_sha256'])==old


def test_placeholder_has_no_generation_or_checkpoint_side_effects(tmp_path):
    from lib.receipts import verified_generation_receipts
    root=trailer(tmp_path)
    before=list(root.glob('checkpoint*'))
    save_revision(root,paper(),None)
    assert list(root.glob('checkpoint*'))==before
    assert verified_generation_receipts(root)==[]

"""Invented regressions for the reviewed paper-cut failures."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib import trailer_edit as edit
from lib.trailer_contract import edit_digest, require_contract
from tests.lib.test_trailer_edit import trailer, paper, signed_story
from tests.lib.look_lock_helpers import approve_request


@pytest.fixture(autouse=True)
def human(monkeypatch):
    monkeypatch.setattr('scripts.gate_approve.require_tty', lambda: None)


def row(text, start=0, duration=24, ref='opening'):
    return {'text':text,'start_frame':start,'duration_frames':duration,'source_ref':ref}


def approved(tmp_path):
    root=trailer(tmp_path)
    signed_story(root)
    return root


def test_creation_failure_is_invisible_and_recoverable(tmp_path,monkeypatch):
    from lib import pipeline_pin
    original=pipeline_pin.prepare_migration_request
    monkeypatch.setattr(pipeline_pin,'prepare_migration_request',lambda *a,**k: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(ValueError,match='could not be created'):
        edit.create_trailer(tmp_path,'Night Watch',28,50)
    assert not [p for p in tmp_path.iterdir() if p.is_dir() and not p.name.startswith('.')]
    assert not list(tmp_path.glob('.trailer-staging/*'))
    monkeypatch.setattr(pipeline_pin,'prepare_migration_request',original)
    assert edit.create_trailer(tmp_path,'Night Watch',28,50).is_dir()


def test_simultaneous_creation_and_existing_name(tmp_path,monkeypatch):
    import threading,time
    from lib import pipeline_pin
    original=pipeline_pin.prepare_migration_request
    entered=threading.Event()
    def slow(*a,**k):
        entered.set();time.sleep(.15);return original(*a,**k)
    monkeypatch.setattr(pipeline_pin,'prepare_migration_request',slow)
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(edit.create_trailer,tmp_path,'Night Watch',28,50)
        assert entered.wait(2)
        second=pool.submit(edit.create_trailer,tmp_path,'Night Watch',28,50)
        a,b=first.result(),second.result()
    assert a==b
    with pytest.raises(ValueError,match='name is already in use'):
        edit.create_trailer(tmp_path,'night watch',28,50)


@pytest.mark.parametrize('title,ceiling',[('Night\nWatch',50),('Night\x1bWatch',50),('Night Watch',1e308),('Night Watch',50.123456)])
def test_creation_refuses_misleading_inputs(tmp_path,title,ceiling):
    with pytest.raises(ValueError):edit.create_trailer(tmp_path,title,28,ceiling)


def test_half_made_creation_has_repair_action(tmp_path):
    from lib.checkpoint import init_project
    from backlot.state import load_board_state
    root=init_project('old-draft',title='Night Watch',pipeline_type='authored-film@1.6',pipeline_dir=tmp_path,target_duration_seconds=28)
    (root/'project.yaml').write_text('pipeline: authored-film\nbudget_usd_cap: 50\n')
    marker=json.loads((root/'project.json').read_text());marker.update(spending_ceiling_usd=50,trailer_creation_pending=True)
    (root/'project.json').write_text(json.dumps(marker))
    state=load_board_state(root)
    assert state['trailer_creation']['needs_repair']
    assert 'missing' in state['trailer_creation']['message']
    edit.repair_creation(root)
    assert not load_board_state(root)['trailer_creation']['needs_repair']
    assert next((root/'.gate-requests').glob('*.json')).is_file()


@pytest.mark.parametrize('text',['The sea is rising.','THIS WINTER',''])
def test_honest_excerpts_can_omit_protected_lines(tmp_path,text):
    root=approved(tmp_path);p=paper()
    if text:p['lines']=[row(text)]
    assert edit.save_revision(root,p,None)


def test_altered_line_is_rejected_even_with_exact_copy_elsewhere(tmp_path):
    root=approved(tmp_path);p=paper()
    p['lines']=[row('Turn the light on.')]
    p['cards']=[row('Keep the light on.',24)]
    with pytest.raises(ValueError,match='Line 1.*Keep the light on'):
        edit.save_revision(root,p,None)


@pytest.mark.parametrize('kind',['beat','shot'])
def test_planning_spoilers_are_visible_warnings(tmp_path,kind):
    root=approved(tmp_path);p=paper();p['lines']=[row('Keep the light on.')]
    if kind=='beat':p['beats'][2]['text']='The keeper is the thief'
    else:p['shots'][0]['purpose']='The keeper is the thief';p['shots'][0]['references']=['ending']
    result=edit.save_revision(root,p,None)
    assert result['story_check']['warnings']
    assert 'private plan' in result['story_check']['warnings'][0]


def test_shared_writer_keeps_rough_cut_and_paper_does_not_replace_timing(tmp_path):
    from lib.state_io import atomic_write_json
    root=trailer(tmp_path)
    rough={'version':'1.0','trailer':require_contract(root),'render_runtime':'remotion',
           'cuts':[{'id':'take','source':'assets/video/invented.mp4','in_seconds':0,'out_seconds':4}]}
    rough['edit_revision_sha256']=edit_digest(rough)
    (root/'artifacts'/'edit_decisions.json').write_text(json.dumps(rough))
    result=edit.save_revision(root,paper(),rough['edit_revision_sha256'])
    assert edit.read_revision(root,rough['edit_revision_sha256'])['cuts']==rough['cuts']
    assert result['cuts']==rough['cuts'] and result['paper_cut_pending']
    first=edit.paper_state(root)['history'][0]
    assert first['saved_at'] and first['time_source']=='file'
    assert not first['saved_at'].startswith('Saved')
    next_edit=copy.deepcopy(result);next_edit['cuts'][0]['out_seconds']=3
    atomic_write_json(root/'artifacts'/'edit_decisions.json',next_edit)
    assert edit.read_revision(root,result['edit_revision_sha256'])['cuts']==rough['cuts']
    assert edit.paper_state(root)['needs_review']


def test_versions_order_time_restore_and_interrupted_save(tmp_path,monkeypatch):
    root=trailer(tmp_path);a=edit.save_revision(root,paper(),None)
    p=paper();p['beats'][0]['text']='A new hook'
    b=edit.save_revision(root,p,a['edit_revision_sha256'])
    history=edit.paper_state(root)['history']
    assert [x['id'] for x in history]==[a['edit_revision_sha256'],b['edit_revision_sha256']]
    assert all(x['saved_at'] for x in history)
    restored=edit.restore_revision(root,a['edit_revision_sha256'],b['edit_revision_sha256'])
    assert restored['paper_cut']==a['paper_cut'] and restored['previous_revision']==b['edit_revision_sha256']
    old=edit.paper_state(root)['history']
    def fail(*a):raise OSError('interrupted')
    monkeypatch.setattr(edit.os,'replace',fail)
    p['beats'][1]['text']='Another change'
    with pytest.raises(OSError):edit.save_revision(root,p,restored['edit_revision_sha256'])
    assert edit.paper_state(root)['history']==old
    assert not list((root/'artifacts').glob('*.tmp'))
    assert not list((root/'artifacts').glob('.*.tmp'))


def test_draft_without_story_then_full_check_when_story_is_approved(tmp_path):
    root=trailer(tmp_path);p=paper();p['lines']=[row('Turn the light on.')]
    saved=edit.save_revision(root,p,None)
    assert saved['story_check']['status']=='unchecked'
    signed_story(root)
    state=edit.paper_state(root)
    assert state['story_check']['status']=='failed'
    assert state['story_check']['errors'] and not state['usable_downstream']
    from lib.trailer_contract import approval_record
    from lib.pipeline_pin import pinned_pipeline
    with pytest.raises(ValueError,match='fresh look|checked'):
        approval_record(root,pinned_pipeline(root,'authored-film'),'assets',{})


def test_status_changes_do_not_create_script_review_cycle(tmp_path):
    root=approved(tmp_path)
    checkpoint={'artifacts':{'script':{'sections':[{'text':'A line'}]}},'status':'awaiting_human'}
    (root/'checkpoint_script.json').write_text(json.dumps(checkpoint))
    p=paper();p['lines']=[row('Keep the light on.')]
    saved=edit.save_revision(root,p,None)
    checkpoint.update(status='completed',timestamp='later',human_approved=True)
    (root/'checkpoint_script.json').write_text(json.dumps(checkpoint))
    assert not edit.paper_state(root)['needs_review']
    assert edit.save_revision(root,p,saved['edit_revision_sha256'])['edit_revision_sha256']==saved['edit_revision_sha256']


def test_upstream_change_during_save_does_not_record_a_version(tmp_path,monkeypatch):
    root=trailer(tmp_path);a=edit.save_revision(root,paper(),None)
    old=edit.paper_state(root)['history'];real=edit._inputs;calls=[]
    def change(path):
        calls.append(1)
        value=real(path)
        if len(calls)>1:value['changed']='yes'
        return value
    monkeypatch.setattr(edit,'_inputs',change)
    p=paper();p['beats'][0]['text']='A changed hook'
    with pytest.raises(ValueError,match='changed while saving'):
        edit.save_revision(root,p,a['edit_revision_sha256'])
    assert edit.paper_state(root)['history']==old


def test_malformed_edit_does_not_break_board(tmp_path):
    from backlot.state import load_board_state
    root=trailer(tmp_path)
    (root/'artifacts'/'edit_decisions.json').write_text('[]')
    assert load_board_state(root)['paper_cut']['error']


def test_adapter_requires_the_callers_base_and_tracks_absent_inputs(tmp_path):
    from backlot.claude_ops import prepare
    root=trailer(tmp_path);a=edit.save_revision(root,paper(),None)
    work=root/'frontlot-work';work.mkdir();(work/'paper.json').write_text(json.dumps(paper()))
    p=paper();p['beats'][0]['text']='A newer hook';edit.save_revision(root,p,a['edit_revision_sha256'])
    prepared=prepare('trailer_paper_save',{'paper':'paper.json','expected_revision':a['edit_revision_sha256']},
                     repo=Path.cwd(),film_slug=root.name,film_root=root,snapshot_dir=tmp_path/'snap')
    assert prepared.argv[prepared.argv.index('--expected-revision')+1]==a['edit_revision_sha256']
    assert all(root/f'checkpoint_{s}.json' in prepared.inputs for s in ['canon_ingest','proposal','script'])


def test_signer_refuses_damaged_creation_facts_plainly(tmp_path):
    from scripts.gate_approve import construct,GateHandlerError
    root=edit.create_trailer(tmp_path,'Night Watch',28,50)
    request=json.loads(next((root/'.gate-requests').glob('*.json')).read_text())
    marker=json.loads((root/'project.json').read_text());marker.pop('title')
    (root/'project.json').write_text(json.dumps(marker))
    with pytest.raises(GateHandlerError):construct(root,request)


def collection_paper():
    return {'beats':[
        {'kind':'hook','text':'A deserted gallery holds an impossible announcement.'},
        {'kind':'escalation','text':'The caretaker hears tomorrow’s evacuation announcement.'},
        {'kind':'reversal','text':'The closed museum seems ready to open tonight.'},
        {'kind':'invitation','text':'Invite the audience to enter the collection.'}],
        'lines':[row('Please leave before we open.',168,72,'opening')],
        'cards':[row('THE MUSEUM CLOSED YEARS AGO.',120,48,''),row('TONIGHT, IT OPENS.',384,72,''),row('THE LAST COLLECTION',600,72,'')],
        'shots':[{'purpose':purpose,'duration_frames':seconds*24,'references':refs,'difficulty':difficulty}
                 for purpose,seconds,refs,difficulty in [
                    ('A deserted gallery establishes the world',5,['Gallery sketch'],'Simple'),
                    ('The caretaker looks towards a dead loudspeaker',6,['Caretaker sketch','Gallery sketch'],'Moderate'),
                    ('The caretaker crosses a dark doorway under the announcement',5,['Caretaker sketch','Door sketch'],'Moderate'),
                    ('An empty display case catches a moving shadow',7,['Display case sketch'],'Difficult'),
                    ('The caretaker turns towards a light behind her; a lock turns',5,['Caretaker sketch','Gallery sketch'],'Moderate')]]}


def collection_canon():
    return {'version':'1.0','project_title':'The Last Collection','format':'trailer',
        'source_documents':[{'path':'invented.md','doc_type':'outline','authority':'writing_document','sha256':'a'*64}],
        'tone':{'tone_words':['uneasy']},'open_questions':[],
        'locks':[{'id':'ending','decision':'The caretaker has been dead since the museum closed.',
                  'spoiler':True,'source_path':'invented.md','source_type':'writer_statement'}],
        'structure':{'beats':[{'id':'opening','summary':'An empty museum after closing.'}]},
        'protected_lines':[{'line':'Please leave before we open.','source_path':'invented.md'},
                           {'line':'Nobody leaves the collection.','source_path':'invented.md'}]}


def test_twenty_eight_second_example_through_panel_routes(tmp_path,monkeypatch):
    from backlot import server
    from lib.trailer_contract import signed_trailer_pin,validate_stage_contract
    monkeypatch.setattr(server,'PROJECTS_DIR',tmp_path)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',str(tmp_path.resolve()))
    client=TestClient(server.create_app(port=4783,capability_token='invented-proof'))
    headers={'origin':'http://localhost:4783','x-frontlot-capability':'invented-proof'}
    body={'title':'The Last Collection','seconds':28,'ceiling':50,'request_key':'same-invented-click'}
    r=client.post('/api/trailers',headers=headers,json=body);assert r.status_code==200
    slug=r.json()['project_id'];root=tmp_path/slug
    assert client.post('/api/trailers',headers=headers,json=body).json()==r.json()
    req=json.loads(next((root/'.gate-requests').glob('*.json')).read_text());approve_request(req,root)
    assert signed_trailer_pin(root)
    url=f'/api/project/{slug}/paper-cut';p=collection_paper()
    a=client.post(url,headers=headers,json={'paper':p,'expected_revision':None})
    assert a.status_code==200,a.text
    a=a.json();assert a['story_check']['status']=='unchecked'
    assert a['timing_message']=='The sequence matches the target.'
    assert sum(s['duration_frames'] for s in a['timeline']['picture'])==672
    assert len(a['paper_cut']['shots'])==5 and len(a['paper_cut']['cards'])==3 and len(a['paper_cut']['lines'])==1
    assert client.get(f'{url}/{a["edit_revision_sha256"]}').json()==a
    assert not client.get(url).json()['usable_downstream']
    signed_story(root,collection_canon())
    assert client.get(url).json()['story_check']['status']=='checked'
    b=client.post(url,headers=headers,json={'paper':a['paper_cut'],'expected_revision':a['edit_revision_sha256']})
    assert b.status_code==200,b.text
    b=b.json()
    assert client.get(url).json()['usable_downstream']
    restored=client.post(url+'/restore',headers=headers,json={'revision':a['edit_revision_sha256'],'expected_revision':b['edit_revision_sha256']})
    assert restored.status_code==200,restored.text
    assert len(client.get(url).json()['history'])==3
    assert not list(root.glob('generation*'))
    assert all(s['status']!='completed' for s in client.get(f'/api/project/{slug}/state').json()['stages'] if s['name']!='canon_ingest')
    with pytest.raises(ValueError,match='unfinished'):validate_stage_contract(require_contract(root),'edit',{'edit_decisions':restored.json()})
    malformed=client.post('/api/trailers',headers=headers,content='{broken')
    assert malformed.status_code==400 and 'could not be read' in malformed.json()['detail']


@pytest.mark.parametrize('text,rejected',[
    ('Please leave after we close.',True), # Similarity .778: just above .75.
    ('Please leave until we close.',False), # Similarity .704: below .75.
    ('Please leave before we open!',True),
    ('Please leave before we open.',False),
    ('THIS WINTER',False),
])
def test_protected_matching_boundary_and_honest_words(tmp_path,text,rejected):
    root=trailer(tmp_path);signed_story(root,collection_canon())
    p=paper();p['lines']=[row(text)]
    if rejected:
        with pytest.raises(ValueError,match='Line 1.*protected wording'):edit.save_revision(root,p,None)
    else:assert edit.save_revision(root,p,None)


@pytest.mark.parametrize('text,rejected',[
    ('SHE DIED THE NIGHT IT CLOSED.',True),
    ('The caretaker has been dead since the museum closed.',True),
    ('THE MUSEUM CLOSED YEARS AGO.',False),
    ('THIS WINTER',False),
])
def test_spoiler_near_paraphrase_and_honest_cards(tmp_path,text,rejected):
    root=trailer(tmp_path);signed_story(root,collection_canon())
    p=paper();p['cards']=[row(text,ref='')]
    if rejected:
        with pytest.raises(ValueError,match='Card 1 reveals'):edit.save_revision(root,p,None)
    else:assert edit.save_revision(root,p,None)


def test_dead_writer_temp_is_cleaned_and_uncommitted_archive_is_hidden(tmp_path):
    root=trailer(tmp_path);a=edit.save_revision(root,paper(),None)
    temp=root/'artifacts'/'.paper-abandoned.tmp';temp.write_text('incomplete')
    history=root/'artifacts'/'edit-history';history.mkdir()
    (history/('f'*64+'.json')).write_text('{}')
    assert len(edit.paper_state(root)['history'])==1
    assert not temp.exists()
    with pytest.raises(ValueError):edit.read_revision(root,'f'*64)
    assert edit.read_revision(root)['edit_revision_sha256']==a['edit_revision_sha256']


def test_stale_downstream_and_unchecked_draft_are_refused(tmp_path):
    from lib.trailer_contract import approval_record
    from lib.pipeline_pin import pinned_pipeline
    root=trailer(tmp_path);a=edit.save_revision(root,paper(),None)
    with pytest.raises(ValueError,match='checked'):
        approval_record(root,pinned_pipeline(root,'authored-film'),'assets',{})
    signed_story(root)
    a=edit.save_revision(root,a['paper_cut'],a['edit_revision_sha256'])
    (root/'checkpoint_proposal.json').write_text('{"artifacts":{"new":"treatment"}}')
    assert edit.paper_state(root)['needs_review']
    with pytest.raises(ValueError,match='fresh look'):
        approval_record(root,pinned_pipeline(root,'authored-film'),'assets',{})


def test_creation_request_failure_while_writing_settings(tmp_path,monkeypatch):
    original=Path.write_text
    def fail(path,*args,**kwargs):
        if path.name=='project.yaml':raise OSError('disk full')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail)
    with pytest.raises(ValueError,match='Nothing was added'):edit.create_trailer(tmp_path,'Night Watch',28,50)
    assert not [p for p in tmp_path.iterdir() if p.is_dir() and not p.name.startswith('.')]
    assert not list(tmp_path.glob('.trailer-staging/*'))


def test_signer_refuses_damaged_facts_at_commit_and_uses_plain_words(tmp_path):
    import io
    from scripts.gate_approve import construct,GateHandlerError,show_request
    root=edit.create_trailer(tmp_path,'Night Watch',28,50)
    req=json.loads(next((root/'.gate-requests').glob('*.json')).read_text())
    built=construct(root,req);out=io.StringIO();show_request(req,root,out,built)
    assert 'Trailer title: Night Watch' in out.getvalue()
    assert all(word not in out.getvalue() for word in ['sha256','authored-film','entity_id','checkpoint','scope:'])
    marker=json.loads((root/'project.json').read_text());marker['spending_ceiling_usd']='bad'
    (root/'project.json').write_text(json.dumps(marker))
    with pytest.raises(GateHandlerError):built.pre_commit_check()


def test_full_script_still_requires_protected_lines():
    from lib.canon_enforcement import _check_script
    script={'sections':[{'text':'An honest unrelated line.','source_ref':'opening'}],
            'metadata':{'canon_check':{'locks_honored':['ending'],'tensions':[]}}}
    with pytest.raises(ValueError,match='does not appear verbatim'):
        _check_script(script,collection_canon(),{'opening','ending'},'completed')

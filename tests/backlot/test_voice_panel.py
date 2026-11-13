import json
from pathlib import Path
import pytest
from tests.tools.test_audio_governance import project, sign
from tests.tools.test_audio_half_transport import priced, http, speech_request

@pytest.fixture
def voice_world(priced,http,monkeypatch):
    from lib.checkpoint import init_project
    from lib.pipeline_pin import migration_record
    from lib.pipeline_loader import manifest_digest
    from lib.trailer_edit import save_revision
    from tests.lib.test_trailer_edit import signed_story,paper
    init_project(priced.name,title='Quiet Observatory',pipeline_type='authored-film@1.6',pipeline_dir=priced.parent,target_duration_seconds=28)
    sign(priced,'pipeline_migration',migration_record('authored-film','1.6',manifest_digest('authored-film@1.6')),'authored-film')
    signed_story(priced);draft=paper();draft['shots'][0]['duration_frames']=672
    draft['lines']=[{'text':'The gallery is closed.','start_frame':0,'duration_frames':72}]
    save_revision(priced,draft,None)
    gets=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'voices':[{'voice_id':'invented_voice','name':'Gallery narrator','category':'premade'}]}
    monkeypatch.setattr('requests.get',lambda *a,**k:(gets.append(1) or Response()))
    return priced,http,gets

def options():return {'line_index':0,'voice':'invented_voice','rights_confirmed':True,'notes':'Quiet, with a pause after gallery.'}

def test_voice_panel_uses_signed_route_and_real_take_decisions(voice_world):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world
    assert panel.catalogue(root)==[{'id':'invented_voice','name':'Gallery narrator'}]
    result=panel.prepare_voice(root,options());record=result['request']
    assert record['prompt']=='The gallery is closed.'
    assert record['binding']['performance_notes']==options()['notes']
    assert panel.send(root,record['request_id'])['success'] is False
    assert posts==[]
    sign(root,'audio_spend',record,record['request_id'])
    take=panel.send(root,record['request_id'])['take']
    assert len(posts)==1 and posts[0][1]['json']['text']==record['prompt']
    assert panel.send(root,record['request_id'])['take']['id']==take['id'] and len(posts)==1
    gate=panel.decide(root,take['id'],'accept')
    assert panel.snapshot(root)['takes'][0]['status']=='Waiting for your decision'
    sign(root,'audio_take',gate['approval_record'],take['id'])
    assert panel.snapshot(root)['takes'][0]['status']=='Accepted'
    assert panel.playback(root,take['id'])[0][:4]==b'RIFF'
    gate=panel.decide(root,take['id'],'decline');sign(root,'audio_take',gate['approval_record'],take['id'])
    with pytest.raises(ValueError,match='declined'):panel.decide(root,take['id'],'accept')
    assert len(posts)==1 and len(gets)==1

@pytest.mark.parametrize('case,reason',[('no-catalogue','Load account voices'),('unknown-voice','Choose a voice'),('no-rights','permission'),('bad-line','no longer'),('bad-notes','two thousand'),('extra-field','Replacement words'),('unchecked','checked')])
def test_voice_panel_refusals_make_no_submission(voice_world,case,reason):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;data=options()
    if case!='no-catalogue':panel.catalogue(root)
    if case=='unknown-voice':data['voice']='another_voice'
    if case=='no-rights':data['rights_confirmed']=False
    if case=='bad-line':data['line_index']=99
    if case=='bad-notes':data['notes']='x'*2001
    if case=='extra-field':data['prompt']='Unapproved replacement.'
    if case=='unchecked':
        path=root/'checkpoint_canon_ingest.json';value=json.loads(path.read_text());value['status']='in_progress';path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match=reason):panel.prepare_voice(root,data)
    assert posts==[]

def test_voice_panel_request_is_confined_and_notes_cannot_change_after_signing(voice_world):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;panel.catalogue(root)
    record=panel.prepare_voice(root,options())['request'];sign(root,'audio_spend',record,record['request_id'])
    path=root/'.audio-requests'/(record['request_id']+'.json');record['binding']['performance_notes']='Changed direction.';path.write_text(json.dumps(record))
    assert not panel.send(root,record['request_id'])['success'] and posts==[]
    with pytest.raises(ValueError):panel.playback(root,'../project.yaml')

def test_voice_panel_http_requires_writer_capability(voice_world,monkeypatch):
    from fastapi.testclient import TestClient
    import backlot.server as server
    root,posts,gets=voice_world
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with TestClient(server.create_app(port=4913,capability_token='invented-browser-proof')) as client:
        url=f'/api/project/{root.name}/voice/catalogue'
        assert client.post(url,json={}).status_code==403
        assert client.post(url,json={},headers={'origin':'http://127.0.0.1:4913'}).status_code==401
    assert posts==[] and gets==[]

def test_voice_price_settings_use_existing_config_signature(voice_world):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world
    data={'dollars_per_credit':.0002,'speech_credits_per_character':1,'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'expires_at':4102444800}
    req=panel.prepare_prices(root,data)
    from lib.project_config import load_current_verified_project_config
    with pytest.raises(RuntimeError):load_current_verified_project_config(root)
    sign(root,'config',req['approval_record'],'project_config')
    assert load_current_verified_project_config(root).data['audio_price_basis']['voice_multiplier_ceiling']['value']==2
    assert posts==[] and gets==[]

@pytest.mark.parametrize('field,value',[('dollars_per_credit',0),('voice_multiplier_ceiling',.5),('expires_at',0),('expires_at',1e308),('speech_credits_per_character',True)])
def test_bad_price_settings_do_not_change_production(voice_world,field,value):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;old=(root/'project.yaml').read_bytes()
    data={'dollars_per_credit':.0002,'speech_credits_per_character':1,'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'expires_at':4102444800};data[field]=value
    with pytest.raises(ValueError):panel.prepare_prices(root,data)
    assert (root/'project.yaml').read_bytes()==old and posts==[] and gets==[]

@pytest.mark.parametrize('charged',[False,True])
def test_unknown_charge_requires_signed_account_review(voice_world,monkeypatch,charged):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;panel.catalogue(root)
    record=panel.prepare_voice(root,options())['request'];sign(root,'audio_spend',record,record['request_id'])
    def timeout(*a,**k):posts.append(1);raise TimeoutError()
    monkeypatch.setattr('requests.post',timeout)
    assert not panel.send(root,record['request_id'])['success']
    item=panel.snapshot(root)['unresolved_requests'][0];identity=item['reservation']['reservation_id']
    with pytest.raises(ValueError):panel.apply_resolution(root,identity)
    req=panel.prepare_resolution(root,{'reservation_id':identity,'charged':charged,'cash':0,'credits':.0002 if charged else 0,'note':'I checked my account and its outcome.'})
    sign(root,'audio_resolution',req['approval_record'],identity)
    panel.apply_resolution(root,identity)
    assert not panel.snapshot(root)['unresolved_requests'] and len(posts)==1
    assert panel.snapshot(root)['budget']['audio_credits']==(.0002 if charged else 0)
    assert 'used' in panel.snapshot(root)['requests'][0]['error']

def test_fresh_trailer_can_sign_audio_prices_without_a_picture_judge(tmp_path,monkeypatch):
    from lib.trailer_edit import create_trailer
    from tests.lib.look_lock_helpers import approve_request
    from backlot import voice_panel as panel
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    root=create_trailer(tmp_path,'The Quiet Gallery',28,20)
    req=json.loads(next((root/'.gate-requests').glob('*.json')).read_text());approve_request(req,root)
    req=panel.prepare_prices(root,{'dollars_per_credit':.0002,'speech_credits_per_character':1,'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'expires_at':4102444800})
    approve_request(req,root)
    from lib.project_config import load_current_verified_project_config
    assert load_current_verified_project_config(root).qc is None

def test_price_setting_signer_refuses_changed_draft(voice_world):
    from backlot import voice_panel as panel
    from scripts.gate_approve import construct
    root,posts,gets=voice_world
    req=panel.prepare_prices(root,{'dollars_per_credit':.0002,'speech_credits_per_character':1,'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'expires_at':4102444800})
    path=root/'project.yaml';path.write_text(path.read_text()+'\n# changed after review\n')
    with pytest.raises(Exception,match='settings changed'):construct(root,req)
    assert posts==[]

def test_restore_prior_signed_prices_does_not_approve_a_new_draft(voice_world):
    from backlot import voice_panel as panel
    from lib.project_config import load_current_verified_project_config
    root,posts,gets=voice_world;old=(root/'project.yaml').read_bytes()
    panel.prepare_prices(root,{'dollars_per_credit':.0003,'speech_credits_per_character':1,'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'expires_at':4102444800})
    panel.restore_prices(root)
    assert (root/'project.yaml').read_bytes()==old
    assert load_current_verified_project_config(root) and posts==[]

def test_changed_line_needs_review_but_can_still_be_declined(voice_world):
    from backlot import voice_panel as panel
    from lib import trailer_edit,audio_workspace
    root,posts,gets=voice_world;panel.catalogue(root)
    record=panel.prepare_voice(root,options())['request'];sign(root,'audio_spend',record,record['request_id'])
    take=panel.send(root,record['request_id'])['take']
    req=panel.decide(root,take['id'],'accept');sign(root,'audio_take',req['approval_record'],take['id'])
    saved=trailer_edit.read_revision(root);draft=saved['paper_cut'];draft['lines'][0]['text']='A lantern glows.'
    trailer_edit.save_revision(root,draft,saved['edit_revision_sha256'])
    assert audio_workspace.take_state(root,take)=='Needs a fresh look'
    req=panel.decide(root,take['id'],'decline');sign(root,'audio_take',req['approval_record'],take['id'])
    assert audio_workspace.take_state(root,take)=='Declined' and len(posts)==1

def test_linked_settings_stop_catalogue_before_account_contact(voice_world):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world
    other=root.parent/'linked-settings';other.mkdir()
    (other/'project.yaml').symlink_to(root/'project.yaml')
    with pytest.raises(ValueError,match='links'):panel.catalogue(other)
    assert posts==[] and gets==[]

@pytest.mark.parametrize('case',['missing-key','service-failure','empty-list'])
def test_catalogue_failure_is_plain_and_never_submits_speech(voice_world,monkeypatch,case):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world
    if case=='missing-key':monkeypatch.delenv('ELEVENLABS_API_KEY')
    else:
        class Response:
            def raise_for_status(self):
                if case=='service-failure':raise RuntimeError('Never disclose this provider message')
            def json(self):return {'voices':[]}
        def failed(*a,**k):gets.append(1);return Response()
        monkeypatch.setattr('requests.get',failed)
    with pytest.raises(ValueError,match='Connect your ElevenLabs|account voices could not be loaded'):panel.catalogue(root)
    assert posts==[] and len(gets)==(0 if case=='missing-key' else 1)

def test_insufficient_ceiling_blocks_the_real_signer_before_contact(voice_world):
    from backlot import voice_panel as panel
    from scripts.gate_approve import construct,GateHandlerError
    import hashlib,yaml
    root,posts,gets=voice_world
    data=yaml.safe_load((root/'project.yaml').read_text());data['budget_usd_cap']=.0001;raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config');panel.catalogue(root)
    result=panel.prepare_voice(root,options())
    req=json.loads((root/'.gate-requests'/(result['gate_request_id']+'.json')).read_text())
    with pytest.raises(GateHandlerError,match='remaining production budget'):construct(root,req)
    assert 'Not enough remains' in panel.snapshot(root)['requests'][-1]['error'] and posts==[]

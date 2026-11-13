"""Effects use real signatures, local files and counted provider boundaries."""
import hashlib,json,time
import pytest,yaml
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http,wav_bytes

def effect_bytes():
    import io,wave
    b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*16000)
    return b.getvalue()

@pytest.fixture
def effect_world(voice_world,monkeypatch):
    root,posts,gets=voice_world
    settings=yaml.safe_load((root/'project.yaml').read_text())
    settings['audio_price_basis']['effect_credits_per_second']={'value':40,'set_at':time.time()-1,'expires_at':4102444800}
    for p in settings['provider_egress']:
        if p['provider']=='elevenlabs' and 'effect_prompt' not in p['content_classes']:p['content_classes'].append('effect_prompt')
    raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    import requests
    original=requests.post
    def post(url,**kw):
        response=original(url,**kw)
        if url.endswith('/sound-generation'):response.content=effect_bytes()
        return response
    monkeypatch.setattr(requests,'post',post)
    return root,posts,gets

def brief():return {'text':'A quiet metal latch clicks once.','seconds':2,'loop':False,'start_frame':24,'shot_index':0,'brief_id':None}

def prepare(root):
    from backlot import music_panel as cues
    saved=cues.save_effect(root,brief())
    return cues.prepare_cue(root,{'brief_id':saved['brief_id']},kind='effect')['request']

def test_effect_one_signature_one_call_exact_loop_timing_and_final_decline(effect_world):
    from backlot import music_panel as cues,voice_panel
    from lib import audio_workspace as audio
    from tools.audio.elevenlabs_sfx import ElevenLabsSFX
    from tools.tool_registry import ToolRegistry
    root,posts,gets=effect_world;r=prepare(root)
    assert r['kind']=='effect' and r['loop'] is False and r['max_credits_usd']==.016
    assert not cues.send(root,r['request_id'],kind='effect')['success'] and posts==[]
    sign(root,'audio_spend',r,r['request_id'])
    registry=ToolRegistry();registry.register(ElevenLabsSFX())
    made=registry.get('elevenlabs_sfx').execute({'project_dir':str(root),'audio_request_id':r['request_id']})
    assert made.success,made.error
    assert posts[0][0]=='https://api.elevenlabs.io/v1/sound-generation'
    assert posts[0][1]['json']=={'text':brief()['text'],'duration_seconds':2,'loop':False,'model_id':'eleven_text_to_sound_v3'}
    assert cues.send(root,r['request_id'],kind='effect')['success'] and len(posts)==1
    t=made.data['take'];assert t['approved_request_id']==r['request_id'] and voice_panel.playback(root,t['id'])[0]==effect_bytes()
    gate=cues.decide(root,t['id'],'accept',kind='effect');sign(root,'audio_take',gate['approval_record'],t['id'])
    assert audio.require_accepted(root,t['id'])['id']==t['id']
    snap=voice_panel.snapshot(root);assert snap['budget']['audio_credits']==.0002
    assert snap['takes'][0]['kind']=='effect' and snap['requests'][0]['record']['kind']=='effect'
    cues.save_effect(root,dict(brief(),brief_id=r['binding']['brief_id'],text='A sharper latch.'))
    assert voice_panel.snapshot(root)['takes'][0]['status']=='Needs a fresh look'
    gate=cues.decide(root,t['id'],'decline',kind='effect');sign(root,'audio_take',gate['approval_record'],t['id'])
    assert audio.take_state(root,t)=='Declined'
    with pytest.raises(ValueError,match='declined'):cues.decide(root,t['id'],'accept',kind='effect')
    assert len(posts)==1 and gets==[]

@pytest.mark.parametrize('case,message',[
    ('empty','description'),('short','half a second'),('long','thirty seconds'),('fraction','whole frames'),
    ('loop','loop'),('moment','moment'),('outside','fit'),('extra','effect controls'),
    ('unchecked','checked'),('missing-price','sound-effect'),('expired','expired'),('permission','permission'),('account','not connected')])
def test_effect_preparation_refusals_contact_nothing(effect_world,monkeypatch,case,message):
    from backlot import music_panel as cues
    root,posts,gets=effect_world;data=brief()
    if case=='empty':data['text']=' '
    if case=='short':data['seconds']=.25
    if case=='long':data['seconds']=31
    if case=='fraction':data['seconds']=1.01
    if case=='loop':data['loop']='yes'
    if case=='moment':data['shot_index']=99
    if case=='outside':data['start_frame']=671
    if case=='extra':data['provider']='other'
    if case=='account':monkeypatch.delenv('ELEVENLABS_API_KEY')
    if case=='unchecked':
        p=root/'checkpoint_canon_ingest.json';v=json.loads(p.read_text());v['status']='in_progress';p.write_text(json.dumps(v))
    if case in ('missing-price','expired','permission'):
        v=yaml.safe_load((root/'project.yaml').read_text())
        if case=='missing-price':v['audio_price_basis'].pop('effect_credits_per_second')
        elif case=='expired':v['audio_price_basis']['effect_credits_per_second']['expires_at']=time.time()-1
        else:
            for row in v['provider_egress']:row['content_classes']=[x for x in row['content_classes'] if x!='effect_prompt']
        raw=yaml.safe_dump(v).encode();(root/'project.yaml').write_bytes(raw);sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError,match=message):
        b=cues.save_effect(root,data);cues.prepare_cue(root,{'brief_id':b['brief_id']},kind='effect')
    assert posts==[] and gets==[]

@pytest.mark.parametrize('field,value',[('prompt','Changed words.'),('loop',True),('duration_seconds',3),('model','another_model'),('max_credits_usd',.02)])
def test_changed_approved_effect_refuses_before_contact(effect_world,field,value):
    from backlot import music_panel as cues
    from scripts.gate_approve import construct
    root,posts,gets=effect_world;r=prepare(root);sign(root,'audio_spend',r,r['request_id'])
    r[field]=value;(root/'.audio-requests'/(r['request_id']+'.json')).write_text(json.dumps(r))
    assert not cues.send(root,r['request_id'],kind='effect')['success']
    assert posts==[] and gets==[]

@pytest.mark.parametrize('mode',['timeout','broken-download','invalid-audio','overcharge'])
def test_effect_unknown_or_failed_result_stays_reserved_and_stops_other_audio(effect_world,monkeypatch,mode):
    from backlot import music_panel as cues
    from tools.cost_tracker import load_reservations
    root,posts,gets=effect_world;r=prepare(root);sign(root,'audio_spend',r,r['request_id'])
    class Response:
        status_code=200
        headers={'character-cost':'1000000' if mode=='overcharge' else '1','request-id':'invented-effect'}
        def raise_for_status(self):pass
        @property
        def content(self):
            if mode=='broken-download':raise OSError('interrupted')
            return b'not audio' if mode=='invalid-audio' else wav_bytes()
    def post(*a,**kw):
        posts.append(1)
        if mode=='timeout':raise TimeoutError()
        return Response()
    monkeypatch.setattr('requests.post',post)
    assert not cues.send(root,r['request_id'],kind='effect')['success']
    assert next(iter(load_reservations(root).values()))['state']=='pending_billing'
    other=prepare(root);sign(root,'audio_spend',other,other['request_id'])
    assert not cues.send(root,other['request_id'],kind='effect')['success']
    assert len(posts)==1 and gets==[]

def test_effect_import_uses_shared_chooser_licence_provenance_and_moment(effect_world,monkeypatch,tmp_path):
    from backlot import music_panel as cues,voice_panel
    from lib import audio_workspace as audio
    root,posts,gets=effect_world;source=tmp_path/'owned-effect.wav';source.write_bytes(effect_bytes())
    b=cues.save_effect(root,brief())
    monkeypatch.setattr(cues,'choose_file',lambda **kw:str(source))
    choice=cues.choose(root,kind='effect')
    data={'choice_id':choice['choice_id'],'source':'My latch recording','licence_note':'','brief_id':b['brief_id']}
    with pytest.raises(ValueError,match='licence note'):cues.import_selected(root,data,kind='effect')
    t=cues.import_selected(root,dict(data,licence_note='I own this recording.'),kind='effect')['take']
    assert t['kind']=='effect' and t['origin']=='imported' and t['binding']['brief_id']==b['brief_id']
    gate=cues.decide(root,t['id'],'accept',kind='effect');sign(root,'audio_take',gate['approval_record'],t['id'])
    assert audio.require_accepted(root,t['id'])['source']=='My latch recording'
    assert voice_panel.playback(root,t['id'])[0]==effect_bytes()
    assert posts==[] and gets==[]

@pytest.mark.parametrize('case',['link','parent-link','traversal','no-selection','other-kind','expired','generated'])
def test_effect_import_refuses_unsafe_or_generated_file(effect_world,monkeypatch,tmp_path,case):
    from backlot import music_panel as cues
    root,posts,gets=effect_world;source=tmp_path/'own.wav';source.write_bytes(effect_bytes());b=cues.save_effect(root,brief())
    if case=='generated':
        r=prepare(root);sign(root,'audio_spend',r,r['request_id']);assert cues.send(root,r['request_id'],kind='effect')['success']
    if case=='link':
        p=tmp_path/'linked.wav';p.symlink_to(source);source=p
    if case=='parent-link':
        p=tmp_path/'linked-folder';p.symlink_to(tmp_path,target_is_directory=True);source=p/'own.wav'
    if case=='traversal':source=tmp_path/'invented'/'..'/'own.wav'
    monkeypatch.setattr(cues,'choose_file',lambda **kw:str(source))
    with pytest.raises(ValueError,match='links|path tricks|Choose.*file again|generated'):
        choice=cues.choose(root,kind='effect')
        if case=='no-selection':choice['choice_id']='missing'
        if case=='other-kind':cues._choices[choice['choice_id']]['kind']='music'
        if case=='expired':cues._choices[choice['choice_id']]['expires_at']=0
        cues.import_selected(root,{'choice_id':choice['choice_id'],'source':'My own','licence_note':'I own it.','brief_id':b['brief_id']},kind='effect')
    assert len(posts)==(1 if case=='generated' else 0) and gets==[]

def test_effect_routes_need_writer_before_any_action(effect_world,monkeypatch):
    from fastapi.testclient import TestClient
    import backlot.server as server
    root,posts,gets=effect_world;monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with TestClient(server.create_app(port=4913,capability_token='invented-browser-proof')) as client:
        for route in ('brief','prepare','import/choose','import','invented/send','invented/decision'):
            url=f'/api/project/{root.name}/effects/{route}'
            assert client.post(url,json={}).status_code==403
            assert client.post(url,json={},headers={'origin':'http://127.0.0.1:4913'}).status_code==401
    assert posts==[] and gets==[]

def test_all_three_kinds_share_one_receipted_production_total(effect_world,monkeypatch):
    from backlot import voice_panel as voice,music_panel as cues
    from tests.backlot.test_music_panel import prepare as music_request
    root,posts,gets=effect_world
    import requests
    original=requests.post
    def post(url,**kw):
        response=original(url,**kw)
        if '/text-to-speech/' in url:
            data=bytearray(wav_bytes());data[-1]=1;response.content=bytes(data)
        return response
    monkeypatch.setattr(requests,'post',post)
    voice.catalogue(root)
    spoken=voice.prepare_voice(root,{'line_index':0,'voice':'invented_voice','notes':'','rights_confirmed':True})['request']
    for record,kind in ((spoken,'speech'),(music_request(root),'music'),(prepare(root),'effect')):
        sign(root,'audio_spend',record,record['request_id'])
        made=voice.send(root,record['request_id']) if kind=='speech' else cues.send(root,record['request_id'],kind=kind)
        assert made['success'],made
    snap=voice.snapshot(root)
    assert {t['kind'] for t in snap['takes']}=={'speech','music','effect'}
    assert snap['budget']['spent']==pytest.approx(.0006,abs=1e-12) and snap['budget']['audio_credits']==pytest.approx(.0006,abs=1e-12)
    assert snap['budget']['remaining']==.9994 and len(posts)==3 and len(gets)==1

@pytest.mark.parametrize('case',['moment','unchecked','account','budget','wrong-kind','price'])
def test_effect_stops_after_signature_when_authority_changes(effect_world,monkeypatch,case):
    from backlot import voice_panel as voice,music_panel as cues
    root,posts,gets=effect_world;r=prepare(root);sign(root,'audio_spend',r,r['request_id'])
    if case=='moment':
        from lib import trailer_edit
        old=trailer_edit.read_revision(root);paper=old['paper_cut'];paper['shots'][0]['purpose']='Another question'
        trailer_edit.save_revision(root,paper,old['edit_revision_sha256'])
    if case=='unchecked':
        path=root/'checkpoint_canon_ingest.json';v=json.loads(path.read_text());v['status']='in_progress';path.write_text(json.dumps(v))
    if case=='account':monkeypatch.delenv('ELEVENLABS_API_KEY')
    if case=='price':
        v=yaml.safe_load((root/'project.yaml').read_text());v['audio_price_basis']['effect_credits_per_second']['value']=41
        raw=yaml.safe_dump(v).encode();(root/'project.yaml').write_bytes(raw);sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    if case=='budget':
        from tools.audio._governed import _tracker
        from lib.project_config import load_current_verified_project_config
        from tools.cost_tracker import reserve_paid_call
        reserve_paid_call(_tracker(root,load_current_verified_project_config(root)),root,tool='invented_footage',endpoint='invented',normalized_inputs_hash='a'*64,reserved_usd=.999,output_hint={'kind':'video'})
    result=voice.send(root,r['request_id']) if case=='wrong-kind' else cues.send(root,r['request_id'],kind='effect')
    assert not result['success'] and posts==[] and gets==[]

def test_effect_tool_is_discovered_and_has_no_unapproved_route(effect_world):
    from tools.tool_registry import ToolRegistry
    from tools.audio.music_gen import MusicGen
    root,posts,gets=effect_world
    registry=ToolRegistry();registry.discover('tools.audio')
    tool=registry.get('elevenlabs_sfx');assert tool is not None
    assert tool.capability=='sound_effects' and tool.retry_policy.max_retries==0
    assert 'generate_sfx' not in MusicGen.capabilities
    for args in ({},{'project_dir':str(root),'prompt':'Never send this.','duration_seconds':2,'loop':False},
                 {'project_dir':str(root),'audio_request_id':'not-an-approval'}):
        result=tool.execute(args);assert not result.success
    assert posts==[] and gets==[]

def test_a_separate_effect_does_not_change_a_kept_take(effect_world):
    from backlot import music_panel as cues
    from lib import audio_workspace as audio
    root,posts,gets=effect_world;r=prepare(root);sign(root,'audio_spend',r,r['request_id'])
    t=cues.send(root,r['request_id'],kind='effect')['take']
    gate=cues.decide(root,t['id'],'accept',kind='effect');sign(root,'audio_take',gate['approval_record'],t['id'])
    separate=cues.save_effect(root,dict(brief(),text='A faint distant bell.',start_frame=120))
    assert separate['brief_id']!=r['binding']['brief_id'] and audio.require_accepted(root,t['id'])['id']==t['id']
    assert len(posts)==1 and gets==[]

def test_effect_rate_requires_the_existing_config_signature(effect_world):
    from backlot import voice_panel as voice,music_panel as cues
    from lib.project_config import load_current_verified_project_config
    from backlot.state import _render_config
    root,posts,gets=effect_world
    gate=voice.prepare_prices(root,dict(dollars_per_credit=.0002,speech_credits_per_character=1,music_credits_per_minute=1200,voice_multiplier_ceiling=2,effect_credits_per_second=40,expires_at=4102444800))
    assert _render_config(root,gate)['audio_price_summary']['effect_second_dollars']==.008
    with pytest.raises(RuntimeError):load_current_verified_project_config(root)
    sign(root,'config',gate['approval_record'],'project_config')
    assert prepare(root)['max_credits_usd']==.016
    assert posts==[] and gets==[]

def test_signed_effect_account_outcome_resolves_without_another_call(effect_world,monkeypatch):
    from backlot import voice_panel as voice,music_panel as cues
    root,posts,gets=effect_world;r=prepare(root);sign(root,'audio_spend',r,r['request_id'])
    def post(*a,**kw):posts.append(1);raise TimeoutError()
    monkeypatch.setattr('requests.post',post)
    assert not cues.send(root,r['request_id'],kind='effect')['success']
    snap=voice.snapshot(root);identity=snap['unresolved_requests'][0]['reservation']['reservation_id']
    assert snap['budget']['held']==.016
    with pytest.raises(ValueError,match='Sign one account outcome'):voice.apply_resolution(root,identity)
    gate=voice.prepare_resolution(root,dict(reservation_id=identity,charged=False,cash=0,credits=0,note='I checked my account: no charge.'))
    sign(root,'audio_resolution',gate['approval_record'],identity);voice.apply_resolution(root,identity)
    assert voice.snapshot(root)['budget']['held']==0 and not voice.snapshot(root)['unresolved_requests']
    assert not cues.send(root,r['request_id'],kind='effect')['success'] and len(posts)==1 and gets==[]

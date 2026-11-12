"""App-callable approved audio operations with counted HTTP responses."""
import hashlib,io,json,time,wave
from pathlib import Path
import pytest,yaml
from tests.tools.test_audio_governance import project,sign,no_real_network_contact
from lib import audio_workspace as ws

@pytest.fixture
def priced(project):
    data=yaml.safe_load((project/'project.yaml').read_text());data['version']='1.4'
    def rate(value):return {'value':value,'set_at':time.time()-60,'expires_at':4102444800}
    data['audio_price_basis']={k:rate(v) for k,v in [('dollars_per_credit',.0002),('speech_credits_per_character',1),('music_credits_per_minute',1200),('voice_multiplier_ceiling',2)]}
    raw=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(raw)
    sign(project,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    return project

def wav_bytes():
    b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\0\0'*8000)
    return b.getvalue()

@pytest.fixture
def http(monkeypatch):
    import requests
    calls=[]
    class Response:
        status_code=200
        headers={'character-cost':'1','request-id':'invented-job'}
        content=wav_bytes()
        def raise_for_status(self):pass
    def post(url,**kw):calls.append((url,kw));return Response()
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
    return calls

def music_request(root):
    brief=ws.save_music_brief(root,'A quiet bell with room for speech.',3)
    return ws.prepare_request(root,'music','music_v1',brief_id=brief),brief

@pytest.mark.parametrize('route',['execute','internal','registry'])
def test_approved_music_posts_once_and_records_a_take(priced,http,route):
    from tools.audio.music_gen import MusicGen
    from tools.tool_registry import ToolRegistry
    record,brief=music_request(priced)
    tool=MusicGen()
    if route=='registry':
        registry=ToolRegistry();registry.register(tool);tool=registry.get('music_gen')
    args={'project_dir':str(priced),'audio_request_id':record['request_id']}
    sign(priced,'audio_spend',record,record['request_id'])
    result=tool._generate(args,'not-an-account-choice') if route=='internal' else tool.execute(args)
    assert result.success,result.error
    assert len(http)==1 and http[0][1]['json']['force_instrumental'] is True
    take=result.data['take'];assert take['approved_request_id']==record['request_id'] and take['release_note']==ws.TEST_ONLY
    assert ws.take_state(priced,take)=='Waiting for your decision'
    accepted=ws.decision_record(priced,take['id'],'accept');sign(priced,'audio_take',accepted,take['id'])
    assert ws.require_accepted(priced,take['id'])['id']==take['id']
    again=tool.execute(args);assert again.success and len(http)==1
    ws.save_music_brief(priced,'A different cue.',3,brief)
    assert ws.take_state(priced,take)=='Needs a fresh look'
    with pytest.raises(ValueError,match='accepted'):ws.require_accepted(priced,take['id'])
    assert len(http)==1

@pytest.mark.parametrize('case',['unsigned','changed','expired','no-basis'])
def test_no_contact_for_unsigned_or_changed_material(priced,http,case):
    from tools.audio.music_gen import MusicGen
    record,brief=music_request(priced)
    if case!='unsigned':sign(priced,'audio_spend',record,record['request_id'])
    if case=='changed':ws.save_music_brief(priced,'Changed words.',3,brief)
    if case in ('expired','no-basis'):
        data=yaml.safe_load((priced/'project.yaml').read_text())
        if case=='no-basis':data.pop('audio_price_basis')
        else:data['audio_price_basis']['dollars_per_credit']['expires_at']=time.time()-1
        raw=yaml.safe_dump(data).encode();(priced/'project.yaml').write_bytes(raw)
        sign(priced,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    r=MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert not r.success and len(http)==0


def test_imported_licence_and_signed_decline(priced,tmp_path,http):
    source=tmp_path/'owned.wav';source.write_bytes(wav_bytes())
    take=ws.import_music(priced,source,'Invented local recording','I own this recording.')
    assert take['origin']=='imported' and take['licence_note']=='I own this recording.'
    record=ws.decision_record(priced,take['id'],'decline');sign(priced,'audio_take',record,take['id'])
    assert ws.take_state(priced,take)=='Declined'
    with pytest.raises(ValueError,match='accepted'):ws.require_accepted(priced,take['id'])
    assert http==[]


def speech_request(root):
    from lib.trailer_contract import edit_digest
    directory=root/'artifacts';directory.mkdir(exist_ok=True)
    edit={'paper_cut':{'lines':[{'text':'The gallery is closed.','start_frame':0,'duration_frames':72}]}}
    edit['edit_revision_sha256']=edit_digest(edit)
    (directory/'edit_decisions.json').write_text(json.dumps(edit))
    return ws.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented-voice')


def test_voice_selector_never_substitutes_and_line_changes_need_review(priced,http):
    from tools.audio.tts_selector import TTSSelector
    from lib.trailer_contract import edit_digest
    record=speech_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    args={'project_dir':str(priced),'audio_request_id':record['request_id'],'preferred_provider':'elevenlabs'}
    r=TTSSelector().execute(args);assert r.success,r.error
    assert len(http)==1 and http[0][0].endswith('/invented-voice')
    assert http[0][1]['json']=={'text':'The gallery is closed.','model_id':'eleven_multilingual_v2'}
    take=r.data['take'];decision=ws.decision_record(priced,take['id'],'accept');sign(priced,'audio_take',decision,take['id'])
    assert ws.require_accepted(priced,take['id'])
    path=priced/'artifacts/edit_decisions.json';edit=json.loads(path.read_text());edit['paper_cut']['lines'][0]['text']='Tomorrow it opens.'
    edit['edit_revision_sha256']=edit_digest(edit);path.write_text(json.dumps(edit))
    assert ws.take_state(priced,take)=='Needs a fresh look'
    assert len(http)==1

@pytest.mark.parametrize('field,value',[('prompt','Changed words.'),('voice','other'),('model','other'),('duration_seconds',4),('max_credits_usd',.9)])
def test_saved_request_change_refuses_every_post(priced,http,field,value):
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    record=speech_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    record[field]=value
    (priced/'.audio-requests'/(record['request_id']+'.json')).write_text(json.dumps(record))
    r=ElevenLabsTTS().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert not r.success and http==[]


def test_unknown_charge_blocks_voice_music_selector_and_retry(priced,http,monkeypatch):
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    from tools.audio.tts_selector import TTSSelector
    import requests
    voice=speech_request(priced);music,_=music_request(priced)
    for r in (voice,music):sign(priced,'audio_spend',r,r['request_id'])
    def timeout(*a,**k):http.append(('timeout',{}));raise TimeoutError('invented timeout')
    monkeypatch.setattr(requests,'post',timeout)
    args={'project_dir':str(priced),'audio_request_id':voice['request_id']}
    assert not ElevenLabsTTS().execute(args).success
    for tool,record in ((ElevenLabsTTS(),voice),(MusicGen(),music),(TTSSelector(),voice)):
        assert not tool.execute(dict(args,audio_request_id=record['request_id'])).success
    assert len(http)==1


def test_successful_charge_is_recorded_once(priced,http):
    from tools.audio.music_gen import MusicGen
    from tools.cost_tracker import CostTracker,load_reservations
    from lib.receipts import verified_generation_receipts
    r,_=music_request(priced);sign(priced,'audio_spend',r,r['request_id'])
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':r['request_id']})
    assert result.success and len(http)==1
    rows=verified_generation_receipts(priced);assert len(rows)==1 and rows[0]['cost_usd']==.0002 and rows[0]['credits_usd']==.0002 and rows[0]['cash_usd']==0
    assert list(load_reservations(priced).values())[0]['state']=='completed'


def test_top_level_checkpoint_pipeline_identifies_a_trailer(project):
    from backlot.state import _trailer_display_evidence
    (project/'checkpoint_assets.json').write_text('{"pipeline":"authored-film@1.6"}')
    assert _trailer_display_evidence(project,{}) is True


def test_tilde_output_is_the_path_checked_and_written(priced,http,monkeypatch):
    from tools.audio.music_gen import MusicGen
    monkeypatch.setenv('HOME',str(priced.parent))
    r,_=music_request(priced);sign(priced,'audio_spend',r,r['request_id'])
    relative='~/'+priced.name+'/assets/audio/'+r['request_id']+'.audio'
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':r['request_id'],'output_path':relative})
    assert result.success and len(http)==1
    assert (priced/'assets/audio'/(r['request_id']+'.audio')).is_file()


def test_import_path_trick_is_refused(priced,tmp_path,http):
    path=tmp_path/'owned.wav';path.write_bytes(wav_bytes())
    with pytest.raises(ValueError,match='path tricks'):
        ws.import_music(priced,str(tmp_path/'invented'/'..'/'owned.wav'),'Owned music','Owned by me')
    assert http==[]


def test_take_decision_signer_reconstructs_material(priced,tmp_path,http):
    from scripts.gate_approve import construct
    source=tmp_path/'owned.wav';source.write_bytes(wav_bytes())
    take=ws.import_music(priced,source,'Invented recording','Owned by me')
    req=ws.request_decision(priced,take['id'],'accept')
    built=construct(priced,req)
    assert built.record==ws.decision_record(priced,take['id'],'accept')
    built.pre_commit_check()
    source=priced/take['path'];source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):built.pre_commit_check()
    assert http==[]

@pytest.mark.parametrize('mode',['overcharge','missing-cost','bad-download','timeout'])
def test_uncertain_service_results_keep_budget_reserved(priced,http,monkeypatch,mode):
    import requests
    from tools.audio.music_gen import MusicGen
    from tools.cost_tracker import load_reservations
    record,_=music_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    class Response:
        status_code=200
        headers={'request-id':'invented-job'}
        if mode!='missing-cost':headers['character-cost']='100000' if mode=='overcharge' else '1'
        def raise_for_status(self):pass
        @property
        def content(self):raise OSError('invented interrupted download')
    def post(*args,**kwargs):
        http.append((args,kwargs))
        if mode=='timeout':raise TimeoutError('invented timeout')
        return Response()
    monkeypatch.setattr(requests,'post',post)
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert not result.success and len(http)==1
    assert list(load_reservations(priced).values())[0]['state']=='pending_billing'
    assert ws.state(priced)['takes']==[]

@pytest.mark.parametrize('charged,cash,credits',[(False,0,0),(True,.001,.002)])
def test_signed_resolution_is_exact_and_cannot_resubmit(priced,http,monkeypatch,charged,cash,credits):
    import requests
    from tools.audio.music_gen import MusicGen
    from tools.cost_tracker import load_reservations
    from scripts.gate_approve import construct
    r,_=music_request(priced);sign(priced,'audio_spend',r,r['request_id'])
    def post(*a,**k):http.append(('timeout',{}));raise TimeoutError('invented timeout')
    monkeypatch.setattr(requests,'post',post)
    args={'project_dir':str(priced),'audio_request_id':r['request_id']}
    assert not MusicGen().execute(args).success
    identity=next(iter(load_reservations(priced)))
    req=ws.request_resolution(priced,identity,charged,cash,credits,'Checked the invented account ledger.')
    built=construct(priced,req);built.pre_commit_check();sign(priced,'audio_resolution',built.record,identity)
    with pytest.raises(ValueError,match='signed'):
        ws.resolve_charge(priced,identity,charged,cash,credits,'Checked a different account record.')
    ws.resolve_charge(priced,identity,charged,cash,credits,'Checked the invented account ledger.')
    assert load_reservations(priced)[identity]['state']==('completed' if charged else 'failed')
    assert not MusicGen().execute(args).success and len(http)==1


def test_an_active_call_cannot_be_resolved(priced,http):
    with ws.call_lock(priced):
        with pytest.raises(ValueError,match='still running'):
            ws.request_resolution(priced,'invented',False,0,0,'Checked the account.')
    assert http==[]

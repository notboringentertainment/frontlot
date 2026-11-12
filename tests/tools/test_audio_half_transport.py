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

import hashlib,importlib,inspect,json
from pathlib import Path
import pytest
from lib import audio_workspace as ws
from tests.tools.test_audio_governance import project,sign,no_real_network_contact
from tests.tools.test_audio_half_transport import priced,http,music_request,speech_request

@pytest.mark.parametrize('route',['copy','relabel'])
def test_generated_music_cannot_become_imported(priced,http,tmp_path,route):
    from tools.audio.music_gen import MusicGen
    record,_=music_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert result.success,result.error
    take=result.data['take'];source=priced/take['path']
    if route=='copy':
        copy=tmp_path/'library.wav';copy.write_bytes(source.read_bytes())
        with pytest.raises(ValueError,match='generated.*test-only'):ws.import_music(priced,copy,'A library','Licensed for release.')
    else:
        take.update(origin='imported',licence_note='Licensed for release.')
        (priced/'.audio-workspace'/(take['id']+'.json')).write_text(json.dumps(take))
        with pytest.raises(ValueError,match='provenance cannot be changed'):ws.request_decision(priced,take['id'],'accept')
    assert len(http)==1

@pytest.mark.parametrize('phase',['prepare','sign','send'])
@pytest.mark.parametrize('kind',['speech','music'])
def test_unchecked_voice_cannot_be_prepared_signed_or_sent(priced,http,phase,kind):
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from scripts.gate_approve import construct
    record=speech_request(priced)
    if kind=='music':record,_=music_request(priced)
    if phase=='send':sign(priced,'audio_spend',record,record['request_id'])
    path=priced/'checkpoint_canon_ingest.json'
    if path.exists():
        data=json.loads(path.read_text());data['status']='in_progress';path.write_text(json.dumps(data))
    if phase=='prepare':
        with pytest.raises(ValueError,match='checked.*paper cut'):
            ws.prepare_request(priced,kind,record['model'],line_index=0,voice=record['voice'],brief_id=record['binding'].get('brief_id'))
    elif phase=='sign':
        with pytest.raises(ValueError,match='checked.*paper cut'):construct(priced,{'kind':'audio_spend','project_id':priced.name,'entity_id':record['request_id'],'stage':'assets','scope':'audio','request_id':record['request_id']})
    else:
        from tools.audio.music_gen import MusicGen
        result=(ElevenLabsTTS() if kind=='speech' else MusicGen()).execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
        assert not result.success and 'checked' in result.error
    assert http==[]


def test_receipt_identity_keeps_distinct_takes_with_identical_bytes(priced,http):
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    from tools.audio.music_gen import MusicGen
    voice=speech_request(priced)
    again=ws.prepare_request(priced,'speech',voice['model'],line_index=0,voice=voice['voice'])
    for tool,record in [(ElevenLabsTTS(),voice),(ElevenLabsTTS(),again)]:
        sign(priced,'audio_spend',record,record['request_id'])
        result=tool.execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
        assert result.success,result.error
        assert ws.read(priced,record['request_id'])['kind']==record['kind']
    assert len(http)==2


def test_generated_identity_cannot_hide_behind_changed_bytes_and_metadata(priced,http):
    from tools.audio.music_gen import MusicGen
    record,_=music_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert result.success
    take=result.data['take'];path=priced/take['path'];data=bytearray(path.read_bytes());data[-1]=1;path.write_bytes(data)
    take={k:v for k,v in take.items() if k not in ['generation_receipt_id','approved_request_id','approval_record_sha256']}
    take.update(origin='imported',licence_note='Claimed imported',sha256=hashlib.sha256(data).hexdigest())
    (priced/'.audio-workspace'/(take['id']+'.json')).write_text(json.dumps(take))
    with pytest.raises(ValueError,match='provenance cannot be changed'):ws.request_decision(priced,take['id'],'accept')
    assert len(http)==1


@pytest.mark.parametrize('provider',['google','openai','suno','doubao','kling'])
def test_private_submissions_cannot_bypass_the_production_rule(priced,http,provider,monkeypatch):
    (priced/'project.json').write_text('{"production_kind":"trailer"}')
    inputs={'project_dir':str(priced),'output_path':str(priced/'unapproved.wav'),'text':'A quiet gallery.','prompt':'A quiet bell.'}
    if provider=='google':
        from tools.audio.google_tts import GoogleTTS
        call=lambda:GoogleTTS()._generate(inputs,api_key='invented-only')
    elif provider=='openai':
        import sys
        from types import SimpleNamespace as N
        def counted(**kw):http.append(('openai',kw));raise RuntimeError('Counting fake only')
        client=N(audio=N(speech=N(with_streaming_response=N(create=counted))))
        monkeypatch.setitem(sys.modules,'openai',N(OpenAI=lambda:client))
        from tools.audio.openai_tts import OpenAITTS
        call=lambda:OpenAITTS()._generate(inputs)
    elif provider=='suno':
        from tools.audio.suno_music import SunoMusic
        call=lambda:SunoMusic()._submit(inputs,'invented-only')
    elif provider=='doubao':
        from tools.audio.doubao_tts import DoubaoTTS
        call=lambda:DoubaoTTS()._generate(inputs,api_key='invented-only',voice_id='invented-voice')
    else:
        from tools.audio.kling_tts import KlingTTS
        class Client:
            def post(self,*a):http.append(('kling',{}));raise RuntimeError('Counting fake only')
        call=lambda:KlingTTS._create_and_collect_audios(Client(),{},inputs)
    with pytest.raises(ValueError,match='approved request'):call()
    assert http==[]

def audio_classes():
    from tools.base_tool import BaseTool
    classes=[]
    for path in (Path(__file__).parents[2]/'tools/audio').glob('*.py'):
        if path.name.startswith('_'):continue
        module=importlib.import_module('tools.audio.'+path.stem)
        classes.extend(c for _,c in inspect.getmembers(module,inspect.isclass) if c.__module__==module.__name__ and issubclass(c,BaseTool))
    for name in ['audio_probe','audio_energy','azure_stt','dashscope_asr','transcriber']:
        module=importlib.import_module('tools.analysis.'+name)
        classes.extend(c for _,c in inspect.getmembers(module,inspect.isclass) if c.__module__==module.__name__ and issubclass(c,BaseTool))
    return classes

@pytest.mark.parametrize('cls',audio_classes(),ids=lambda c:c.name)
def test_every_audio_tool_has_zero_trailer_service_contact(priced,http,monkeypatch,cls):
    import requests,urllib.request
    calls=[]
    def counted(*a,**k):calls.append(1);raise RuntimeError('Counting fake only')
    for name in ['post','get','request']:monkeypatch.setattr(requests,name,counted)
    monkeypatch.setattr(requests.Session,'request',counted);monkeypatch.setattr(urllib.request,'urlopen',counted)
    (priced/'project.json').write_text('{"production_kind":"trailer","pipeline_type":"authored-film@1.6"}')
    tool=cls()
    if tool.name=='transcriber':
        import sys
        from types import SimpleNamespace as N
        model=N(transcribe=lambda *a,**k:([],N(language='en',language_probability=1,duration=1)))
        monkeypatch.setitem(sys.modules,'faster_whisper',N(WhisperModel=lambda *a,**k:model))
    if hasattr(tool,'_get_api_key'):monkeypatch.setattr(tool,'_get_api_key',lambda:'invented-only')
    from tests.tools.test_audio_half_transport import wav_bytes
    source=priced/'local.wav';source.write_bytes(wav_bytes())
    result=tool.execute({'project_dir':str(priced),'output_path':str(priced/'assets/audio/unapproved.wav'),'input_path':str(source),'operation':'extract','text':'A quiet gallery.','prompt':'A quiet bell.','duration_seconds':3})
    assert calls==[] and http==[]
    if tool.capability in ['tts','music_generation','music_search'] or tool.name in ['azure_stt','dashscope_asr']:
        assert not result.success and 'approved request' in result.error.lower()

def test_unsent_config_failure_does_not_claim_a_reservation(priced,http):
    from tools.audio.music_gen import MusicGen
    record,_=music_request(priced);sign(priced,'audio_spend',record,record['request_id'])
    path=priced/'project.yaml';path.write_text(path.read_text()+'\n# unsigned edit\n')
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert not result.success and 'Nothing was sent' in result.error and 'reserved' not in result.error
    assert http==[]


def test_missing_event_layer_cannot_open_an_audio_route(priced,http,monkeypatch):
    import lib.events
    from tools.audio.google_tts import GoogleTTS
    (priced/'project.json').write_text('{"production_kind":"trailer"}')
    monkeypatch.delattr(lib.events,'emit_event')
    monkeypatch.setattr(GoogleTTS,'_get_api_key',lambda self:'invented-only')
    result=GoogleTTS().execute({'project_dir':str(priced),'output_path':str(priced/'voice.wav'),'text':'The gallery closes.'})
    assert not result.success and 'approved request' in result.error
    assert http==[]


def test_voice_reads_the_exact_snapshot_that_passed_story_checks(priced,http,monkeypatch):
    from lib import trailer_edit
    from lib.trailer_contract import edit_digest
    speech_request(priced)
    real=trailer_edit.paper_state
    def concurrent_save(root):
        checked=real(root)
        path=root/'artifacts/edit_decisions.json';changed=json.loads(path.read_text())
        changed['paper_cut']['lines'][0]['text']='Keep the light on tomorrow.'
        changed['edit_revision_sha256']=edit_digest(changed);path.write_text(json.dumps(changed))
        return checked
    monkeypatch.setattr(trailer_edit,'paper_state',concurrent_save)
    assert ws.line(priced,0)['text']=='The gallery is closed.'
    assert http==[]

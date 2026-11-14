import pytest
from tests.tools.test_audio_half_transport import project,priced,http,music_request,speech_request
from tests.tools.test_audio_governance import sign,no_real_network_contact
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace,clip_bytes
from tests.backlot.test_effect_panel import effect_world,voice_world,brief

@pytest.mark.parametrize('kind',['speech','music'])
def test_icon_path_files_paid_audio(priced,http,monkeypatch,kind):
    from tools.audio.music_gen import MusicGen
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    record=speech_request(priced) if kind=='speech' else music_request(priced)[0]
    sign(priced,'audio_spend',record,record['request_id'])
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    result=(ElevenLabsTTS() if kind=='speech' else MusicGen()).execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert result.success,result.error
    assert len(http)==1 and result.data['take']

def test_missing_probe_refuses_before_reservation(priced,http,monkeypatch):
    from tools.audio.music_gen import MusicGen
    from tools.cost_tracker import load_reservations
    record=music_request(priced)[0];sign(priced,'audio_spend',record,record['request_id'])
    monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-ffprobe')
    result=MusicGen().execute({'project_dir':str(priced),'audio_request_id':record['request_id']})
    assert not result.success and 'ffprobe' in result.error
    assert not http and not load_reservations(priced)

@pytest.mark.parametrize('missing',[False,True])
def test_footage_preflight_before_upload(film,provider,monkeypatch,missing):
    from tools.cost_tracker import load_reservations
    root,shot=film;record=prepare(film);approve(root,record)
    clip_bytes()  # Build the stand-in before adopting the icon's environment.
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    if missing:monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-probe')
    if missing:
        with pytest.raises(ValueError,match='ffprobe'):workspace().execute(root,record['request_id'])
        assert not load_reservations(root) and all(not v for v in provider.values())
    else:
        assert workspace().execute(root,record['request_id'])
        assert len(provider['posts'])==1

@pytest.mark.parametrize('missing',[False,True])
def test_effect_preflight(effect_world,monkeypatch,missing):
    from backlot import music_panel as cues
    from tools.cost_tracker import load_reservations
    root,posts,gets=effect_world
    b=cues.save_effect(root,brief());r=cues.prepare_cue(root,{'brief_id':b['brief_id']},kind='effect')['request']
    sign(root,'audio_spend',r,r['request_id'])
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    if missing:monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-probe')
    result=cues.send(root,r['request_id'],kind='effect')
    if missing:assert not result['success'] and not posts and not load_reservations(root)
    else:assert result['success'] and len(posts)==1

def test_shared_video_boundary_checks_before_upload(film,provider,monkeypatch):
    from tools.video import _shared
    from tools.cost_tracker import load_reservations
    root,_=film
    monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-probe')
    with pytest.raises(ValueError,match='ffprobe'):
        _shared.paid_call_context({'project_dir':str(root)},media='video')
    assert not load_reservations(root) and all(not v for v in provider.values())

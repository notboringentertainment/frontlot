"""Voice and music use signed, exact requests and local imported provenance."""
import pytest
from tests.tools.test_audio_governance import project, sign, no_real_network_contact

def test_import_without_licence_refuses(project,tmp_path):
    from lib.audio_workspace import import_music
    source=tmp_path/'invented.wav';source.write_bytes(b'invented')
    with pytest.raises(ValueError,match='licence'):
        import_music(project,source,'A local recording','')

def test_import_link_refuses(project,tmp_path):
    from lib.audio_workspace import import_music
    source=tmp_path/'invented.wav';source.write_bytes(b'invented')
    alias=tmp_path/'alias.wav';alias.symlink_to(source)
    with pytest.raises(ValueError,match='links'):
        import_music(project,alias,'A local recording','Owned by the inventor')

def test_unresolved_resolution_needs_signature(project):
    from lib.audio_workspace import resolve_charge
    from tests.tools.test_audio_governance import prepare,approve,run,Transport
    from tools.cost_tracker import load_reservations
    record=prepare(project);approve(project,record);transport=Transport('timeout')
    with pytest.raises(TimeoutError):run(project,record,transport)
    reservation=next(iter(load_reservations(project)))
    with pytest.raises(ValueError,match='signed'):
        resolve_charge(project,reservation,False,0,0,'Checked the invented account')
    assert (transport.calls,transport.downloads)==(1,0)

def test_signed_not_charged_resolution_releases_without_resubmitting(project):
    from lib.audio_workspace import resolve_charge,resolution_record
    from tests.tools.test_audio_governance import prepare,approve,run,Transport
    from tools.cost_tracker import load_reservations
    record=prepare(project);approve(project,record);transport=Transport('timeout')
    with pytest.raises(TimeoutError):run(project,record,transport)
    identity=next(iter(load_reservations(project)))
    resolution=resolution_record(project,identity,False,0,0,'Checked the invented account')
    sign(project,'audio_resolution',resolution,identity)
    resolve_charge(project,identity,False,0,0,'Checked the invented account')
    assert load_reservations(project)[identity]['state']=='failed'
    assert (transport.calls,transport.downloads)==(1,0)

def test_all_old_audio_needs_individual_approval(project,monkeypatch):
    import requests
    from tests.tools.test_audio_round3 import _older_settings
    from tools.audio.music_gen import MusicGen
    from tools.audio.elevenlabs_tts import ElevenLabsTTS
    _older_settings(project)
    calls=[]
    def post(*args,**kwargs):calls.append(1);raise AssertionError('unapproved post')
    monkeypatch.setattr(requests,'post',post);monkeypatch.setenv('ELEVENLABS_API_KEY','invented-only')
    for tool in (MusicGen(),ElevenLabsTTS()):
        result=tool.execute({'project_dir':str(project),'output_path':str(project/'audio.mp3'),'text':'A bell.','prompt':'A bell.','duration_seconds':3})
        assert not result.success and 'each request' in result.error
    assert calls==[]

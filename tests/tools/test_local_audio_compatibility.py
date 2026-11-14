import json
from types import SimpleNamespace
import pytest
from tests.tools.test_audio_governance import no_real_network_contact

@pytest.fixture(autouse=True)
def no_outside_calls(monkeypatch):
    import requests
    calls=[]
    def counted(*a,**k):calls.append(1);raise AssertionError('No outside contact is permitted')
    monkeypatch.setattr(requests.Session,'request',counted)
    yield
    assert calls==[]

@pytest.mark.parametrize('signed',[False,True,'unsigned-settings'])
def test_local_piper_has_no_exception_from_the_request_rule(tmp_path,monkeypatch,signed):
    from tools.audio.piper_tts import PiperTTS
    root=tmp_path/'invented-local';root.mkdir()
    (root/'project.json').write_text(json.dumps({'id':root.name,'pipeline_type':'authored-film'}))
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    monkeypatch.setattr('lib.events.PROJECTS_DIR',tmp_path)
    if signed is True:
        from tests.tools.test_audio_governance import project
        root=project.__wrapped__(tmp_path,monkeypatch)
    elif signed=='unsigned-settings':
        (root/'project.yaml').write_text('version: "1.2"\n')
    calls=[]
    def local(cmd,**kwargs):
        calls.append((cmd,kwargs['input']))
        from pathlib import Path
        Path(cmd[cmd.index('--output_file')+1]).write_bytes(b'local-audio')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr('tools.audio.piper_tts.shutil.which',lambda name:'/invented/piper')
    monkeypatch.setattr('tools.audio.piper_tts.subprocess.run',local)
    result=PiperTTS().execute({'project_dir':str(root),'text':'A quiet gallery.','output_path':str(root/'voice.wav')})
    assert not result.success
    assert 'approved request' in result.error if signed is True else 'signed settings' in result.error
    assert calls==[]

@pytest.mark.parametrize('case,reason',[('trailer','approved request'),('missing-settings','signed settings'),('linked','must not use links')])
def test_audio_refusals_give_the_actual_reason(tmp_path,monkeypatch,case,reason):
    from tools.audio._legacy_guard import refusal
    root=tmp_path/'older-local';root.mkdir()
    (root/'project.json').write_text(json.dumps({'id':root.name,'production_kind':'trailer' if case=='trailer' else 'film'}))
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    if case=='linked':
        link=tmp_path/'linked';link.symlink_to(root,target_is_directory=True);root=link
    result=refusal({'project_dir':str(root),'output_path':str(root/'voice.wav')})
    assert reason in result

def test_piper_trailer_refuses_before_local_execution(tmp_path,monkeypatch):
    from tools.audio.piper_tts import PiperTTS
    root=tmp_path/'invented-trailer';root.mkdir()
    (root/'project.json').write_text('{"production_kind":"trailer"}')
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    calls=[]
    monkeypatch.setattr('tools.audio.piper_tts.subprocess.run',lambda *a,**k:calls.append(1))
    result=PiperTTS().execute({'project_dir':str(root),'text':'A quiet gallery.','output_path':str(root/'voice.wav')})
    assert not result.success and 'approved request' in result.error
    assert calls==[]

def test_only_piper_is_a_local_generator_caught_by_the_audio_rule():
    from tests.tools.test_audio_round3_review import audio_classes
    names={cls.name for cls in audio_classes() if getattr(getattr(cls,'runtime',None),'value',None)=='local' and cls.capability in {'tts','music_generation','sfx','sound_effects','audio_generation','speech_to_text'}}
    assert names=={'piper_tts'}

def test_local_audio_does_not_ignore_a_broken_signed_history(tmp_path,monkeypatch):
    from tests.contracts.test_kling_tts import verified_older_audio_production
    from tools.audio._legacy_guard import refusal
    root=verified_older_audio_production.__wrapped__(tmp_path,monkeypatch)
    (root/'approvals.jsonl').write_text('{}\n')
    result=refusal({'project_dir':str(root),'output_path':str(root/'voice.wav')})
    assert 'approval history cannot be verified' in result and 'paths' not in result

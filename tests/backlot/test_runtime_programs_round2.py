"""The board's detached runners share the same executable discovery."""
import ast
import hashlib
import json
from pathlib import Path
import pytest
from lib import programs
from scripts import frontlot_run

def test_detached_runner_finds_program_under_icon_path(tmp_path,monkeypatch):
    safe=tmp_path/'bin';safe.mkdir()
    tool=safe/'invented-command';tool.write_text('#!/bin/sh\nprintf arrived\n');tool.chmod(0o700)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    monkeypatch.setattr(programs,'STANDARD_DIRS',(str(safe),))
    requests=tmp_path/'requests';requests.mkdir()
    argv=['invented-command']
    (requests/'invented.json').write_text(json.dumps({'argv':argv,'state':'launching','paid':False,
        'argv_sha256':hashlib.sha256(json.dumps(argv).encode()).hexdigest()}))
    assert frontlot_run.main(['--store',str(tmp_path),'--request','invented','--repo',str(tmp_path)])==0
    outcome=json.loads((requests/'invented.outcome.json').read_text())
    assert outcome['exit']==0 and outcome['tail']=='arrived'

@pytest.mark.parametrize('name',['frontlot_run','claude_session','gate_sign','canon_view','headshot_run','sheet_run'])
def test_board_runtime_scripts_use_shared_launcher(name):
    source=Path(frontlot_run.REPO)/'scripts'/f'{name}.py'
    calls=[n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.Call)
        and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name)
        and n.func.value.id=='subprocess' and n.func.attr in ('run','Popen','call','check_output','check_call')]
    assert not calls,'A board runtime script bypasses shared program discovery.'

def test_probe_declares_video_before_reservation():
    source=Path(frontlot_run.REPO)/'scripts'/'probe_b_kling.py'
    calls=[n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.Call)
        and isinstance(n.func,ast.Attribute) and n.func.attr=='paid_call_context']
    assert len(calls)==1 and any(k.arg=='media' and isinstance(k.value,ast.Constant)
        and k.value.value=='video' for k in calls[0].keywords)

def test_signer_uses_discovery_for_its_child(tmp_path,monkeypatch):
    from scripts import gate_sign
    safe=tmp_path/'bin';safe.mkdir();exe=safe/'invented-signer'
    exe.write_text('#!/bin/sh\nexit 0\n');exe.chmod(0o700)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    monkeypatch.setattr(programs,'STANDARD_DIRS',(str(safe),))
    with gate_sign._HoldLifecycleSignals() as lifecycle:
        child=lifecycle.spawn(['invented-signer'])
        try:assert child.wait(timeout=5)==0
        finally:gate_sign._reap_exact_child(child)

def test_assistant_system_commands_use_discovery(tmp_path,monkeypatch):
    from scripts import claude_session
    safe=tmp_path/'bin';safe.mkdir();exe=safe/'sysctl'
    exe.write_text('#!/bin/sh\nprintf "{ sec = 123, usec = 0 }"\n');exe.chmod(0o700)
    monkeypatch.setenv('PATH',str(safe))
    monkeypatch.setenv('OPENMONTAGE_SYSCTL',str(exe))
    seen=[];original=programs.run
    monkeypatch.setattr(programs,'run',lambda *a,**kw:seen.append(a[0][0]) or original(*a,**kw))
    assert claude_session.boot_time()==123
    assert seen==['sysctl']

@pytest.mark.parametrize('name',['ffmpeg','npx'])
def test_composer_availability_uses_discovery(tmp_path,monkeypatch,name):
    from tools.video import video_compose
    safe=tmp_path/'bin';safe.mkdir();exe=safe/name
    exe.write_text('#!/bin/sh\nexit 0\n');exe.chmod(0o700)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    monkeypatch.setattr(programs,'STANDARD_DIRS',(str(safe),))
    # The installed composer directory is independent of program discovery.
    assert programs.which(name)==str(exe)
    seen=[]
    original=programs.which
    monkeypatch.setattr(programs,'which',lambda command,**kw:seen.append(command) or original(command,**kw))
    tool=video_compose.VideoCompose()
    if name=='ffmpeg':assert tool._ffmpeg_available()
    else:
        monkeypatch.setattr(video_compose,'__file__',str(tmp_path/'tools'/'video'/'video_compose.py'))
        composer=tmp_path/'remotion-composer';composer.mkdir()
        (composer/'package.json').write_text('{}');(composer/'node_modules').mkdir()
        assert tool._remotion_available()
    assert name in seen

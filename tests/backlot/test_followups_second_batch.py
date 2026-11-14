"""Second-batch regressions on invented takes with counted transports."""
import json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http
from tests.backlot.test_music_panel import prepare as music_request
from tests.backlot.test_footage_controls import board,film,provider,post
from tests.tools.test_signed_footage import prepare,approve,workspace


def paid_without_record(root,monkeypatch):
    from backlot import music_panel as music
    from lib import audio_workspace as audio
    record=music_request(root);sign(root,'audio_spend',record,record['request_id'])
    def interrupted(*a,**k):raise OSError('Invented stop before the workspace entry.')
    with monkeypatch.context() as patch:
        patch.setattr(audio,'generated_take',interrupted)
        assert not music.send(root,record['request_id'])['success']
    return record


def test_2_released_request_refusal_is_true(board,provider,monkeypatch):
    client,root,shot=board;record=prepare((root,shot));approve(root,record)
    def local_failure(*a,**k):raise OSError('Invented local preparation failure.')
    monkeypatch.setattr(workspace(),'frozen_reference',local_failure)
    with pytest.raises(ValueError,match='not submitted or charged'):workspace().execute(root,record['request_id'])
    response=post(board,'dispatch',request_id=record['request_id'])
    assert response.status_code==400
    assert 'nothing was submitted or charged' in response.json()['detail'].lower()
    assert 'new request' in response.json()['detail'].lower()
    assert 'charge needs review' not in response.json()['detail'].lower()
    assert all(not rows for rows in provider.values())


@pytest.mark.parametrize('fault',['changed','missing'])
def test_3_bad_local_file_has_warning_not_recovery_button(voice_world,monkeypatch,fault):
    from backlot import voice_panel as voice
    root,posts,gets=voice_world;record=paid_without_record(root,monkeypatch)
    path=root/'assets/audio'/(record['request_id']+'.audio')
    original=Path.read_bytes
    if fault=='changed':path.write_bytes(original(path)+b'changed')
    else:
        def missing(self):
            if self==path:raise FileNotFoundError('Invented missing take file.')
            return original(self)
        monkeypatch.setattr(Path,'read_bytes',missing)
    item=next(r for r in voice.snapshot(root)['requests'] if r['record']['request_id']==record['request_id'])
    assert not item['recoverable'] and item['error']
    if fault=='changed':assert 'no longer matches its receipt' in item['error']
    else:assert 'file' in item['error']
    assert len(posts)==1 and not gets


@pytest.mark.parametrize('phase',['mid-write','before-rename'])
def test_5_interrupted_workspace_write_recovers_without_charge(voice_world,monkeypatch,phase):
    from backlot import voice_panel as voice,music_panel as music
    from lib import audio_workspace as audio,state_io
    root,posts,gets=voice_world;record=music_request(root);sign(root,'audio_spend',record,record['request_id'])
    path=root/'.audio-workspace'/(record['request_id']+'.json')
    original_dump=json.dump;original_replace=os.replace;original_mkstemp=state_io.tempfile.mkstemp;original_fdopen=os.fdopen
    target_fds=set()
    def old_partial(value,handle,*a,**k):
        if str(getattr(handle,'name',''))==str(path):
            handle.write('{"id":');handle.flush();raise OSError('Invented interrupted in-place write.')
        return original_dump(value,handle,*a,**k)
    def mkstemp(*a,**k):
        fd,name=original_mkstemp(*a,**k)
        if Path(name).parent==path.parent and Path(name).name.startswith('.'+path.name+'.'):target_fds.add(fd)
        return fd,name
    class Partial:
        def __init__(self,handle):self.handle=handle
        def __enter__(self):return self
        def __exit__(self,*a):return self.handle.__exit__(*a)
        def write(self,data):self.handle.write(data[:7]);self.handle.flush();raise OSError('Invented interrupted temporary write.')
    def fdopen(fd,*a,**k):
        handle=original_fdopen(fd,*a,**k)
        if phase=='mid-write' and fd in target_fds:
            target_fds.discard(fd);return Partial(handle)
        return handle
    def replace(source,destination):
        if phase=='before-rename' and Path(destination)==path:raise OSError('Invented stop before atomic publication.')
        return original_replace(source,destination)
    with monkeypatch.context() as patch:
        patch.setattr(json,'dump',old_partial);patch.setattr(state_io.tempfile,'mkstemp',mkstemp)
        patch.setattr(os,'fdopen',fdopen);patch.setattr(os,'replace',replace)
        assert not music.send(root,record['request_id'])['success']
    assert len(posts)==1 and not path.exists()
    assert not list(path.parent.glob('.'+path.name+'.*.tmp'))
    before=voice.snapshot(root)['budget']
    recovered=voice.recover(root,record['request_id'],kind='music')['take']
    assert audio.take_state(root,recovered)=='Waiting for your decision'
    assert voice.snapshot(root)['budget']==before and len(posts)==1 and not gets


@pytest.mark.parametrize('contents',[b'',b'{"id":',b'{}'])
def test_5_old_incomplete_record_is_not_a_take_and_can_be_recovered(voice_world,monkeypatch,contents):
    from backlot import voice_panel as voice
    from lib import audio_workspace as audio
    root,posts,gets=voice_world;record=paid_without_record(root,monkeypatch)
    path=audio._directory(root)/(record['request_id']+'.json');path.write_bytes(contents)
    staged=path.parent/('.'+path.name+'.abandoned.tmp');staged.write_bytes(b'{"incomplete":')
    with pytest.raises(ValueError,match='incomplete'):audio.read(root,record['request_id'])
    state=voice.snapshot(root);assert state['takes']==[]
    item=next(r for r in state['requests'] if r['record']['request_id']==record['request_id'])
    assert item['recoverable'] and not item['error']
    before=state['budget'];take=voice.recover(root,record['request_id'],kind='music')['take']
    assert take['id']==record['request_id'] and audio.take_state(root,take)=='Waiting for your decision'
    assert not staged.exists()
    assert voice.snapshot(root)['budget']==before and len(posts)==1 and not gets


def test_5_complete_record_with_wrong_receipt_is_not_overwritten(voice_world,monkeypatch):
    from backlot import voice_panel as voice
    from lib import audio_workspace as audio
    root,posts,gets=voice_world;record=paid_without_record(root,monkeypatch)
    take=voice.recover(root,record['request_id'],kind='music')['take'];take['generation_receipt_id']='a-different-receipt'
    path=audio._directory(root)/(record['request_id']+'.json');raw=json.dumps(take).encode();path.write_bytes(raw)
    with pytest.raises(ValueError):voice.recover(root,record['request_id'],kind='music')
    assert path.read_bytes()==raw and len(posts)==1


def fake_owner(tmp_path,monkeypatch):
    from tests import followup_safety as safety
    owner=tmp_path/'invented-home'/'.openmontage';owner.mkdir(parents=True);owner.chmod(0o700)
    for i in range(113):(owner/str(i)).write_text('Invented metadata.')
    monkeypatch.setattr(safety.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_dir=str(owner.parent)))
    return safety,owner


def test_6_fingerprint_detects_root_permission_change(tmp_path,monkeypatch):
    safety,owner=fake_owner(tmp_path,monkeypatch)
    before=tmp_path/'before.json';after=tmp_path/'after.json'
    monkeypatch.setattr(safety.sys,'argv',['fingerprint',str(before)]);safety.main()
    owner.chmod(0o755)
    monkeypatch.setattr(safety.sys,'argv',['fingerprint',str(before),str(after)])
    with pytest.raises(SystemExit,match='changed'):safety.main()


def test_6_fingerprint_refuses_output_in_owner_folder(tmp_path,monkeypatch):
    safety,owner=fake_owner(tmp_path,monkeypatch)
    monkeypatch.setenv('FRONTLOT_FOLLOWUPS_PARENT',str(owner.parent))
    for name in ('HOME','OPENMONTAGE_HOME','OPENMONTAGE_GATES_DIR','OPENMONTAGE_METADATA_PATH','TMPDIR'):
        monkeypatch.setenv(name,str(owner.parent/'isolated'/name))
    output=owner/'forbidden.json';monkeypatch.setattr(safety.sys,'argv',['fingerprint',str(output)])
    with pytest.raises(SystemExit,match='real app folder'):safety.main()
    assert not output.exists()

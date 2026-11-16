"""Invented paid-byte and optional-program failures, with counted stand-ins."""
import hashlib
import io
import sys
from pathlib import Path
import pytest
from tests.backlot.test_voice_panel import voice_world,options
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace
from tests.backlot.signing_outcome_helpers import held,figures,signed_review


def test_sound_retains_its_download_locator_before_local_filing(voice_world,monkeypatch):
    from backlot import voice_panel as voice
    from tools.cost_tracker import nonterminal_reservations,retained_download_paths
    from scripts.reconcile_paid_calls import reconcile_project
    root,posts,gets=voice_world
    voice.catalogue(root);record=voice.prepare_voice(root,options())['request']
    sign(root,'audio_spend',record,record['request_id'])
    def failed(*a,**kw):raise OSError('Invented local inspector failure after download.')
    monkeypatch.setattr('lib.audio_workspace.probe_audio',failed)
    assert not voice.send(root,record['request_id'])['success']
    reservation,=nonterminal_reservations(root)
    saved=retained_download_paths(root,reservation)
    assert len(saved)==1 and saved[0].read_bytes(),'The paid audio has no retained-download locator.'
    before=figures(root,1,fresh=True)
    output=io.StringIO();reconcile_project(root,api_key=None,out=output)
    assert 'Paid downloaded bytes were kept' in output.getvalue()
    assert figures(root,1,fresh=True)==before and len(posts)==len(gets)==1


@pytest.mark.parametrize('value',[7,{'unexpected':'evidence'},'not a list',None])
def test_optional_retained_evidence_cannot_block_a_signed_account_review(request,monkeypatch,value):
    from tools.cost_tracker import _append_reservation_event,load_reservations,retained_download_paths
    from lib.media_spending import apply_signed_resolution
    from backlot.state import _render_account_review
    root,reservation,counts,ceiling=held(request,'footage',monkeypatch)
    prior=counts();_append_reservation_event(root,{'reservation_id':reservation['reservation_id'],'retained_downloads':value})
    reservation=load_reservations(root)[reservation['reservation_id']]
    assert retained_download_paths(root,reservation)==[]
    gate=signed_review(root,reservation,charged=True)
    assert _render_account_review(root,gate)['footage_resolution']['charged'] is True
    apply_signed_resolution(root,reservation['reservation_id']);apply_signed_resolution(root,reservation['reservation_id'])
    assert figures(root,ceiling,fresh=True)==pytest.approx([.003,0,ceiling-.003])
    assert counts()==prior


def test_directory_sync_failure_keeps_the_path_in_the_download_record(tmp_path,monkeypatch):
    from lib import pathsafe,state_io
    from tools.cost_tracker import retained_download_paths
    root=tmp_path/'invented';root.mkdir();raw=b'Invented paid clip bytes.'
    staged=pathsafe.staging_file(root,'.bin');staged.write_bytes(raw)
    objects=root/'objects';objects.mkdir()
    expected=objects/(hashlib.sha256(raw).hexdigest()+'.bin')
    original=state_io._fsync_dir
    def full(directory):
        if Path(directory)==objects and expected.exists():raise OSError(28,'Invented directory sync failure.')
        return original(directory)
    monkeypatch.setattr(state_io,'_fsync_dir',full)
    reservation={'retained_downloads':[staged.relative_to(root).as_posix()]}
    with pytest.raises(OSError):pathsafe.store_content_addressed(staged,objects,'.bin')
    assert retained_download_paths(root,reservation)==[staged], 'The locator names a file that disappeared during local filing.'
    assert staged.read_bytes()==expected.read_bytes()==raw


def test_misconfigured_process_probe_returns_unknown_start_time(monkeypatch):
    from lib import run_lease
    monkeypatch.setitem(sys.modules,'psutil',None)
    monkeypatch.setenv('OPENMONTAGE_PS','relative-ps')
    assert run_lease.process_start_time(42) is None


@pytest.mark.parametrize('kind',['base','energy','probe','piper'])
def test_provider_status_refuses_a_relative_program_without_breaking_the_menu(monkeypatch,kind):
    from tools.base_tool import BaseTool,ToolStatus
    from tools.analysis.audio_energy import AudioEnergy
    from tools.analysis.audio_probe import AudioProbe
    from tools.audio.piper_tts import PiperTTS
    if kind=='base':
        class InventedTool(BaseTool):
            dependencies=['cmd:ffprobe']
            def execute(self,inputs):raise AssertionError('A status read must not execute a tool.')
        tool=InventedTool();name='FFPROBE'
    else:
        tool={'energy':AudioEnergy,'probe':AudioProbe,'piper':PiperTTS}[kind]()
        name={'energy':'FFMPEG','probe':'FFPROBE','piper':'PIPER'}[kind]
    monkeypatch.setenv('OPENMONTAGE_'+name,'relative-program')
    assert tool.get_status()==ToolStatus.UNAVAILABLE
    assert tool.get_info()['status']==ToolStatus.UNAVAILABLE.value


@pytest.mark.parametrize('kind',['duration','library','animation'])
def test_best_effort_probe_keeps_its_fallback_for_an_invalid_setting(tmp_path,monkeypatch,kind):
    from tools.analysis.audio_probe import probe_duration
    from tools.audio.music_library import MusicLibrary
    from tools.graphics.math_animate import MathAnimate
    path=tmp_path/'invented.bin';path.write_bytes(b'invented')
    monkeypatch.setenv('OPENMONTAGE_FFPROBE','relative-program')
    result={'duration':probe_duration,'library':MusicLibrary._probe_duration,'animation':MathAnimate()._probe_output}[kind](path)
    assert result==({'file_size_bytes':8} if kind=='animation' else None)


def test_windows_caption_render_uses_the_same_explicit_npx_as_discovery(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tools.video.remotion_caption_burn import RemotionCaptionBurn
    from lib import programs
    installed=tmp_path/'bin'/'npx.cmd';installed.parent.mkdir();installed.write_text('invented executable');installed.chmod(0o700)
    monkeypatch.setenv('OPENMONTAGE_NPX',str(installed))
    root=tmp_path/'composer';root.mkdir();source=tmp_path/'invented.mp4';source.write_bytes(b'invented')
    target=tmp_path/'captioned.mp4';tool=RemotionCaptionBurn();commands=[]
    monkeypatch.setattr(tool,'_find_remotion_root',lambda:root)
    assert tool._remotion_available() and programs.require('npx')==str(installed)
    def run(cmd,**kw):
        commands.append(cmd)
        if cmd[0]=='ffprobe':return SimpleNamespace(stdout='160x90' if 'stream=width,height' in cmd else '1')
        assert cmd[0]==str(installed),'Rendering uses a different program setting from availability.'
        target.write_bytes(b'invented captioned clip');return SimpleNamespace(stdout='')
    monkeypatch.setattr(tool,'run_command',run)
    with monkeypatch.context() as context:
        context.setattr(sys,'platform','win32')
        result=tool._render_remotion(str(source),str(target),[],4,24,'#ffffff')
    assert result.success and len(commands)==3


def test_difficulty_is_optional_but_a_shot_still_needs_its_purpose(film):
    import copy
    from lib import trailer_edit as edit
    root,_=film;current=edit.read_revision(root);paper=copy.deepcopy(current['paper_cut'])
    paper['shots'][0]['difficulty']=''
    result=edit.save_revision(root,paper,current['edit_revision_sha256'])
    assert result['paper_cut']['shots'][0]['difficulty']=='Not assessed'
    paper=copy.deepcopy(result['paper_cut']);paper['shots'][0]['purpose']=''
    with pytest.raises(ValueError,match='purpose') as refused:edit.save_revision(root,paper,result['edit_revision_sha256'])
    assert 'difficulty' not in str(refused.value).lower()


@pytest.mark.parametrize('name',['ffmpeg','npx'])
def test_composer_menu_handles_invalid_program_settings(monkeypatch,name):
    from lib import programs
    from tools.video.video_compose import VideoCompose
    original=programs.which
    monkeypatch.setenv('OPENMONTAGE_'+name.upper(),'relative-program')
    monkeypatch.setattr(programs,'which',lambda command,**kw:original(command,**kw) if command==name else None)
    info=VideoCompose().get_info()
    assert info['render_engines']['ffmpeg' if name=='ffmpeg' else 'remotion'] is False

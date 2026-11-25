"""Render entry points fail before creating work when a local program is absent."""
from pathlib import Path
import subprocess
import pytest

def test_missing_node_refuses_card_render_before_creating_work(tmp_path,monkeypatch):
    from lib import trailer_titles as titles
    monkeypatch.setenv('OPENMONTAGE_NODE','/nonexistent/invented-node')
    with pytest.raises(ValueError,match='node is missing'):
        titles.render_layer(tmp_path,[],24,tmp_path)
    assert list(tmp_path.iterdir())==[]

def test_missing_ffmpeg_refuses_sound_mix_before_creating_parts(tmp_path,monkeypatch):
    from tools.audio.audio_mixer import AudioMixer
    monkeypatch.setenv('OPENMONTAGE_FFMPEG','/nonexistent/invented-ffmpeg')
    with pytest.raises(ValueError,match='ffmpeg is missing'):
        AudioMixer()._full_mix({'timeline_tracks':[],'target_frames':24,'speech_margin_db':12,'output_path':str(tmp_path/'mix.wav')})
    assert list(tmp_path.iterdir())==[]

def test_broken_node_refuses_before_creating_card_work(tmp_path,monkeypatch):
    from lib import trailer_titles as titles
    broken=tmp_path/'broken-node';broken.write_text('#!/bin/sh\nexit 2\n');broken.chmod(0o700)
    monkeypatch.setenv('OPENMONTAGE_NODE',str(broken))
    monkeypatch.setattr(titles,'renderer_configuration',lambda:(tmp_path,tmp_path/'invented-browser'))
    with pytest.raises(ValueError,match='local title program could not run'):
        titles.render_layer(tmp_path,[],24,tmp_path)
    assert list(tmp_path.iterdir())==[broken]

def test_broken_ffmpeg_refuses_colour_before_any_render_command(tmp_path,monkeypatch):
    from tools.enhancement.color_grade import ColorGrade
    source=tmp_path/'invented.mp4';source.write_bytes(b'invented local source')
    broken=tmp_path/'broken-ffmpeg';broken.write_text('#!/bin/sh\nexit 2\n');broken.chmod(0o700)
    monkeypatch.setenv('OPENMONTAGE_FFMPEG',str(broken))
    launched=[];original=subprocess.Popen
    def spawn(args,*a,**kw):
        launched.append(args);return original(args,*a,**kw)
    monkeypatch.setattr(subprocess,'Popen',spawn)
    result=ColorGrade().execute({'input_path':str(source),'output_path':str(tmp_path/'not-created.mp4'),'profile':'cinematic_warm'})
    assert not result.success and 'ffmpeg could not run' in result.error
    assert launched and all(args[1:]==['-version'] for args in launched),'Colour rendering began before checking the installed program.'
    assert not (tmp_path/'not-created.mp4').exists()

def test_missing_probe_refuses_preview_before_sound_work(tmp_path,monkeypatch):
    from lib import cutting_view as cut
    from tools.audio.audio_mixer import AudioMixer
    saved={'edit_revision_sha256':'invented-revision','timeline':{'mix':{}}}
    monkeypatch.setattr(cut.trailer_edit,'read_revision',lambda *a:saved)
    monkeypatch.setattr(cut,'editable',lambda value:value)
    monkeypatch.setattr(cut,'validate_cut',lambda *a,**kw:{'cards':[]})
    monkeypatch.setattr(AudioMixer,'_full_mix',lambda *a:pytest.fail('Sound work started with no file inspector.'))
    monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-probe')
    with pytest.raises(ValueError,match='ffprobe is missing'):
        cut.render_preview(tmp_path,'invented-revision')
    assert list(tmp_path.iterdir())==[]

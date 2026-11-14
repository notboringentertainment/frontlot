"""Actual encoded files expose inaudible effects and allowed hot mixes."""
import subprocess
import numpy as np
import pytest
from tests.tools.test_trailer_colour_mix import RATE,write_signal
from tests.tools.test_colour_mix_review_round2 import track,encode

@pytest.fixture(autouse=True)
def no_service_contact(monkeypatch):
    calls=[];monkeypatch.setattr('requests.post',lambda *a,**k:calls.append(1))
    yield
    assert calls==[]

@pytest.mark.parametrize('case',['turned-down','room-tone'])
@pytest.mark.parametrize('bed_length',[48,144])
def test_effect_below_the_floor_is_never_called_heard(tmp_path,case,bed_length):
    voice=write_signal(tmp_path/'voice.wav',.15*np.sin(2*np.pi*997*np.arange(RATE*2)/RATE))
    bed=write_signal(tmp_path/'bed.wav',.3*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    effect=write_signal(tmp_path/'effect.wav',np.random.default_rng(71).normal(0,.15 if case=='turned-down' else 10**(-75/20),RATE))
    report,_,_=encode(tmp_path,[track(voice,'speech',0,48),track(bed,'music',0,bed_length),track(effect,'sfx',96,24,-60 if case=='turned-down' else 0)])
    result=report['effect_checks'][0]
    assert result['status']=='unheard',result
    assert result['audible_rms_dbfs'] < -60
    assert not any('Effect 1 is heard' in s for s in report['messages'])
    assert any('Effect 1 could not be heard at its saved level near 0:04.000' in s for s in report['messages'])

def test_allowed_plus_six_music_and_small_margin_make_a_valid_real_file(tmp_path):
    t=np.arange(RATE*6)/RATE
    voice=write_signal(tmp_path/'voice.wav',.15*np.sin(2*np.pi*997*t)*((t>=1)&(t<3)))
    bed=write_signal(tmp_path/'bed.wav',.8*np.sin(2*np.pi*211*t))
    report,_,_=encode(tmp_path,[track(voice,'speech'),track(bed,'music',level=6)],margin=6)
    assert report['on_target'],report['messages']
    assert abs(report['integrated_lufs']+16)<=1 and report['true_peak_dbtp']<=-1

def test_actual_voice_dropout_has_one_named_warning(tmp_path):
    from lib.trailer_finishing import review_output,decode_audio
    voice=write_signal(tmp_path/'voice.wav',.15*np.sin(2*np.pi*997*np.arange(RATE*2)/RATE))
    bed=write_signal(tmp_path/'bed.wav',.3*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    _,refs,good=encode(tmp_path,[track(voice,'speech',24,48),track(bed,'music')])
    actual=decode_audio(good);actual[RATE*3//2:RATE*3]=0
    broken=tmp_path/'dropout.mp4'
    subprocess.run(['ffmpeg','-v','error','-n','-i',str(good),'-f','f32le','-ar','48000','-ac','2','-i','pipe:0','-map','0:v','-map','1:a','-c:v','copy','-c:a','aac',str(broken)],input=actual.astype('<f4').tobytes(),check=True)
    report=review_output(broken,refs)
    assert report['voice_checks'][0]['status']=='partial'
    assert len([s for s in report['messages'] if 'Voice in sound 1' in s])==1
    assert not any('Speech is hard to hear' in s for s in report['messages'])

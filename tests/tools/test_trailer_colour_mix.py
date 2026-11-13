"""Actual local signals make the finishing checks able to fail."""
import math,subprocess
from pathlib import Path
import numpy as np
import pytest

RATE=48000

def write_signal(path,samples):
    stereo=np.repeat(np.asarray(samples,dtype=np.float32)[:,None],2,axis=1)
    subprocess.run(['ffmpeg','-v','error','-n','-f','f32le','-ar',str(RATE),'-ac','2','-i','pipe:0','-c:a','pcm_f32le',str(path)],input=stereo.tobytes(),check=True)
    return str(path)

@pytest.fixture
def signals(tmp_path):
    t=np.arange(RATE*6)/RATE
    speech=.15*np.sin(2*np.pi*997*t)*((t>=1)&(t<3))
    music=.8*np.sin(2*np.pi*211*t)
    rng=np.random.default_rng(41);effect=np.zeros(len(t));effect[4*RATE:4*RATE+RATE//3]=rng.normal(0,.15,RATE//3)
    paths=[write_signal(tmp_path/name,x) for name,x in [('words.wav',speech),('bed.wav',music),('door.wav',effect)]]
    return tmp_path,paths

def mix(signals,*,automatic=True):
    from tools.audio.audio_mixer import AudioMixer
    root,paths=signals
    tracks=[{'path':p,'role':role,'start_frame':0,'source_in_frame':0,'source_out_frame':144,'level_db':0,'fade_in_frames':0,'fade_out_frames':0} for p,role in zip(paths,['speech','music','sfx'])]
    out=root/('balanced.wav' if automatic else 'before.wav')
    result=AudioMixer()._full_mix({'timeline_tracks':tracks,'target_frames':144,'speech_margin_db':12,'automatic':automatic,'output_path':str(out)})
    assert result.success,result.error
    target=root/('balanced.mp4' if automatic else 'before.mp4')
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=s=160x90:r=24:d=6','-i',str(out),'-c:v','libx264','-c:a','aac','-ar','48000','-ac','2','-t','6',str(target)],check=True)
    return target,result.data

def test_real_encoded_mix_is_speech_first_stereo_and_on_target(signals):
    from lib.trailer_finishing import review_output,decode_audio
    target,refs=mix(signals)
    report=review_output(target,refs)
    assert abs(report['integrated_lufs']+16)<=1
    assert report['true_peak_dbtp']<=-1
    assert report['channels']==2 and report['sample_rate']==48000
    assert report['speech_checks'] and all(x['status']=='clear' and x['margin_db']>=11 for x in report['speech_checks'])
    assert report['effect_checks'][0]['status']=='on_time'
    assert abs(report['effect_checks'][0]['observed_frame']-report['effect_checks'][0]['expected_frame'])<=1
    # Independent spectral measurement of the encoded result, not the stems.
    a=decode_audio(target)[RATE:2*RATE].mean(axis=1);t=np.arange(len(a))/RATE
    energy=lambda hz:abs(np.dot(a,np.exp(-2j*np.pi*hz*t)))
    assert 20*math.log10(energy(997)/energy(211))>=11
    assert any('Loudness is on target' in s for s in report['messages'])

def test_actual_bad_file_is_not_given_its_intermediate_pass(signals):
    from lib.trailer_finishing import review_output
    target,refs=mix(signals);bad=target.with_name('too-loud.mp4')
    subprocess.run(['ffmpeg','-v','error','-n','-i',str(target),'-c:v','copy','-af','volume=12dB','-c:a','aac',str(bad)],check=True)
    report=review_output(bad,refs)
    assert report['true_peak_dbtp']>-1 and not report['on_target']
    assert any('peak' in s.lower() or 'clipping' in s.lower() for s in report['messages'])

def test_loud_music_before_balancing_is_caught(signals):
    from lib.trailer_finishing import review_output
    target,refs=mix(signals,automatic=False);report=review_output(target,refs)
    assert any(x['status']=='hard_to_hear' for x in report['speech_checks'])
    assert any('Speech is hard to hear from' in s for s in report['messages'])

def test_silence_and_wrong_effect_time_are_measured_in_the_result(signals):
    from lib.trailer_finishing import review_output
    target,refs=mix(signals);bad=target.with_name('missing-and-late.mp4')
    # The final bytes lose the words and put a recognizable effect later.
    subprocess.run(['ffmpeg','-v','error','-n','-i',str(target),'-i',signals[1][2],'-filter_complex','[1:a]adelay=500|500[a]','-map','0:v','-map','[a]','-c:v','copy','-c:a','aac','-t','6',str(bad)],check=True)
    report=review_output(bad,refs)
    assert any(x['status']!='clear' for x in report['speech_checks'])
    assert report['silences'] and any('Silence' in s for s in report['messages'])
    assert report['effect_checks'][0]['status']=='wrong_time'

def test_clipped_source_is_reported_even_after_peak_is_lowered(signals):
    from lib.trailer_finishing import review_output
    root,paths=signals;t=np.arange(6*RATE)/RATE
    clipped=np.clip(3*np.sin(2*np.pi*3100*t),-1,1)*((t>4)&(t<4.3))
    paths[2]=write_signal(root/'clipped.wav',clipped)
    target,refs=mix(signals);report=review_output(target,refs)
    assert any('already clips' in s for s in report['messages'])

@pytest.mark.parametrize('value',[True,float('nan'),-101,101,'warm'])
def test_colour_controls_refuse_bad_values(value):
    from tools.enhancement.color_grade import ColorGrade
    with pytest.raises(ValueError,match='whole number.*minus one hundred'):
        ColorGrade()._build_filter({'trailer_adjustments':{'brightness':value,'warmth':0,'contrast':0,'colour':0}})

def test_missing_or_hdr_colour_is_handled_honestly():
    from tools.enhancement.color_grade import ColorGrade
    tool=ColorGrade();neutral={'brightness':0,'warmth':0,'contrast':0,'colour':0}
    with pytest.raises(ValueError,match='HDR.*standard-range'):
        tool._build_filter({'trailer_adjustments':neutral,'source_colour':{'color_transfer':'smpte2084'}})
    assert 'bt709' in tool._build_filter({'trailer_adjustments':neutral,'source_colour':{}})

@pytest.mark.parametrize('space',['bt2020nc','bt2020c'])
def test_wide_colour_is_not_mistaken_for_untagged_standard_picture(space):
    from tools.enhancement.color_grade import ColorGrade
    from lib.trailer_finishing import NEUTRAL
    with pytest.raises(ValueError,match='wide-colour.*standard-range'):
        ColorGrade()._build_filter({'trailer_adjustments':NEUTRAL,'source_colour':{'color_space':space}})

@pytest.mark.parametrize('primaries',['smpte431','smpte432','smpte428'])
def test_declared_cinema_or_display_wide_primaries_are_refused(primaries):
    from tools.enhancement.color_grade import ColorGrade
    from lib.trailer_finishing import NEUTRAL
    with pytest.raises(ValueError,match='wide-colour.*standard-range'):
        ColorGrade()._build_filter({'trailer_adjustments':NEUTRAL,'source_colour':{'color_space':'bt709','color_primaries':primaries}})

def test_silent_spoken_take_cannot_hide_under_music(signals):
    from lib.trailer_finishing import review_output
    root,paths=signals;paths[0]=write_signal(root/'silent-words.wav',np.zeros(6*RATE))
    target,refs=mix(signals);report=review_output(target,refs)
    assert any('no audible speech' in s for s in report['messages'])

def test_silent_effect_report_names_its_saved_moment(signals):
    from lib.trailer_finishing import review_output
    root,paths=signals;paths[2]=write_signal(root/'silent-effect.wav',np.zeros(6*RATE))
    target,refs=mix(signals)
    # An all-silent reference has the same samples at any position. Its saved
    # position, not an invented waveform onset at zero, must guide listening.
    refs['effects'][0]['start_frame']=96
    report=review_output(target,refs)
    assert report['effect_checks'][0]['status']=='uncertain'
    assert report['effect_checks'][0]['expected_frame']==96
    assert any('not be identified confidently near 0:04.000' in s for s in report['messages'])

def test_unused_clipped_part_is_not_reported_as_clipping_in_the_cut(signals):
    from tools.audio.audio_mixer import AudioMixer
    root,paths=signals;t=np.arange(6*RATE)/RATE
    paths[0]=write_signal(root/'clipped-after-trim.wav',np.where(t<1,.1*np.sin(2*np.pi*997*t),np.clip(3*np.sin(2*np.pi*3100*t),-1,1)))
    result=AudioMixer()._full_mix({'timeline_tracks':[dict(path=paths[0],role='speech',start_frame=0,source_in_frame=0,source_out_frame=24,level_db=0,fade_in_frames=0,fade_out_frames=0)],'target_frames':24,'speech_margin_db':12,'output_path':str(root/'trimmed.wav')})
    assert result.success and not any('already clips' in s for s in result.data['source_warnings'])

@pytest.mark.parametrize('key,value',[('start_frame',True),('start_frame',-.5),('source_out_frame',1.5),('level_db',float('inf')),('role','unknown')])
def test_new_mix_path_refuses_bad_controls_before_starting_a_process(tmp_path,monkeypatch,key,value):
    from tools.audio.audio_mixer import AudioMixer
    calls=[]
    def refuse(*a,**kw):calls.append(a);raise AssertionError('No process should start for an invalid mix.')
    monkeypatch.setattr(subprocess,'run',refuse)
    track=dict(path=str(tmp_path/'not-read.wav'),role='speech',start_frame=0,source_in_frame=0,source_out_frame=24,level_db=0,fade_in_frames=0,fade_out_frames=0);track[key]=value
    with pytest.raises(ValueError,match='whole frames|sound level|voice, music or effects'):
        AudioMixer()._full_mix({'timeline_tracks':[track],'target_frames':24,'speech_margin_db':12,'output_path':str(tmp_path/'not-written.wav')})
    assert calls==[]

def test_decoder_accepts_only_local_files_before_starting_a_process(monkeypatch):
    from lib.trailer_finishing import decode_audio
    calls=[]
    def fake(*a,**kw):calls.append(a);return subprocess.CompletedProcess(a,1,b'',b'')
    monkeypatch.setattr(subprocess,'run',fake)
    with pytest.raises(ValueError,match='local file'):decode_audio('https://invented.invalid/not-contacted.wav')
    assert calls==[]

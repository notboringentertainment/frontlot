"""The reviewers' signals expose false reassurance in the actual encoded file."""
import json,math,subprocess
import numpy as np
import pytest
from tests.tools.test_trailer_colour_mix import RATE,write_signal

def track(path,role,start=0,frames=144,level=0):
    return dict(path=path,role=role,start_frame=start,source_in_frame=0,source_out_frame=frames,level_db=level,fade_in_frames=0,fade_out_frames=0)

def encode(root,tracks,*,automatic=True,margin=12):
    from tools.audio.audio_mixer import AudioMixer
    from lib.trailer_finishing import review_output
    out=root/('balanced.wav' if automatic else 'before.wav')
    mixed=AudioMixer()._full_mix({'timeline_tracks':tracks,'target_frames':144,'speech_margin_db':margin,'automatic':automatic,'output_path':str(out)})
    assert mixed.success,mixed.error
    target=out.with_suffix('.mp4')
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=s=160x90:r=24:d=6','-i',str(out),'-c:v','libx264','-c:a','aac','-ar','48000','-ac','2','-t','6',str(target)],check=True)
    report=review_output(target,mixed.data)
    with target.with_suffix('.review.json').open('x') as f:json.dump(report,f,indent=2)
    return report,mixed.data,target

@pytest.mark.parametrize('case',['turned-down','quiet-level','room-tone','one-click','audible-then-silent'])
def test_each_unheard_voice_is_named_and_never_gets_reassurance(tmp_path,monkeypatch,case):
    calls=[];monkeypatch.setattr('requests.post',lambda *a,**k:calls.append(1))
    t=np.arange(RATE*2)/RATE;voice=.15*np.sin(2*np.pi*997*t)
    if case=='room-tone':voice=np.random.default_rng(5).normal(0,10**(-75/20),len(t))
    if case in ('one-click','audible-then-silent'):voice=np.zeros(len(t));voice[RATE//3]=.00002
    path=write_signal(tmp_path/'voice.wav',voice)
    voices=[track(path,'speech',24,48,-60 if case=='turned-down' else -40 if case=='quiet-level' else 0)]
    number=1;start,end='0:01.000','0:03.000'
    if case=='audible-then-silent':
        first=write_signal(tmp_path/'audible.wav',.15*np.sin(2*np.pi*997*t))
        voices=[track(first,'speech',0,48),track(path,'speech',72,48)]
        number=2;start,end='0:03.000','0:05.000'
    bed=write_signal(tmp_path/'bed.wav',.5*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    report,refs,_=encode(tmp_path,[*voices,track(bed,'music')])
    assert not any('Speech sits clearly above' in s or 'lowest measured margin' in s for s in report['messages']),report['messages']
    assert any(f'Voice in sound {number}' in s and start in s and end in s for s in report['messages']),report['messages']
    assert report['voice_checks'][number-1]['status']!='clear'
    assert calls==[]

def test_quiet_but_present_voice_is_not_called_clearly_audible(tmp_path):
    # No normalization here: the actual final voice is above the audibility
    # floor but quiet in absolute terms, even though its relative margin wins.
    t=np.arange(RATE*6)/RATE
    voice=write_signal(tmp_path/'quiet.wav',.005*np.sin(2*np.pi*997*t))
    bed=write_signal(tmp_path/'tiny-bed.wav',.0001*np.sin(2*np.pi*211*t))
    report,_,_=encode(tmp_path,[track(voice,'speech'),track(bed,'music')],automatic=False)
    assert not any('Speech sits clearly above' in s or 'lowest measured margin' in s for s in report['messages'])
    assert any('Voice in sound 1' in s and 'quiet' in s.lower() and 'listen' in s.lower() for s in report['messages'])

def test_no_audible_sound_does_not_ask_to_lower_loud_peaks(tmp_path):
    silent=write_signal(tmp_path/'silent.wav',np.zeros(RATE*6))
    report,_,_=encode(tmp_path,[track(silent,'speech')])
    assert report['integrated_lufs'] is None
    assert any('No audible sound was measured' in s for s in report['messages'])
    assert not any('peak' in s.lower() for s in report['messages'])

def test_speech_summary_quotes_the_measured_minimum_not_the_slider(tmp_path):
    t=np.arange(RATE*6)/RATE
    voice=write_signal(tmp_path/'words.wav',.1*np.sin(2*np.pi*997*t))
    bed=write_signal(tmp_path/'bed.wav',.0001*np.sin(2*np.pi*211*t))
    report,_,_=encode(tmp_path,[track(voice,'speech'),track(bed,'music')],automatic=False,margin=6)
    minimum=min(r['margin_db'] for r in report['speech_checks'])
    assert minimum>20
    assert any(f'{minimum:.1f}' in s and 'measured' in s for s in report['messages']),report['messages']

@pytest.mark.parametrize('field,value',[('color_transfer','smpte2084'),('color_primaries','smpte432'),('color_space','bt2020nc')])
def test_any_independent_hdr_or_wide_colour_tag_is_refused(field,value):
    from tools.enhancement.color_grade import ColorGrade
    from lib.trailer_finishing import NEUTRAL
    labels={'color_transfer':'bt709','color_primaries':'bt709','color_space':'bt709'};labels[field]=value
    with pytest.raises(ValueError,match='HDR or wide-colour.*standard-range'):
        ColorGrade()._build_filter({'trailer_adjustments':NEUTRAL,'source_colour':labels})

@pytest.mark.parametrize('late',[False,True])
def test_effect_is_identified_under_speech_after_balancing(tmp_path,monkeypatch,late):
    from tools.audio.audio_mixer import AudioMixer
    t=np.arange(RATE*6)/RATE
    voice=write_signal(tmp_path/'voice.wav',.15*np.sin(2*np.pi*997*t)*((t>=.5)&(t<3.5)))
    music=write_signal(tmp_path/'music.wav',.8*np.sin(2*np.pi*211*t))
    fx=np.random.default_rng(3).normal(0,.15,RATE);fx[RATE//3:]=0
    effect=write_signal(tmp_path/'effect.wav',fx)
    if late:
        original=AudioMixer._mix
        def shifted(self,inputs):
            if str(inputs.get('output_path','')).endswith('other-source.wav'):
                inputs={**inputs,'tracks':[{**r,'start_frame':r['start_frame']+12} if r.get('role')=='sfx' else r for r in inputs['tracks']]}
            return original(self,inputs)
        monkeypatch.setattr(AudioMixer,'_mix',shifted)
    report,_,_=encode(tmp_path,[track(voice,'speech'),track(music,'music'),track(effect,'sfx',48,24)])
    result=report['effect_checks'][0]
    assert result['status']==('wrong_time' if late else 'on_time'),result
    assert abs(result['observed_frame']-(60 if late else 48))<=1

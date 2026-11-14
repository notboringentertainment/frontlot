"""A click cannot stand in for the sustained sound of a spoken line."""
import numpy as np
import pytest
from tests.tools.test_trailer_colour_mix import RATE,write_signal
from tests.tools.test_colour_mix_review_round2 import encode,track

def speech_shaped(seconds=3):
    t=np.arange(round(RATE*seconds))/RATE
    wave=.08*(np.sin(2*np.pi*127*t)+.6*np.sin(2*np.pi*223*t)+.3*np.sin(2*np.pi*761*t))
    envelope=np.zeros(len(t))
    for start,end in [(.05,.35),(.43,.8),(.9,1.2),(1.28,1.6),(1.7,2),(2.1,2.5),(2.6,2.93)]:
        gate=(t>=start)&(t<end)
        envelope[gate]=np.minimum(1,np.minimum((t[gate]-start)/.005,(end-t[gate])/.005))
    return wave*envelope

@pytest.mark.parametrize('case',['full-volume-click','two-millisecond-tick','brief-voice','speech-with-pauses','short-voice'])
def test_presence_covers_the_placement_without_refusing_natural_pauses(tmp_path,monkeypatch,case):
    calls=[];monkeypatch.setattr('requests.post',lambda *a,**k:calls.append(1))
    voice=np.zeros(RATE*3);length=72
    if case=='full-volume-click':voice[RATE]=1
    elif case=='two-millisecond-tick':voice[RATE:RATE+96]=.99*np.sin(2*np.pi*900*np.arange(96)/RATE)
    elif case=='brief-voice':voice[RATE:RATE+RATE//5]=.2*np.sin(2*np.pi*997*np.arange(RATE//5)/RATE)
    elif case=='speech-with-pauses':voice=speech_shaped()
    else:voice=.15*np.sin(2*np.pi*997*np.arange(4000)/RATE);length=2
    source=write_signal(tmp_path/'voice.wav',voice)
    bed=write_signal(tmp_path/'bed.wav',.3*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    effect=write_signal(tmp_path/'effect.wav',np.random.default_rng(19).normal(0,.05,RATE//4))
    report,refs,_=encode(tmp_path,[track(source,'speech',24,length),track(bed,'music'),track(effect,'sfx',108,6)])
    positive=any('lowest measured margin' in s for s in report['messages'])
    if case in ('speech-with-pauses','short-voice'):
        assert positive,report['messages']
        assert report['voice_checks'][0]['status']=='clear'
        assert not any('Voice in sound 1' in s for s in report['source_warnings'])
    else:
        assert not positive,report['messages']
        assert report['voice_checks'][0]['status'] in ('unheard','partial')
        assert any('Voice in sound 1' in s and '0:01.000' in s and '0:04.000' in s and 'listen' in s.lower() for s in report['messages'])
        assert any(('sustained' if case!='brief-voice' else 'only part') in s for s in report['messages'])
        # These incomplete recordings cannot trigger automatic ducking.
        from lib.trailer_finishing import decode_audio
        actual=decode_audio(refs['background_path'])
        assert np.max(np.abs(actual[RATE:RATE*4]))>.1
        if case!='brief-voice':
            np.testing.assert_allclose(actual[RATE:RATE*4],decode_audio(bed)[RATE:RATE*4],atol=1e-6)
    assert calls==[]

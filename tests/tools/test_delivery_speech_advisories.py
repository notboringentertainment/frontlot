"""Real final-file speech warnings inform the writer instead of vetoing pauses."""
import numpy as np
import pytest
from tests.speech_fixtures import paused_voice
from tests.tools.test_trailer_colour_mix import RATE,write_signal
from tests.tools.test_colour_mix_review_round2 import encode,track

@pytest.fixture(autouse=True)
def counted_contact(monkeypatch):
    calls=[];monkeypatch.setattr('requests.post',lambda *a,**kw:calls.append(1))
    yield
    assert calls==[]

@pytest.mark.parametrize('case',['syllable-gaps','one-second-pause','padding'])
def test_timed_notes_for_sustained_voice_do_not_veto_final_approval(tmp_path,case):
    from lib.trailer_delivery import _sound_ok
    voice=write_signal(tmp_path/'voice.wav',paused_voice(case))
    bed=write_signal(tmp_path/'bed.wav',.15*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    effect=write_signal(tmp_path/'effect.wav',np.random.default_rng(72).normal(0,.05,RATE//4))
    measured,_,_=encode(tmp_path,[track(voice,'speech',24,72),track(bed,'music'),track(effect,'sfx',108,6)])
    assert measured['voice_checks'][0]['presence']['sustained']
    assert any('Voice in sound 1' in s and '0:01.000' in s and '0:04.000' in s for s in measured['messages'])
    assert _sound_ok(measured),measured['messages']
    assert measured['source_failures']==[]

@pytest.mark.parametrize('case',['full-click','tick','silence','clipping'])
def test_absent_or_damaged_voice_is_still_refused(tmp_path,case):
    from lib.trailer_delivery import _sound_ok
    voice=np.zeros(RATE*3)
    if case=='full-click':voice[RATE]=1
    elif case=='tick':voice[RATE:RATE+96]=.99*np.sin(2*np.pi*900*np.arange(96)/RATE)
    elif case=='clipping':voice=np.clip(4*paused_voice('padding'),-.99999,.99999);voice[RATE:RATE+10]=1
    source=write_signal(tmp_path/'voice.wav',voice)
    bed=write_signal(tmp_path/'bed.wav',.15*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    measured,_,_=encode(tmp_path,[track(source,'speech',24,72),track(bed,'music')])
    assert not _sound_ok(measured),measured['messages']

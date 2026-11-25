"""A whole unheard placement is not a pause; hum is never certified speech."""
import numpy as np
import re
import pytest
from tests.tools.test_trailer_colour_mix import RATE,write_signal
from tests.tools.test_colour_mix_review_round2 import encode,track
from tests.speech_fixtures import paused_voice

@pytest.fixture(autouse=True)
def counted_contact(monkeypatch):
    calls=[];monkeypatch.setattr('requests.post',lambda *a,**k:calls.append(1))
    yield
    assert calls==[]

@pytest.mark.parametrize('db',[-50,-40,-30])
def test_hiss_that_is_hard_to_hear_for_the_whole_placement_blocks(tmp_path,db):
    from lib.trailer_delivery import _sound_ok
    noise=np.random.default_rng(91).normal(size=RATE*4);noise*=10**(db/20)/np.sqrt(np.mean(noise**2))
    voice=write_signal(tmp_path/'hiss.wav',noise)
    bed=write_signal(tmp_path/'bed.wav',.2*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    measured,_,_=encode(tmp_path,[track(voice,'speech',24,96),track(bed,'music')])
    assert not _sound_ok(measured),measured['messages']
    assert any('nearly all' in m and '0:01.000' in m and '0:05.000' in m for m in measured['messages'])

def test_hum_is_only_measured_sound_and_gets_an_honest_listening_note(tmp_path):
    voice=write_signal(tmp_path/'hum.wav',.01*np.sin(2*np.pi*50*np.arange(RATE*4)/RATE))
    bed=write_signal(tmp_path/'bed.wav',.1*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    measured,_,_=encode(tmp_path,[track(voice,'speech',24,96),track(bed,'music')])
    assert any('steady tone or noise' in m and '0:01.000' in m for m in measured['messages'])
    # These sentences reach both the file card and the signing screen. Catch
    # clarity claims anywhere in a sentence, including a listening instruction.
    assert not any(re.search(r'\bclear\w*\b|intelligib',m,re.IGNORECASE) or
                   'words can be understood' in m.lower() for m in measured['messages'])
    assert any('audible sound' in m for m in measured['messages'])
    assert all(r['status']!='clear' for r in measured['voice_checks']+measured['speech_checks'])

def test_ordinary_speech_shaped_line_with_gaps_stays_a_listening_decision(tmp_path):
    from lib.trailer_delivery import _sound_ok
    voice=write_signal(tmp_path/'voice.wav',paused_voice('syllable-gaps'))
    bed=write_signal(tmp_path/'bed.wav',.1*np.sin(2*np.pi*211*np.arange(RATE*6)/RATE))
    measured,_,_=encode(tmp_path,[track(voice,'speech',24,72),track(bed,'music')])
    assert _sound_ok(measured),measured['messages']
    assert not any('steady tone or noise' in m for m in measured['messages'])

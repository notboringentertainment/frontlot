"""Both fresh and reopened journeys use the same visible, signed operations."""
import io, math, random, struct, wave, uuid
import pytest
from tests.backlot.test_early_paid_readiness import isolated_standins

def _wav(seconds,kind,seed=4):
    rate=24000; samples=[]; rng=random.Random(seed)
    for n in range(int(seconds*rate)):
        t=n/rate
        value=(rng.uniform(-6000,6000) if kind=='effect' else
               4000*math.sin(2*math.pi*(173 if kind=='speech' else 211)*t))
        samples.append(struct.pack('<h',int(value)))
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(b''.join(samples))
    return buffer.getvalue()



@pytest.mark.parametrize('reload_points',[False,True],ids=['fresh','reopen'])
def test_existing_screens_map_a_fresh_trailer(tmp_path,monkeypatch,isolated_standins,reload_points):
    # The joined journey now supplies looks, pictures and prices on screen.
    # The former scaffold appended Fal twice and bypassed those controls.
    from tests.backlot.test_combined_full_journey import run_visible_journey
    run_visible_journey(tmp_path,monkeypatch,isolated_standins,reload_points=reload_points,
                        prefix='delivery-map-'+('reopen' if reload_points else 'fresh')+'-'+uuid.uuid4().hex[:8])

import json
from lib import audio_workspace as audio,media_spending as spending
from backlot import voice_panel
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http,wav_bytes
from tests.lib.look_lock_helpers import approve_request
import pytest


@pytest.fixture(autouse=True)
def human_terminal(monkeypatch):monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)


def test_local_keep_survives_a_held_charge_and_resolution_rebuilds_its_media_facts(voice_world,monkeypatch):
    root,posts,gets=voice_world
    voice_panel.catalogue(root)
    request=voice_panel.prepare_voice(root,{'line_index':0,'voice':'invented_voice','notes':'','rights_confirmed':True})['request']
    sign(root,'audio_spend',request,request['request_id'])
    def timeout(url,**kwargs):
        assert url=='https://api.elevenlabs.io/v1/text-to-speech/invented_voice'
        posts.append(('timeout',));raise TimeoutError('Invented unknown billing.')
    monkeypatch.setattr('requests.post',timeout)
    assert not voice_panel.send(root,request['request_id'])['success']
    from tools.cost_tracker import nonterminal_reservations
    held,=nonterminal_reservations(root)
    source=root/'assets/invented-local-music.wav';source.write_bytes(wav_bytes())
    take=audio.import_music(root,source,'My invented local recording','I made and own this recording.')
    approve_request(audio.request_decision(root,take['id'],'accept'),root)
    assert audio.take_state(root,take)=='Accepted'
    assert not (root/'checkpoint_assets.json').exists()
    assert not voice_panel.send(root,request['request_id'])['success']
    assert len(posts)==1 and len(gets)==1
    gate=spending.request_resolution(root,held['reservation_id'],False,0,0,'I checked the invented account. Nothing was charged.')
    approve_request(gate,root)
    spending.apply_signed_resolution(root,held['reservation_id'])
    assert nonterminal_reservations(root)==[]
    artifact=json.loads((root/'artifacts/asset_manifest.json').read_text())
    assert [row['path'] for row in artifact['assets']]==[take['path']]
    assert not (root/'checkpoint_assets.json').exists()
    assert not voice_panel.send(root,request['request_id'])['success']
    assert len(posts)==1 and len(gets)==1

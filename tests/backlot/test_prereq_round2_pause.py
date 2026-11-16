"""A pending isolated price draft pauses sends without changing any authority."""
import pytest
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace
from tests.backlot.test_footage_price_settings import footage,audio
from lib import media_settings

@pytest.mark.parametrize('form',['audio','footage'])
def test_waiting_price_draft_blocks_a_signed_footage_send_before_contact(film,provider,form):
    from tools.cost_tracker import load_reservations
    root,shot=film;record=prepare(film);approve(root,record)
    before=(root/'project.yaml').read_bytes()
    draft=media_settings.prepare_audio(root,audio()) if form=='audio' else media_settings.prepare_footage(root,footage())
    assert draft and (root/'project.yaml').read_bytes()==before
    with pytest.raises(ValueError,match='paused'):workspace().execute(root,record['request_id'])
    assert not load_reservations(root)
    assert all(not values for values in provider.values())
    media_settings.discard(root)
    result=workspace().execute(root,record['request_id'])
    assert result and len(provider['posts'])==1


"""A signed used look is available without inventing a sixth stage review."""
import json
import pytest
from tests.lib.test_trimmed_trailer_contract import trimmed,human_terminal
from tests.lib.test_frontlot_location_source import form
from tests.lib.look_lock_helpers import approve_request

def test_trimmed_trailer_can_record_its_individually_signed_location(trimmed):
    from lib import frontlot_location_source as source
    from lib.checkpoint import read_checkpoint,write_checkpoint
    req=source.prepare(trimmed,form())['gate']
    approve_request(req,trimmed)
    source.apply(trimmed,req['request_id'])
    record=read_checkpoint(trimmed.parent,trimmed.name,'look_lock')
    assert record['status']=='in_progress' and not record['human_approved']
    with pytest.raises(ValueError):
        write_checkpoint(trimmed.parent,trimmed.name,'look_lock','completed',record['artifacts'],human_approved=True)
    from backlot.state import load_board_state
    assert [s['label'] for s in load_board_state(trimmed)['stages']]==['Story','Paper cut','Rough cut','Colour and sound','Final file']

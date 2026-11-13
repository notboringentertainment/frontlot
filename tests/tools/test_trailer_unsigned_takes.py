"""An unsigned notebook row cannot settle a trailer take."""
import pytest
from fastapi import HTTPException
from lib import supervised_production as sp
from backlot.state import load_board_state
from backlot.server import _decide_take
from tests.tools.test_trailer_footage_notebook import linked,shot  # noqa: F401


@pytest.mark.parametrize('events',[
    ['selection'],['decline'],['selection','decline'],['decline','selection'],
    ['selection','selection'],['decline','decline'],
])
def test_unsigned_take_history_is_never_authority(linked,events):
    root,spec,_=linked;sid=spec['shot_id']
    sp.prepare(root,spec,user_note='An invented brief.')
    take=sp.attach(root,sid,root/'reference.png',user_note='An invented local reference.')
    for kind in events:
        sp._append(root,sid,{'kind':kind,'take_id':take['take_id'],
            'receipt_id':'handwritten','user_approved':True,'signature':'not-a-signature'})
    inspected=sp.inspect_shot(root,sid)
    assert inspected['takes'][0]['standing']=='waiting'
    assert inspected['declines']==inspected['selections']==[]
    assert [r['take_id'] for r in sp.waiting_takes(root,sid)]==[take['take_id']]
    card=next(s for s in load_board_state(root)['shots'] if s['id']==sid)
    assert card['state']=='waiting' and card['waiting_count']==1
    assert not card['takes'][0]['selected'] and not card['takes'][0]['declined']
    for decision in ['approve','decline']:
        with pytest.raises(HTTPException) as error:_decide_take(root,sid,take['take_id'],decision,'An invented decision.')
        assert error.value.status_code==409
        assert 'signature' in error.value.detail and 'already decided' not in error.value.detail
    assert [r['kind'] for r in sp.history(root,sid) if r['kind'] in ('selection','decline')]==events


@pytest.mark.parametrize('operation',[sp.select,sp.decline],ids=['accept','decline'])
def test_no_unsigned_operation_can_record_a_trailer_decision(linked,operation):
    root,spec,_=linked;sid=spec['shot_id']
    sp.prepare(root,spec,user_note='An invented brief.')
    take=sp.attach(root,sid,root/'reference.png',user_note='An invented local reference.')
    before=sp.history(root,sid)
    with pytest.raises(ValueError,match='signature'):
        operation(root,sid,take['take_id'],user_note='An unsigned decision.')
    assert sp.history(root,sid)==before

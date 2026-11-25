"""The reservation boundary itself refuses unavailable local media tools."""
import pytest
from tools.cost_tracker import reserve_paid_call,load_reservations
from tests.tools._authored_film_helpers import make_verified_project,make_tracker

@pytest.mark.parametrize('kind,hint',[(None,'audio'),(None,'video'),('audio',None),('video',None),('image','video')])
def test_direct_reservation_preflights_before_ledger(tmp_path,monkeypatch,kind,hint):
    root=make_verified_project(tmp_path,monkeypatch,'invented-preflight',budget=10)
    tracker=make_tracker(root,budget=10)
    monkeypatch.setenv('OPENMONTAGE_FFPROBE','/nonexistent/invented-probe')
    estimates=[]
    original=tracker.estimate
    def estimate(*a,**kw):
        estimates.append(a)
        return original(*a,**kw)
    monkeypatch.setattr(tracker,'estimate',estimate)
    with pytest.raises(ValueError,match='ffprobe'):
        reserve_paid_call(tracker,root,tool='invented',endpoint='invented',
            normalized_inputs_hash='a'*64,reserved_usd=1,kind=kind,
            output_hint={'kind':hint} if hint else None)
    assert not estimates and not load_reservations(root)
    assert tracker.budget_reserved_usd==tracker.budget_spent_usd==0

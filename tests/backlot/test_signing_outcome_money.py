"""Signed account review must recover exactly once without another contact."""
import json
import pytest
from tests.backlot.signing_outcome_helpers import (
    project,priced,http,voice_world,film,provider,signing_folder,held,figures,signed_review)


@pytest.mark.parametrize('medium',['footage','audio'])
@pytest.mark.parametrize('charged',[False,True])
def test_signed_outcome_reapplication_is_read_only_and_survives_reopen(request,medium,charged,monkeypatch):
    from lib.media_spending import resolve_charge
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count()
    assert figures(root,ceiling)[0]==0 and figures(root,ceiling)[1]>0
    gate=signed_review(root,reservation,charged);record=gate['approval_record']
    apply=lambda:resolve_charge(root,reservation['reservation_id'],record['charged'],record['cash_usd'],record['credits_usd'],record['note'])
    apply();saved={p.name:p.read_bytes() for p in (root/'cost_log.json',root/'cost-reservations.jsonl')}
    apply()
    assert saved=={p.name:p.read_bytes() for p in (root/'cost_log.json',root/'cost-reservations.jsonl')}
    expected=[.003,0,round(ceiling-.003,4)] if charged else [0,0,ceiling]
    assert figures(root,ceiling)==pytest.approx(expected)
    assert figures(root,ceiling,fresh=True)==pytest.approx(expected)
    assert count()==before and not (root/'checkpoint_assets.json').exists()


@pytest.mark.parametrize('medium',['footage','audio'])
@pytest.mark.parametrize('field,value',[('charged',True),('cash_usd',.1),('credits_usd',.1),('note','A different account claim.')])
def test_unsigned_or_altered_outcome_cannot_change_money(request,medium,field,value,monkeypatch):
    from lib.media_spending import resolve_charge,request_resolution
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();amounts=figures(root,ceiling)
    proposed=request_resolution(root,reservation['reservation_id'],False,0,0,'I checked the invented service account.')['approval_record']
    with pytest.raises(ValueError):resolve_charge(root,reservation['reservation_id'],False,0,0,proposed['note'])
    signed_review(root,reservation);altered={**proposed,field:value}
    with pytest.raises(ValueError):resolve_charge(root,reservation['reservation_id'],altered['charged'],altered['cash_usd'],altered['credits_usd'],altered['note'])
    assert figures(root,ceiling,fresh=True)==pytest.approx(amounts) and count()==before

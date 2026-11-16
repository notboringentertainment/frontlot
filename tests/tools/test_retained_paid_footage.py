"""A disappearing local inspector never discards a paid download."""
import pytest
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace,clip_bytes

@pytest.mark.parametrize('failure',['missing','crash','too-long','no-sound','disk-full','disk-full-ledger'])
def test_local_verification_failure_keeps_paid_bytes_and_hold(film,provider,monkeypatch,failure):
    from tools.video import _shared
    from tools.cost_tracker import CostTracker,IndeterminatePaidCallError,load_reservations
    from lib import supervised_production as shots
    root,shot=film;expected=clip_bytes();record=prepare(film);approve(root,record)
    prior=(root/'checkpoint_assets.json').read_bytes() if (root/'checkpoint_assets.json').exists() else None
    if failure=='too-long':
        monkeypatch.setattr(_shared,'verify_video_file',lambda *a,**k:{'duration_seconds':99})
    elif failure in ('disk-full','disk-full-ledger'):
        def full(*a,**k):raise OSError(28,'No space left on device')
        monkeypatch.setattr('lib.receipts.stage_generation_wal',full)
        if failure=='disk-full-ledger':
            from tools import cost_tracker
            reconcile=cost_tracker.reconcile_paid_call
            monkeypatch.setattr(cost_tracker,'reconcile_paid_call',full)
    else:
        def gone(*a,**k):
            if failure=='missing':raise FileNotFoundError('ffprobe disappeared after preflight')
            if failure=='crash':raise OSError('ffprobe crashed after preflight')
            raise _shared.VideoVerificationError('The requested sound track is missing.')
        monkeypatch.setattr(_shared,'verify_video_file',gone)
    with pytest.raises(ValueError,match='downloaded bytes were kept'):workspace().execute(root,record['request_id'])
    if failure=='disk-full-ledger':monkeypatch.setattr(cost_tracker,'reconcile_paid_call',reconcile)
    retained=[p for p in (root/'.staging').iterdir() if p.is_file() and p.read_bytes()==expected]
    assert len(retained)==1,'The paid download was discarded by a local verification failure.'
    reservation=next(iter(load_reservations(root).values()))
    assert reservation['retained_downloads']==[retained[0].relative_to(root).as_posix()]
    tracker=CostTracker(budget_total_usd=50,reserve_pct=0,cost_log_path=root/'cost_log.json')
    assert tracker.budget_reserved_usd>0 and tracker.budget_spent_usd==0
    assert not [t for t in shots.history(root,shot) if t.get('kind')=='take']
    assert ((root/'checkpoint_assets.json').read_bytes() if (root/'checkpoint_assets.json').exists() else None)==prior
    with pytest.raises(IndeterminatePaidCallError):workspace().execute(root,record['request_id'])
    assert len(provider['posts'])==len(provider['downloads'])==1

    assert retained[0].read_bytes()==expected
    # Account review can locate the quarantine but cannot make it a take.
    from lib.media_spending import request_resolution,apply_signed_resolution
    from backlot.state import _render_account_review
    from tests.lib import look_lock_helpers as looks
    gate=request_resolution(root,reservation['reservation_id'],False,0,0,'The invented account showed no charge.')
    assert 'downloaded bytes were kept' in _render_account_review(root,gate)['footage_resolution']['retained_note']
    looks.approve_request(gate,root)
    apply_signed_resolution(root,reservation['reservation_id'])
    apply_signed_resolution(root,reservation['reservation_id'])
    reopened=CostTracker(budget_total_usd=50,reserve_pct=0,cost_log_path=root/'cost_log.json')
    assert reopened.budget_spent_usd==reopened.budget_reserved_usd==0
    assert retained[0].read_bytes()==expected
    assert len(provider['posts'])==len(provider['downloads'])==1

def test_late_local_failure_keeps_recorded_charge_and_existing_result(film,provider,monkeypatch):
    from tools.video.kling_reference_video import KlingReferenceVideo
    from tools.cost_tracker import CostTracker
    from lib import supervised_production as shots
    root,shot=film;record=prepare(film);approve(root,record)
    original=KlingReferenceVideo._execute
    def after_filing(self,inputs):
        result=original(self,inputs)
        assert result.success,result.error
        raise OSError('Invented local result presentation failure after completed filing.')
    monkeypatch.setattr(KlingReferenceVideo,'_execute',after_filing)
    with pytest.raises(ValueError,match='Any unresolved charge remains held'):
        workspace().execute(root,record['request_id'])
    tracker=CostTracker(budget_total_usd=50,reserve_pct=0,cost_log_path=root/'cost_log.json')
    assert tracker.budget_spent_usd==pytest.approx(.5) and tracker.budget_reserved_usd==0
    assert not [r for r in shots.history(root,shot) if r.get('kind')=='take']
    take=workspace().execute(root,record['request_id'])
    assert (root/take['path']).read_bytes()==clip_bytes()
    assert len(provider['posts'])==len(provider['downloads'])==1
    assert not (root/'checkpoint_assets.json').exists()

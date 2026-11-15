"""Document the pre-existing reader race without changing spending operations."""
import pytest
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace
from tests.backlot.signing_outcome_helpers import held,signed_review,figures


@pytest.mark.xfail(raises=AssertionError,reason='Also present on authored-film 08cbfc2: the reservation snapshot can precede job completion.')
def test_account_status_uses_a_consistent_job_and_reservation_snapshot(request,monkeypatch):
    from backlot import footage_controls as controls
    from lib.media_spending import apply_signed_resolution
    root,reservation,counts,ceiling=held(request,'footage',monkeypatch)
    gate=signed_review(root,reservation,charged=True)
    key=(str(root),gate['approval_record']['request']['request_id'])
    jobs={key:{'running':True,'error':None}};before=counts();original=controls._tracker
    def finish_during_read(*args):
        if jobs[key]['running']:
            apply_signed_resolution(root,reservation['reservation_id']);jobs[key]['running']=False
        return original(*args)
    monkeypatch.setattr(controls,'_tracker',finish_during_read)
    snapshot=controls.inspect(root,jobs)
    assert figures(root,ceiling,fresh=True)[1]==0 and counts()==before
    assert snapshot['shots'][0]['status']!='Charge needs your review'

"""Unknown footage bills stay held until the writer signs their outcome."""
import hashlib
import pytest
import yaml
from tests.tools.test_signed_footage import film, provider, prepare, approve, workspace, sign, looks
from tests.tools.test_audio_governance import project, Transport, prepare as audio_prepare, approve as audio_approve, run as audio_run
from lib.media_spending import _tracker, request_resolution, resolve_charge
from lib.project_config import load_current_verified_project_config
from tools.cost_tracker import load_reservations, resume_check, IndeterminatePaidCallError


@pytest.mark.parametrize('fault',['timeout','partial','over-bound','too-long','download'])
@pytest.mark.parametrize('charged',[False,True])
def test_unknown_footage_is_held_until_signed_review(film,provider,monkeypatch,fault,charged):
    from tools.video import _shared
    root,_=film;record=prepare(film);approve(root,record)
    def fail(*a,**k):raise TimeoutError('Invented unknown outcome.')
    if fault=='timeout':monkeypatch.setattr(_shared,'fal_queue_wait',fail)
    if fault in ('partial','over-bound'):
        result={'video':{'url':'https://simulation.invalid/clip.mp4'},'cash_usd':.6 if fault=='over-bound' else .4}
        if fault=='over-bound':result['credits_usd']=0
        monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:result)
    if fault=='too-long':monkeypatch.setattr(_shared,'verify_video_file',lambda *a,**k:{'duration_seconds':6})
    if fault=='download':monkeypatch.setattr(_shared,'fal_download',fail)
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    def figures():
        tracker=_tracker(root,load_current_verified_project_config(root))
        return tracker.budget_spent_usd,tracker.budget_reserved_usd,tracker.budget_remaining_usd
    assert figures()==(0,.5,49.5)
    with pytest.raises(IndeterminatePaidCallError):resume_check(root)
    reservation=next(iter(load_reservations(root).values()))
    assert reservation['state']=='pending_billing'
    amount=.4 if charged else 0
    note='Checked the invented service account.'
    request=request_resolution(root,reservation['reservation_id'],charged,amount,0,note)
    with pytest.raises(ValueError,match='signed approval'):resolve_charge(root,reservation['reservation_id'],charged,amount,0,note)
    assert figures()==(0,.5,49.5)
    looks.approve_request(request,root)
    assert figures()==(0,.5,49.5)
    resolve_charge(root,reservation['reservation_id'],charged,amount,0,note)
    assert figures()==(amount,0,50-amount)
    with pytest.raises(ValueError,match='already been resolved'):resolve_charge(root,reservation['reservation_id'],charged,amount,0,note)
    assert len(provider['posts'])==1


def test_audio_rechecks_signed_settings_after_hold_before_contact(project,monkeypatch):
    from tools.audio import _governed as audio
    record=audio_prepare(project);audio_approve(project,record);transport=Transport()
    original=audio.reserve_paid_call
    def change(*a,**kw):
        reservation=original(*a,**kw)
        data=yaml.safe_load((project/'project.yaml').read_text());data['budget_usd_cap']=0
        raw=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(raw)
        from tests.tools.test_audio_governance import sign
        sign(project,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
        return reservation
    monkeypatch.setattr(audio,'reserve_paid_call',change)
    with pytest.raises(ValueError,match='settings changed'):audio_run(project,record,transport)
    assert transport.calls==transport.downloads==0
    tracker=_tracker(project,load_current_verified_project_config(project))
    assert tracker.budget_reserved_usd==1 and tracker.budget_spent_usd==0

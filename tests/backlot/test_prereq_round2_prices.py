"""Unsigned price drafts cannot become current settings or trap the next form."""
import pytest
from lib import media_settings,receipts
from lib.project_config import load_current_verified_project_config
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request,decline_request
from tests.backlot.test_footage_price_settings import audio,footage

def prepare(root,kind,change=False):
    if kind=='audio':
        data=audio()
        if change:data['dollars_per_credit']=.0003
        return media_settings.prepare_audio(root,data)
    data=footage()
    if change:data['cash_usd_per_second']=.12
    return media_settings.prepare_footage(root,data)

@pytest.mark.parametrize('kind',['audio','footage'])
def test_unsigned_draft_never_changes_the_current_file(root,kind):
    approve_request(prepare(root,'audio'),root)
    before=(root/'project.yaml').read_bytes()
    request=prepare(root,kind,True)
    assert (root/'project.yaml').read_bytes()==before,'Preparing a draft replaced the signed settings.'
    assert load_current_verified_project_config(root).digest==__import__('hashlib').sha256(before).hexdigest()
    assert request['approval_record']['config_sha256']!=__import__('hashlib').sha256(before).hexdigest()
    assert not (root/'cost_log.json').exists()

@pytest.mark.parametrize('kind',['audio','footage'])
def test_a_signed_configuration_does_not_prevent_correcting_a_draft(root,kind):
    approve_request(prepare(root,'audio'),root)
    first=prepare(root,kind);second=prepare(root,kind,True)
    assert first['request_id']!=second['request_id']
    assert (root/'.gate-requests'/'abandoned'/(first['request_id']+'.json')).is_file()
    approve_request(second,root)
    current=load_current_verified_project_config(root).data
    assert (current['audio_price_basis']['dollars_per_credit']['value']==.0003 if kind=='audio'
        else current['footage_price_basis']['cash_usd_per_second']==.12)

def test_declined_audio_figures_cannot_enter_a_footage_signature(root):
    declined=prepare(root,'audio');decline_request(declined,root)
    approve_request(prepare(root,'footage'),root)
    data=load_current_verified_project_config(root).data
    assert not data.get('audio_price_basis'),'A declined audio draft became approved through the footage form.'
    assert not any(p['provider']=='elevenlabs' and p['content_classes'] for p in data['provider_egress'])

@pytest.mark.parametrize('signed',[False,True])
def test_discard_retires_only_the_unsigned_request_and_unblocks_the_other_form(root,signed):
    if signed:approve_request(prepare(root,'audio'),root)
    before=(root/'project.yaml').read_bytes();request=prepare(root,'footage')
    assert callable(getattr(media_settings,'discard',None)),'There is no shared price-draft discard operation.'
    media_settings.discard(root,scope='footage:project_config')
    assert not (root/'.gate-requests'/(request['request_id']+'.json')).exists()
    assert (root/'project.yaml').read_bytes()==before
    assert prepare(root,'audio')
    assert len(receipts.verified_approvals(root,'config'))==int(signed)

@pytest.mark.parametrize('failure',['apply','archive'])
def test_signed_settings_recover_without_a_second_signature(root,monkeypatch,failure):
    from scripts import gate_approve
    from lib import gates
    request=prepare(root,'footage');before=(root/'project.yaml').read_bytes()
    target=media_settings if failure=='apply' else gate_approve
    name='apply_signed' if failure=='apply' else '_complete_done'
    original=getattr(target,name)
    def fail(*a,**kw):raise OSError('Invented interrupted settings filing.')
    monkeypatch.setattr(target,name,fail)
    with pytest.raises(OSError,match='interrupted'):approve_request(request,root)
    rows=receipts.verified_approvals(root,'config');assert len(rows)==1
    if failure=='apply':assert (root/'project.yaml').read_bytes()==before
    with pytest.raises(ValueError,match='signed'):media_settings.discard(root)
    with pytest.raises(ValueError,match='paused'):media_settings.require_no_draft(root)
    monkeypatch.setattr(target,name,original)
    monkeypatch.setattr(gates,'mint_gate_token',lambda *a,**kw:pytest.fail('Recovery must not ask for another signature.'))
    assert approve_request(request,root)['receipt_id']==rows[0]['receipt_id']
    assert receipts.verified_approvals(root,'config')==rows
    assert load_current_verified_project_config(root).data['footage_price_basis']
    media_settings.require_no_draft(root)

def test_omitting_effect_prices_removes_the_corresponding_sending_permission(root):
    approve_request(prepare(root,'audio'),root)
    data=audio();data.pop('effect_credits_per_second')
    approve_request(media_settings.prepare_audio(root,data),root)
    settings=load_current_verified_project_config(root).data
    assert 'effect_credits_per_second' not in settings['audio_price_basis']
    assert 'effect_prompt' not in next(p['content_classes'] for p in settings['provider_egress'] if p['provider']=='elevenlabs')

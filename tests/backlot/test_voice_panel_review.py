"""Review regressions use completed signed takes and counted service boundaries."""
import hashlib,json
import pytest,yaml
from tests.backlot.test_voice_panel import voice_world,options
from tests.tools.test_audio_governance import project,sign
from tests.tools.test_audio_half_transport import priced,http

def price_data():return dict(dollars_per_credit=.0002,speech_credits_per_character=1,music_credits_per_minute=1200,voice_multiplier_ceiling=2,expires_at=4102444800)

def test_recorded_credit_total_comes_from_a_real_completed_take(voice_world,monkeypatch):
    from backlot import voice_panel as panel
    from lib.receipts import verified_generation_receipts
    import requests
    root,posts,gets=voice_world;original=requests.post
    def post(*a,**kw):
        result=original(*a,**kw);result.headers={'character-cost':'29','request-id':'invented-charged-job'};return result
    monkeypatch.setattr(requests,'post',post)
    panel.catalogue(root);record=panel.prepare_voice(root,options())['request'];sign(root,'audio_spend',record,record['request_id'])
    assert panel.send(root,record['request_id'])['success']
    receipt=verified_generation_receipts(root)[0]
    assert receipt['credits_usd']==.0058 and receipt['cash_usd']==0
    assert 'audio_charge' not in receipt
    result=panel.snapshot(root)['budget']
    assert result['audio_credits']==.0058 and result['audio_cash']==0 and result['spent']==.0058
    assert panel.send(root,record['request_id'])['success'] and len(posts)==1 and len(gets)==1
    assert panel.snapshot(root)['budget']['audio_credits']==.0058

@pytest.mark.parametrize('mode',['unsigned','no-basis'])
def test_loading_voices_before_price_signature_is_a_specific_refusal(voice_world,mode,monkeypatch):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world
    if mode=='unsigned':
        # A genuine first draft has no prior signed account consent to read.
        from lib.trailer_edit import create_trailer
        from tests.lib.look_lock_helpers import approve_request
        monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
        root=create_trailer(root.parent,'Invented first price consent',28,10)
        approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
        panel.prepare_prices(root,price_data())
    else:
        data=yaml.safe_load((root/'project.yaml').read_text());data.pop('audio_price_basis')
        raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw);sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError,match='Sign the price settings first'):panel.catalogue(root)
    assert posts==[] and gets==[]

def test_restore_immediately_after_signing_does_not_search_for_an_archive(voice_world,monkeypatch):
    from backlot import voice_panel as panel
    from lib.receipts import verified_approvals
    from tests.lib.look_lock_helpers import approve_request
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root,posts,gets=voice_world;req=panel.prepare_prices(root,price_data());approve_request(req,root)
    path=root/'project.yaml';before=path.read_bytes();stamp=path.stat().st_mtime_ns;count=len(verified_approvals(root,'config'))
    assert 'No unsigned price draft is waiting' in panel.restore_prices(root)['message']
    assert path.read_bytes()==before and path.stat().st_mtime_ns==stamp and len(verified_approvals(root,'config'))==count
    assert posts==[] and gets==[]

def test_snapshot_exposes_only_verified_price_figures_and_connection_state(voice_world,monkeypatch):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;snap=panel.snapshot(root)
    assert snap['price_basis']['dollars_per_credit']['value']==.0002 and snap['price_settings_signed'] is True
    assert snap['account']['configured'] is True and 'invented-only' not in json.dumps(snap)
    monkeypatch.delenv('ELEVENLABS_API_KEY');snap=panel.snapshot(root)
    assert snap['account']['configured'] is False and 'not connected' in snap['account']['message'] and 'restart' in snap['account']['message']
    with pytest.raises(ValueError,match='not connected'):panel.prepare_voice(root,options())
    assert not panel.send(root,'not-a-request')['success']
    before=panel.snapshot(root)['price_basis'];req=panel.prepare_prices(root,price_data());snap=panel.snapshot(root)
    assert snap['price_basis']==before and snap['price_settings_signed'] is True
    assert snap['purchases_paused'] is True and 'paused' in snap['message']
    assert posts==[] and gets==[]

def test_waiting_price_change_keeps_existing_consent_but_pauses_purchases(voice_world):
    from backlot import voice_panel as panel
    root,posts,gets=voice_world;before=panel.snapshot(root)['price_basis']
    panel.prepare_prices(root,price_data())
    snap=panel.snapshot(root)
    assert snap['price_settings_signed'] and snap['price_basis']==before and snap['purchases_paused']
    # Reading the catalogue still uses unchanged, already-signed consent.
    panel.catalogue(root)
    with pytest.raises(ValueError,match='paused'):panel.prepare_voice(root,options())
    assert posts==[] and gets==[1]
    panel.restore_prices(root)
    assert not panel.snapshot(root)['purchases_paused']
    assert panel.prepare_voice(root,options())['request'] and posts==[]

"""Both price forms preserve the one settings record and show both sets."""
import importlib.util,json,time
import pytest
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request

def audio():
    return {'dollars_per_credit':.0002,'speech_credits_per_character':1,'music_credits_per_minute':1200,
        'voice_multiplier_ceiling':2,'effect_credits_per_second':40,'expires_at':time.time()+3600}
def footage():
    return {'cash_usd_per_second':.112,'credits_usd_per_second':0,'expires_at':time.time()+3600,
        'allow_prompts':True,'allow_reference_images':True}
def settings():
    assert importlib.util.find_spec('lib.media_settings'),'The shared production-price operation is missing.'
    from lib import media_settings
    return media_settings

@pytest.mark.parametrize('order',[('audio','footage'),('footage','audio')])
def test_each_signing_order_preserves_both_figures_permissions_and_one_ceiling(root,order):
    from backlot.voice_panel import prepare_prices as audio_prepare
    from lib.project_config import load_current_verified_project_config
    from backlot.state import build_gate_detail
    m=settings();requests=[]
    for kind in order:
        req=audio_prepare(root,audio()) if kind=='audio' else m.prepare_footage(root,footage())
        detail=build_gate_detail(root,req['request_id'])
        if requests:
            assert detail['packet']['audio_prices'] and detail['packet']['footage_prices']
            assert detail['packet']['production_ceiling']==10
        approve_request(req,root);requests.append(req)
    data=load_current_verified_project_config(root).data
    assert data['budget_usd_cap']==10
    assert data['audio_price_basis']['dollars_per_credit']['value']==.0002
    assert data['footage_price_basis']['cash_usd_per_second']==.112
    assert data['footage_price_basis']['credits_usd_per_second']==0
    permissions={p['provider']:set(p['content_classes']) for p in data['provider_egress']}
    assert permissions['fal']=={'prompts','reference_images'}
    assert {'speech_text','music_prompt','effect_prompt'}<=permissions['elevenlabs']
    assert not (root/'cost_log.json').exists()

def test_pending_first_form_cannot_be_overwritten_by_the_other(root):
    from backlot.voice_panel import prepare_prices
    m=settings();req=prepare_prices(root,audio());before=(root/'project.yaml').read_bytes()
    with pytest.raises(ValueError,match='sign|finish|review|pending'):m.prepare_footage(root,footage())
    assert (root/'project.yaml').read_bytes()==before
    approve_request(req,root)
    next_req=m.prepare_footage(root,footage());assert next_req

@pytest.mark.parametrize('fault',['missing','expired','negative','nan','zero-total','no-pictures','no-prompts'])
def test_refused_rates_write_no_settings_or_charge(root,fault):
    m=settings();data=footage();before=(root/'project.yaml').read_bytes()
    if fault=='missing':data.pop('cash_usd_per_second')
    elif fault=='expired':data['expires_at']=time.time()-1
    elif fault=='negative':data['cash_usd_per_second']=-1
    elif fault=='nan':data['cash_usd_per_second']=float('nan')
    elif fault=='zero-total':data['cash_usd_per_second']=0
    elif fault=='no-pictures':data['allow_reference_images']=False
    else:data['allow_prompts']=False
    with pytest.raises(ValueError):m.prepare_footage(root,data)
    assert (root/'project.yaml').read_bytes()==before
    assert not (root/'cost_log.json').exists()

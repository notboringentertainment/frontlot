"""An obsolete unsigned purchase can be retired without buying or approving."""
import copy
import hashlib
import yaml
import pytest
from tests.backlot.test_footage_controls import board,post,inspect
from tests.tools.test_signed_footage import film,provider,sign,workspace,looks
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options
from tests.lib.test_cutting_signed import signed_cut


def change_config(root,change):
    data=yaml.safe_load((root/'project.yaml').read_text());change(data)
    raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')


@pytest.mark.parametrize('change',['price','remaining'])
def test_obsolete_clip_can_be_set_aside_and_replaced(board,provider,change):
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    if change=='price':change_config(root,lambda c:c['footage_price_basis'].update(cash_usd_per_second=.12))
    else:
        from lib.media_spending import _tracker
        from lib.project_config import load_current_verified_project_config
        tracker=_tracker(root,load_current_verified_project_config(root))
        entry=tracker.estimate('invented sound','counted fixture',.1)
        tracker.reconcile(entry,.1)
    row=inspect(board)['shots'][0]['requests'][0]
    assert row.get('signing_refusal'), 'The shot must explain why signing stopped.'
    from backlot.state import _render_footage
    packet=_render_footage(root,gate)
    assert packet['packet_error'] and packet['error']==row['signing_refusal']
    result=post(board,'set-aside',request_id=gate['entity_id'])
    assert result.status_code==200,result.text
    assert not inspect(board)['shots'][0]['requests'][0]['gate_id']
    fresh=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    assert fresh.status_code==200,fresh.text
    assert fresh.json()['gate']['entity_id']!=gate['entity_id']
    assert all(not provider[k] for k in provider)
    assert not list((root/'.gate-requests'/'done').glob('*'+gate['request_id']+'*'))


@pytest.mark.parametrize('kind',['speech','music','effect'])
def test_obsolete_audio_gets_a_fresh_card_without_contact(voice_world,kind):
    from lib import audio_workspace as audio
    from backlot import voice_panel as panel
    root,posts,gets=voice_world
    # Extend the existing ElevenLabs entry; providers remain unique.
    def allow(c):
        c['audio_price_basis']['effect_credits_per_second']={'value':40,'set_at':1,'expires_at':4102444800}
        next(e for e in c['provider_egress'] if e['provider']=='elevenlabs')['content_classes']=['speech_text','music_prompt','effect_prompt']
    change_config(root,allow)
    if kind=='speech':
        panel.catalogue(root);record=panel.prepare_voice(root,options())['request']
    else:
        identity=audio.save_music_brief(root,'An invented quiet score.',3) if kind=='music' else audio.save_effect_brief(root,'An invented tap.',1,False,0,0)
        record=audio.prepare_request(root,kind,'music_v1' if kind=='music' else 'eleven_text_to_sound_v3',brief_id=identity)
    change_config(root,lambda c:c.update(wall_time_minutes=61))
    row=next(r for r in panel.snapshot(root)['requests'] if r['record']['request_id']==record['request_id'])
    assert row.get('signing_refusal') and row.get('replaceable')
    fresh=panel.replace_request(root,record['request_id'])
    assert fresh['request']['request_id']!=record['request_id']
    assert fresh['request']['prompt']==record['prompt']
    assert not posts


def test_unchanged_cut_keeps_revision_and_real_changes_do_not(signed_cut):
    from lib import trailer_edit
    from lib import trailer_delivery as delivery
    from lib.cutting_view import save_cut,editable
    root,provider,posts,timeline,take=signed_cut
    looks.approve_request(delivery.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256']),root)
    before=trailer_edit.read_revision(root)
    saved=save_cut(root,editable(before['timeline']),before['edit_revision_sha256'])
    assert saved==before
    assert delivery.rough_state(root)=='Signed by you'
    changed=copy.deepcopy(editable(before['timeline']));changed['audio'][0]['level_db']=-7
    revised=save_cut(root,changed,before['edit_revision_sha256'])
    assert revised['edit_revision_sha256']!=before['edit_revision_sha256']
    assert len(revised['revision_history'])==len(before['revision_history'])+1
    assert delivery.rough_state(root)=='Needs a fresh look'
    assert len(provider['posts'])==3 and len(posts)==1


def test_buying_sound_retires_waiting_clip_without_losing_the_shot(signed_cut):
    from backlot.footage_controls import create,inspect
    from lib import audio_workspace as audio
    from backlot.voice_panel import send
    from lib.spending_requests import set_aside
    root,provider,posts,timeline,take=signed_cut;shot=timeline['picture'][0]['shot_id']
    gate=create(root,{'shot_id':shot,'model':workspace().MODEL,'duration_seconds':5})['gate']
    req=audio.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented')
    sign(root,'audio_spend',req,req['request_id'])
    assert send(root,req['request_id'])['success']
    row=inspect(root,{})['shots'][0]['requests'][-1]
    assert 'remaining ceiling changed' in row['signing_refusal']
    set_aside(root,'footage_spend',gate['entity_id'])
    fresh=create(root,{'shot_id':shot,'model':workspace().MODEL,'duration_seconds':5})['gate']
    assert fresh['entity_id']!=gate['entity_id']
    assert len(provider['posts'])==3 and len(posts)==2


def test_second_waiting_shot_can_recover_after_first_is_bought(board,provider):
    from lib import trailer_edit
    _,root,first=board
    old=trailer_edit.read_revision(root);paper=copy.deepcopy(old['paper_cut'])
    paper['shots'][0]['duration_frames']=48
    second=copy.deepcopy(paper['shots'][0]);second['id']='second-shot';paper['shots'].append(second)
    trailer_edit.save_revision(root,paper,old['edit_revision_sha256'])
    gates=[post(board,'create',shot_id=s,model=workspace().MODEL,duration_seconds=5).json()['gate'] for s in [first,'second-shot']]
    looks.approve_request(gates[0],root);workspace().execute(root,gates[0]['entity_id'])
    row=inspect(board)['shots'][1]['requests'][0]
    assert 'remaining ceiling changed' in row['signing_refusal']
    assert post(board,'set-aside',request_id=gates[1]['entity_id']).status_code==200
    fresh=post(board,'create',shot_id='second-shot',model=workspace().MODEL,duration_seconds=5)
    assert fresh.status_code==200,fresh.text
    assert fresh.json()['gate']['entity_id']!=gates[1]['entity_id']
    assert all(len(provider[k])==1 for k in provider)


def test_signed_card_cannot_be_set_aside(board,provider):
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    looks.approve_request(gate,root)
    result=post(board,'set-aside',request_id=gate['entity_id'])
    assert result.status_code==400 and 'was signed' in result.json()['detail']
    assert all(not provider[k] for k in provider)


def test_settled_footage_drops_old_held_message(board,provider,monkeypatch):
    import time
    from tools.video import _shared
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    looks.approve_request(gate,root)
    def timeout(*a,**k):provider['waits'].append(1);raise TimeoutError()
    monkeypatch.setattr(_shared,'fal_queue_wait',timeout)
    post(board,'signed',gate_id=gate['request_id'])
    for _ in range(100):
        row=inspect(board)['shots'][0]['requests'][0]
        if row['error']:break
        time.sleep(.05)
    assert 'money is held' in row['error']
    rid=row['reservation']['reservation_id']
    review=post(board,'resolve',reservation_id=rid,charged=False,cash=0,credits=0,note='Checked the simulated account.').json()['gate']
    looks.approve_request(review,root)
    assert post(board,'signed',gate_id=review['request_id']).status_code==200
    settled=inspect(board)['shots'][0]['requests'][0]
    assert not settled['error'] and settled['reservation']['state']=='failed'
    assert len(provider['posts'])==len(provider['uploads'])==len(provider['waits'])==1
    assert not provider['downloads']


def test_overcharge_names_the_reported_charge(board,provider,monkeypatch):
    from tools.video import _shared
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    looks.approve_request(gate,root)
    def over(*a,**k):provider['waits'].append(1);return {'video':{'url':'https://simulation.invalid/clip.mp4'},'cash_usd':.56,'credits_usd':0}
    monkeypatch.setattr(_shared,'fal_queue_wait',over)
    with pytest.raises(ValueError,match='reported a charge above the signed maximum'):
        workspace().execute(root,gate['entity_id'])
    from tools.cost_tracker import nonterminal_reservations
    assert len(nonterminal_reservations(root))==1
    assert len(provider['posts'])==len(provider['waits'])==len(provider['uploads'])==1 and not provider['downloads']


def test_signature_and_set_aside_cannot_both_win(board,provider):
    from concurrent.futures import ThreadPoolExecutor
    from lib.spending_requests import set_aside
    from lib.run_common import request_state
    from scripts import gate_approve as ga
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    shown=ga.construct(root,gate)
    def sign_it():
        from lib import gates
        try:
            with gates.handler_context():ga._decide(gate,root,shown=shown,note=None)
            return True
        except Exception:return False
    def retire():
        try:set_aside(root,'footage_spend',gate['entity_id']);return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as workers:
        a=workers.submit(sign_it);b=workers.submit(retire);signed,retired=a.result(),b.result()
    assert signed != retired
    assert request_state(root,gate['request_id'])==('done' if signed else 'abandoned')
    assert all(not provider[k] for k in provider)


def test_sound_rows_name_kind_and_bound(voice_world):
    from lib import audio_workspace as audio
    from backlot.state import _gate_rows
    root,posts,gets=voice_world
    def settings(c):
        c['audio_price_basis']['effect_credits_per_second']={'value':40,'set_at':1,'expires_at':4102444800}
        next(e for e in c['provider_egress'] if e['provider']=='elevenlabs')['content_classes']=['speech_text','music_prompt','effect_prompt']
    change_config(root,settings)
    records=[audio.prepare_request(root,'speech','eleven_multilingual_v2',line_index=0,voice='invented'),
             audio.prepare_request(root,'music','music_v1',brief_id=audio.save_music_brief(root,'An invented score.',3)),
             audio.prepare_request(root,'effect','eleven_text_to_sound_v3',brief_id=audio.save_effect_brief(root,'An invented latch.',1,False,0,0))]
    rows=[r for r in _gate_rows(root,root.name)['requests'] if r['kind']=='audio_spend']
    for record,label in zip(records,['Spoken line','Instrumental music','Sound effect']):
        row=next(r for r in rows if r['entity_id']==record['request_id'])
        assert row['purchase_label']==f"{label} · highest charge ${record['max_cash_usd']+record['max_credits_usd']:.4f}"
        from backlot.state import _render_audio_spend
        from lib.run_common import read_request
        packet=_render_audio_spend(root,read_request(root,row['request_id'])[1])
        assert packet['record']==record and not packet.get('packet_error')
    assert not posts and not gets


def test_readiness_does_not_replay_a_live_call(board,provider,monkeypatch):
    from lib import footage_requests
    _,root,shot=board
    post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    replays=[]
    monkeypatch.setattr(footage_requests,'resume_check',lambda *a:replays.append(1))
    assert not inspect(board)['shots'][0]['requests'][0]['signing_refusal']
    assert not replays and all(not provider[k] for k in provider)

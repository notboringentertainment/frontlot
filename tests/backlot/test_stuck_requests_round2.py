import threading
import pytest
from tests.backlot.test_stuck_requests import board,film,provider,voice_world,project,priced,http,change_config
from tests.backlot.test_footage_controls import post,inspect
from tests.tools.test_signed_footage import workspace,looks
from tests.backlot.test_voice_panel import options

def test_duplicate_waiting_clip_reuses_the_existing_card(board,provider):
    _,root,shot=board
    first=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    second=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=6).json()['gate']
    assert second['request_id']==first['request_id']
    assert len(inspect(board)['shots'][0]['requests'])==1
    assert all(not v for v in provider.values())

def test_price_draft_cannot_retire_a_sound_card(voice_world):
    from backlot import voice_panel as p
    from lib.run_common import read_request
    root,posts,gets=voice_world;p.catalogue(root)
    made=p.prepare_voice(root,options());identity=made['request']['request_id'];gate=made['gate_request_id']
    p.prepare_prices(root,{'dollars_per_credit':.0002,'speech_credits_per_character':1,'music_credits_per_minute':1200,'voice_multiplier_ceiling':2,'expires_at':4102444800})
    row=next(r for r in p.snapshot(root)['requests'] if r['record']['request_id']==identity)
    assert row.get('waiting_reason') and not row['replaceable'] and not row['signing_refusal']
    with pytest.raises(ValueError,match='paused'):p.replace_request(root,identity)
    assert read_request(root,gate)[0]=='pending' and not posts

def test_active_clip_is_waiting_not_an_unresolved_charge(board,provider,monkeypatch):
    from tools.video import _shared
    from lib import trailer_edit
    import copy
    _,root,shot=board
    old=trailer_edit.read_revision(root);paper=copy.deepcopy(old['paper_cut']);paper['shots'][0]['duration_frames']=48
    other=copy.deepcopy(paper['shots'][0]);other['id']='other-shot';paper['shots'].append(other)
    trailer_edit.save_revision(root,paper,old['edit_revision_sha256'])
    gates=[post(board,'create',shot_id=s,model=workspace().MODEL,duration_seconds=5).json()['gate'] for s in [shot,'other-shot']]
    entered=threading.Event();release=threading.Event();original=_shared.fal_queue_wait;errors=[]
    def wait(*a,**k):entered.set();assert release.wait(30);return original(*a,**k)
    monkeypatch.setattr(_shared,'fal_queue_wait',wait);looks.approve_request(gates[0],root)
    def run():
        try:workspace().execute(root,gates[0]['entity_id'])
        except Exception as e:errors.append(e)
    thread=threading.Thread(target=run);thread.start()
    try:
        assert entered.wait(10)
        row=inspect(board)['shots'][1]['requests'][0]
        assert 'being made' in (row.get('waiting_reason') or '')
        assert not row['signing_refusal']
        from backlot.state import _render_footage
        assert 'being made' in _render_footage(root,gates[1])['error']
        assert len(provider['posts'])==1
    finally:release.set();thread.join(30)
    assert not errors and not thread.is_alive()

def test_needs_you_marks_old_cards_and_names_the_shot(board,provider):
    from backlot.state import _gate_rows
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    change_config(root,lambda c:c['footage_price_basis'].update(cash_usd_per_second=.2))
    row=next(r for r in _gate_rows(root,root.name,readiness=True)['requests'] if r['request_id']==gate['request_id'])
    assert 'Shot 1' in row['purchase_label'] and row['signing_refusal']
    assert all(not v for v in provider.values())

def test_sound_signing_packet_explains_an_active_call(voice_world):
    from backlot import voice_panel as p
    from backlot.state import _render_audio_spend
    from lib.run_common import read_request
    from lib.media_spending import call_lock
    root,posts,gets=voice_world;p.catalogue(root);made=p.prepare_voice(root,options())
    entered=threading.Event();release=threading.Event()
    def active():
        with call_lock(root):entered.set();assert release.wait(20)
    thread=threading.Thread(target=active);thread.start()
    try:
        assert entered.wait(10)
        packet=_render_audio_spend(root,read_request(root,made['gate_request_id'])[1])
        assert not posts
        assert packet['packet_error'] and packet['spending_blocked'] and 'being made' in packet['error']
        assert not posts
    finally:release.set();thread.join(20)

def test_obsolete_review_is_not_missing_pictures(board,provider):
    from backlot.state import _render_footage
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    change_config(root,lambda c:c['footage_price_basis'].update(cash_usd_per_second=.2))
    assert all(not v for v in provider.values())
    packet=_render_footage(root,gate)
    assert packet['spending_blocked'] and 'price changed' in packet['error']

def test_internal_provenance_error_is_plain():
    from backlot.footage_controls import plain_refusal
    text=plain_refusal(ValueError('output invented-id already has generation receipt invented-other with different provenance'))
    assert text=='This file is already recorded for another request, so it cannot be filed as this take. Review its recorded request and account outcome.'

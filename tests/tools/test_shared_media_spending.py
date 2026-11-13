"""Sound and footage share one financial stop on invented productions."""
import hashlib
import pytest
import yaml

from lib import audio_workspace as audio, media_spending as spending
from tests.tools.test_signed_footage import film, provider, prepare, approve, workspace, looks
from tests.tools.test_audio_half_transport import http, music_request
from tests.tools.test_audio_governance import project, sign, Transport, run


def test_purchase_and_account_review_use_the_productions_existing_lock(project):
    import os,subprocess,sys
    code="""import fcntl,sys
with open(sys.argv[1],'a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    print('held',flush=True)
    sys.stdin.readline()
"""
    with subprocess.Popen([sys.executable,'-c',code,str(project/'.audio-call.lock')],
            env=dict(os.environ),stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) as owner:
        try:
            assert owner.stdout.readline().strip()=='held'
            for reentrant in (True,False):
                with pytest.raises(spending.PaidCallBusy):
                    with spending.call_lock(project,reentrant=reentrant):
                        pytest.fail('A live purchase must keep the shared financial lock.')
            with pytest.raises(spending.PaidCallBusy):
                spending.resolution_record(project,'invented',False,0,0,'Checked the invented account.')
        finally:
            owner.stdin.write('done\n');owner.stdin.flush()


@pytest.mark.parametrize('first',['audio','footage'])
def test_unknown_charge_stops_both_media_and_only_a_signature_releases_it(film,provider,http,monkeypatch,first):
    from tools.audio.music_gen import MusicGen
    from tools.video import _shared
    from tools.cost_tracker import load_reservations
    root,_=film
    data=yaml.safe_load((root/'project.yaml').read_text())
    data['provider_egress'].append({'provider':'elevenlabs','content_classes':['speech_text','music_prompt','effect_prompt']})
    data['audio_price_basis']={key:{'value':value,'set_at':1,'expires_at':4102444800}
        for key,value in [('dollars_per_credit',.0002),('music_credits_per_minute',1200),('speech_credits_per_character',1)]}
    raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    sound,_=music_request(root);picture=prepare(film)
    sign(root,'audio_spend',sound,sound['request_id']);approve(root,picture)
    inputs={'project_dir':str(root),'audio_request_id':sound['request_id']}
    if first=='audio':
        def timeout(*a,**kw):http.append(('unknown',{}));raise TimeoutError('Invented uncertain charge.')
        monkeypatch.setattr('requests.post',timeout)
        assert not MusicGen().execute(inputs).success
    else:
        def timeout(*a,**kw):raise TimeoutError('Invented uncertain charge.')
        monkeypatch.setattr(_shared,'fal_queue_wait',timeout)
        with pytest.raises(ValueError):workspace().execute(root,picture['request_id'])
    before=(len(http),len(provider['uploads']),len(provider['posts']))
    assert before==((1,0,0) if first=='audio' else (0,1,1))
    assert not MusicGen().execute(inputs).success
    with pytest.raises(Exception):workspace().execute(root,picture['request_id'])
    assert before==(len(http),len(provider['uploads']),len(provider['posts']))
    reservation,=load_reservations(root).values()
    assert reservation['state']=='pending_billing'
    args=(root,reservation['reservation_id'],False,0,0,'Checked the invented service account.')
    with pytest.raises(ValueError,match='signed'):spending.resolve_charge(*args)
    looks.approve_request(spending.request_resolution(*args),root)
    spending.resolve_charge(*args)
    assert load_reservations(root)[reservation['reservation_id']]['state']=='failed'
    if first=='audio':assert workspace().execute(root,picture['request_id'])['status']=='Waiting for your decision'
    else:
        result=MusicGen().execute(inputs)
        assert result.success,result.error
    assert not (root/'checkpoint_assets.json').exists()
    assert audio.resolve_charge is spending.resolve_charge


@pytest.mark.parametrize('kind,key',[('audio_take','id'),('footage_take','take_id')])
def test_signed_decline_survives_edited_metadata_and_a_later_accept(project,kind,key):
    take={key:'invented-take','description':'A quiet doorway.'}
    record={'take':take,'decision':'decline'}
    sign(project,kind,record,'invented-take')
    changed={**take,'description':'An edited description.'}
    sign(project,kind,{'take':changed,'decision':'accept'},'invented-take')
    assert spending.take_decisions(project,kind,'invented-take',changed,identity_key=key)==[record]


@pytest.mark.parametrize('hint',[None,{'audio_approval_receipt_id':'invented'},{'footage_approval_receipt_id':'invented'}])
def test_every_reservation_publishes_its_intent_before_the_budget_hold(project,monkeypatch,hint):
    from tools.cost_tracker import reserve_paid_call,load_reservations,resume_check,IndeterminatePaidCallError
    from lib.project_config import load_current_verified_project_config
    tracker=spending._tracker(project,load_current_verified_project_config(project))
    def crash(entry):raise SystemExit('Invented crash inside the hold.')
    monkeypatch.setattr(tracker,'reserve',crash)
    with pytest.raises(SystemExit):
        reserve_paid_call(tracker,project,tool='invented',endpoint='invented',
            normalized_inputs_hash='a'*64,reserved_usd=.1,output_hint=hint)
    reservation,=load_reservations(project).values()
    assert reservation['state']=='submitting'
    with pytest.raises(IndeterminatePaidCallError):resume_check(project)


def test_a_signed_audio_request_with_only_typed_price_bounds_cannot_submit(project):
    from tools.audio import _governed
    record=_governed.prepare(project,kind='speech',model='invented',voice='invented',
        prompt='A quiet gallery.',duration_seconds=3,max_cash_usd=.4,max_credits_usd=.6,
        price_expires_at=4102444800)
    sign(project,'audio_spend',record,record['request_id'])
    transport=Transport()
    with pytest.raises(ValueError,match='signed price basis'):run(project,record,transport)
    assert (transport.calls,transport.downloads)==(0,0)


def test_effects_have_no_signed_price_contract_and_cannot_submit(project):
    from tests.tools.test_audio_governance import prepare as audio_request
    record=audio_request(project,kind='effect',voice=None)
    sign(project,'audio_spend',record,record['request_id'])
    transport=Transport()
    with pytest.raises(ValueError,match='speech or instrumental music'):run(project,record,transport)
    assert (transport.calls,transport.downloads)==(0,0)


def test_ceiling_changed_during_quote_is_checked_before_submit(project,monkeypatch):
    from lib import audio_prices
    from tests.tools.test_audio_governance import prepare as audio_request,approve as approve_audio
    record=audio_request(project);approve_audio(project,record);transport=Transport()
    original=audio_prices.quote
    def change_ceiling(*args,**kwargs):
        price=original(*args,**kwargs)
        data=yaml.safe_load((project/'project.yaml').read_text());data['budget_usd_cap']=0
        raw=yaml.safe_dump(data).encode();(project/'project.yaml').write_bytes(raw)
        sign(project,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
        return price
    monkeypatch.setattr(audio_prices,'quote',change_ceiling)
    with pytest.raises(ValueError,match='settings changed'):run(project,record,transport)
    assert (transport.calls,transport.downloads)==(0,0)

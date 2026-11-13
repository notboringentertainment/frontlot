"""Signed footage on invented material; all four provider boundaries count."""
import copy
import hashlib
import importlib
import importlib.util
import json
import subprocess
from functools import lru_cache
from pathlib import Path
import pytest
import yaml

from lib import gates, receipts, supervised_production as sp
from lib.canonical_json import record_sha256
from lib.trailer_edit import create_trailer, save_revision
from tests.lib.test_trailer_edit import signed_story, paper
from tests.lib import look_lock_helpers as looks
from tests.tools._authored_film_helpers import write_receipted_png


def workspace():
    assert importlib.util.find_spec('lib.footage_requests'), 'Signed footage operations are not implemented.'
    return importlib.import_module('lib.footage_requests')


def sign(root, kind, record, entity):
    with gates.handler_context():
        token = gates.mint_gate_token(root.name, 'assets', kind, record_sha256(record), user_response='yes')
        return receipts.record_human_approval(root, root.name, 'assets', kind, record, token, kind, entity_id=entity)


@pytest.fixture
def film(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.gate_approve.require_tty', lambda: None)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR', tmp_path)
    monkeypatch.setattr('lib.events.PROJECTS_DIR', tmp_path)
    monkeypatch.setenv('FAL_KEY', 'invented-only')
    root = create_trailer(tmp_path, 'An invented gallery trailer', 4, 50)
    looks.approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()), root)
    config = {'version':'1.5', 'budget_usd_cap':50, 'wall_time_minutes':60,
        'cast_cap':{'characters':1,'locations':1},
        'provider_egress':[{'provider':'fal','content_classes':['prompts','reference_images']}],
        'default_video_endpoint':'fal-ai/kling-video/o3/pro/reference-to-video',
        'qc':{'judge_provider':'openai','judge_model':'invented','policy_bundle_sha256':'a'*64,
            'max_attempts_per_series':1,'hero_policy_sha256':'b'*64,'max_hero_attempts':1},
        'footage_price_basis':{'provider':'fal','model':'fal-ai/kling-video/o3/pro/reference-to-video',
            'cash_usd_per_second':.1,'credits_usd_per_second':0,'set_at':1,'expires_at':4102444800}}
    raw = yaml.safe_dump(config).encode()
    (root/'project.yaml').write_bytes(raw)
    sign(root, 'config', {'config_sha256':hashlib.sha256(raw).hexdigest()}, 'project_config')
    signed_story(root)
    monkeypatch.setattr(looks, 'PROJECT', root.name)
    look = looks.character_look('gallery-keeper')
    looks.activate_look(root, look)
    image = write_receipted_png(root, 'assets/keeper.png')
    value = paper()
    value['shots'][0].update(purpose='The gallery keeper looks towards a doorway.', references=['assets/keeper.png'])
    edit = save_revision(root, value, None)
    shot = edit['paper_cut']['shots'][0]
    sp.prepare(root, {'shot_id':shot['id'],'paper_shot_id':shot['id'],'direction':shot['purpose'],
        'source_paths':['checkpoint_canon_ingest.json'],'spend_allowance_usd':50,'max_video_takes':10,
        'allowed_tools':['kling_reference_video'],'look_refs':looks.look_refs_for(look),
        'reference_manifest':[{'path':'assets/keeper.png','asset_id':image['sha256'],
            'role':'hero','visual_bible_entity_id':'gallery-keeper'}]}, user_note='An invented shot brief.')
    return root, shot['id']


@lru_cache
def clip_bytes():
    import tempfile
    path = Path(tempfile.mkdtemp(prefix='footage-pattern-'))/'pattern.mp4'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','testsrc=duration=5:size=160x90:rate=24',
        '-c:v','libx264','-pix_fmt','yuv420p',str(path)], check=True)
    return path.read_bytes()


@pytest.fixture
def provider(monkeypatch):
    from tools.video import _shared
    calls = {'uploads':[], 'posts':[], 'waits':[], 'downloads':[]}
    def upload(path):
        calls['uploads'].append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
        return 'https://simulation.invalid/reference.png'
    def post(model, payload, **kw):
        calls['posts'].append((model, copy.deepcopy(payload)))
        return {'request_id':'invented-job-'+str(len(calls['posts']))}
    def wait(*a, **k):
        calls['waits'].append(1)
        return {'video':{'url':'https://simulation.invalid/clip.mp4'},'cash_usd':.5,'credits_usd':0}
    def download(url, dest, **kw):
        calls['downloads'].append(1); Path(dest).write_bytes(clip_bytes())
    monkeypatch.setattr(_shared,'upload_image_fal',upload)
    monkeypatch.setattr(_shared,'fal_queue_submit',post)
    monkeypatch.setattr(_shared,'fal_queue_wait',wait)
    monkeypatch.setattr(_shared,'fal_download',download)
    return calls


def prepare(film):
    root, shot = film
    return workspace().prepare_request(root, shot, duration_seconds=5,
        model='fal-ai/kling-video/o3/pro/reference-to-video')


def approve(root, record):
    return looks.approve_request(workspace().request_approval(root, record['request_id']), root)


def test_no_signature_means_no_upload_or_post(film, provider):
    root, _ = film
    record = prepare(film)
    with pytest.raises(ValueError, match='signed'):
        workspace().execute(root, record['request_id'])
    assert provider == {'uploads':[],'posts':[],'waits':[],'downloads':[]}


def test_one_signature_one_clip_and_signed_take_in_timeline(film, provider):
    root, shot = film
    record = prepare(film); approve(root, record)
    first = workspace().execute(root, record['request_id'])
    assert workspace().execute(root, record['request_id']) == first
    assert len(provider['posts']) == len(provider['uploads']) == 1
    assert first['status'] == 'Waiting for your decision'
    req = workspace().request_decision(root, shot, first['take_id'], 'accept')
    looks.approve_request(req, root)
    sp.select(root, shot, first['take_id'], user_note='Use the signed take.')
    assert workspace().require_accepted(root, shot, first['take_id'])['sha256'] == first['sha256']
    edit = json.loads((root/'artifacts/edit_decisions.json').read_text())
    assert edit['timeline']['picture'][0]['take_id'] == first['take_id']
    assert edit['timeline']['picture'][0]['kind'] == 'footage'
    assert edit['revision_history'][-1]['timeline']['picture'][0]['kind'] == 'placeholder'
    assert not (root/'checkpoint_assets.json').exists()


@pytest.mark.parametrize('field,value', [('prompt','Another action.'),('model','another-model'),
    ('duration_seconds',6),('max_cash_usd',.7),('max_credits_usd',1),('price_expires_at',4102444801),
    ('references',[]),('look_refs',[])])
@pytest.mark.parametrize('rehash',[False,True],ids=['broken-fingerprint','recomputed-fingerprint'])
def test_changed_approved_request_refuses_before_network(film, provider, field, value,rehash):
    root, _ = film
    record = prepare(film); approve(root, record)
    altered = copy.deepcopy(record); altered[field] = value
    if rehash:altered['input_sha256']=record_sha256({k:v for k,v in altered.items() if k not in ('request_id','input_sha256')})
    path = root/'.footage-requests'/(record['request_id']+'.json')
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError):workspace().execute(root, record['request_id'])
    assert not provider['uploads'] and not provider['posts']


def test_missing_story_refuses_preparation(film, provider):
    root, _ = film
    (root/'checkpoint_canon_ingest.json').write_text('{}')
    with pytest.raises(ValueError,match='story|Story'):prepare(film)
    assert not provider['posts']


def test_decline_is_signed_and_final(film, provider):
    root, shot = film
    record = prepare(film); approve(root,record)
    take = workspace().execute(root,record['request_id'])
    with pytest.raises(ValueError,match='signature'):sp.decline(root,shot,take['take_id'],user_note='Decline.')
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'decline'),root)
    sp.decline(root,shot,take['take_id'],user_note='Apply the signed decline.')
    with pytest.raises(ValueError,match='declined'):workspace().request_decision(root,shot,take['take_id'],'accept')
    with pytest.raises(ValueError):workspace().require_accepted(root,shot,take['take_id'])
    assert workspace().take_state(root,shot,take['take_id']) == 'Declined'


def test_unknown_charge_uses_the_shared_signed_audio_resolution(film, provider, monkeypatch):
    from tools.video import _shared
    from tools.cost_tracker import load_reservations
    from lib import audio_workspace
    root, shot = film
    record = prepare(film); approve(root,record)
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:(_ for _ in ()).throw(TimeoutError('Unknown simulated billing.')))
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    reservation = next(iter(load_reservations(root).values()))
    assert reservation['state'] == 'pending_billing'
    with pytest.raises(Exception):workspace().prepare_request(root,shot,duration_seconds=5,model=record['model'],retake_of=record['request_id'])
    with pytest.raises(ValueError,match='signed'):
        audio_workspace.resolve_charge(root,reservation['reservation_id'],False,0,0,'Checked the invented service account.')
    req = audio_workspace.request_resolution(root,reservation['reservation_id'],False,0,0,'Checked the invented service account.')
    from backlot.state import GATE_RENDERERS
    assert GATE_RENDERERS['audio_resolution'](root,req)['footage_resolution']['description']==record['prompt']
    looks.approve_request(req,root)
    audio_workspace.resolve_charge(root,reservation['reservation_id'],False,0,0,'Checked the invented service account.')
    assert load_reservations(root)[reservation['reservation_id']]['state']=='failed'
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    assert len(provider['posts']) == 1


@pytest.mark.parametrize('change', ['description','reference','look','source'])
def test_live_source_change_refuses_before_upload(film, provider, change):
    root, shot = film
    record = prepare(film);approve(root,record)
    if change=='reference':(root/'assets/keeper.png').write_bytes(b'A changed image.')
    elif change=='source':(root/'checkpoint_canon_ingest.json').write_text('{}')
    elif change=='look':looks.activate_look(root,looks.character_look('gallery-keeper',hair='A different approved haircut.'),
        supersedes=record['look_refs'][0]['look_hash'])
    else:
        path=root/'artifacts/edit_decisions.json';value=json.loads(path.read_text())
        revised=copy.deepcopy(value['paper_cut']);revised['shots'][0]['purpose']='An altered paper action.'
        save_revision(root,revised,value['edit_revision_sha256'])
    with pytest.raises(Exception):workspace().execute(root,record['request_id'])
    assert not provider['uploads'] and not provider['posts']


def test_accepted_take_becomes_stale_after_paper_change(film, provider):
    root, shot = film
    record=prepare(film);approve(root,record);take=workspace().execute(root,record['request_id'])
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'accept'),root)
    sp.select(root,shot,take['take_id'],user_note='Apply the signed choice.')
    current=json.loads((root/'artifacts/edit_decisions.json').read_text())
    revised=copy.deepcopy(current['paper_cut']);revised['shots'][0]['references']=[]
    save_revision(root,revised,current['edit_revision_sha256'])
    assert workspace().take_state(root,shot,take['take_id'])=='Needs a fresh look'
    from backlot.state import load_board_state
    assert load_board_state(root)['shots'][0]['takes'][0]['needs_review']
    with pytest.raises(ValueError):workspace().require_accepted(root,shot,take['take_id'])


def test_changed_shot_can_prepare_a_separately_priced_retake(film,provider):
    root,shot=film
    record=prepare(film);approve(root,record);take=workspace().execute(root,record['request_id'])
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'accept'),root)
    sp.select(root,shot,take['take_id'],user_note='Use the signed take.')
    current=json.loads((root/'artifacts/edit_decisions.json').read_text())
    revised=copy.deepcopy(current['paper_cut']);revised['shots'][0]['purpose']='The keeper turns towards a second doorway.'
    save_revision(root,revised,current['edit_revision_sha256'])
    brief=sp.read_brief(root,shot)
    spec={k:v for k,v in brief.items() if k not in ('revision_id','stopped','source_snapshots','paper_shot')}
    spec['direction']=revised['shots'][0]['purpose']
    sp.prepare(root,spec,user_note='A revised invented shot.')
    new=workspace().prepare_request(root,shot,duration_seconds=5,model=record['model'],retake_of=record['request_id'])
    assert new['request_id']!=record['request_id']
    assert new['prompt']==spec['direction']
    assert len(provider['posts'])==1


@pytest.mark.parametrize('route',['tool','internal','script','selector'])
def test_other_video_routes_cannot_use_conversational_approval(film, provider, route, monkeypatch):
    from tools.video.kling_reference_video import KlingReferenceVideo
    root, shot = film
    inputs=sp.request(root,shot,prompt=sp.read_brief(root,shot)['direction'],output_path='assets/unsigned.mp4',duration='5')
    if route=='tool':result=KlingReferenceVideo().execute(inputs)
    elif route=='internal':result=KlingReferenceVideo()._execute(inputs)
    elif route=='selector':
        from tools.video import _shared
        with pytest.raises(Exception):_shared.paid_call_context(inputs,media='video',estimated_usd=.5)
        result=None
    else:
        from scripts import supervised_shot
        settings=root/'invented-settings.json';settings.write_text(json.dumps({'prompt':inputs['prompt'],'output_path':'assets/unsigned.mp4','duration':'5'}))
        monkeypatch.setattr('sys.argv',['supervised_shot',str(root),'generate',shot,str(settings),'--tool','kling_reference_video'])
        from tools.tool_registry import registry
        monkeypatch.setattr(registry,'discover',lambda:None);monkeypatch.setattr(registry,'get',lambda name:KlingReferenceVideo())
        with pytest.raises(Exception):supervised_shot._main()
        result=None
    if result is not None:assert not result.success
    assert not provider['posts'] and not provider['uploads']


@pytest.mark.parametrize('field,value',[('expires_at',1),('model','another-model'),('cash_usd_per_second',None)])
def test_invalid_or_expired_signed_figures_refuse(film,provider,field,value):
    root, _ = film
    data=yaml.safe_load((root/'project.yaml').read_text());data['footage_price_basis'][field]=value
    raw=yaml.safe_dump(data).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    with pytest.raises(ValueError):prepare(film)
    assert not provider['posts'] and not provider['uploads']


@pytest.mark.parametrize('forged',['selection','decline'])
def test_unsigned_rows_do_not_block_or_borrow_a_signed_take_decision(film,provider,forged):
    from backlot.state import load_board_state
    root,shot=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    sp._append(root,shot,{'kind':forged,'take_id':take['take_id'],'receipt_id':'invented','signature':'invented'})
    assert workspace().take_state(root,shot,take['take_id'])=='Waiting for your decision'
    assert sp.inspect_shot(root,shot)['takes'][0]['standing']=='waiting'
    assert next(s for s in load_board_state(root)['shots'] if s['id']==shot)['takes'][0]['waiting']
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'accept'),root)
    assert sp.inspect_shot(root,shot)['takes'][0]['standing']=='chosen'
    sp.select(root,shot,take['take_id'],user_note='Apply a real signed choice.')
    assert workspace().require_accepted(root,shot,take['take_id'])['take_id']==take['take_id']
    assert not (root/'checkpoint_assets.json').exists()


def test_signed_decline_is_final_even_after_a_later_signed_accept_record(film,provider):
    root,shot=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id']);raw=workspace()._take(root,shot,take['take_id'])
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'decline'),root)
    # A different or future signer must not revive a terminal decline either.
    sign(root,'footage_take',{'shot_id':shot,'take':raw,'decision':'accept'},take['take_id'])
    assert workspace().take_state(root,shot,take['take_id'])=='Declined'
    assert sp.inspect_shot(root,shot)['takes'][0]['standing']=='declined'
    with pytest.raises(ValueError):sp.select(root,shot,take['take_id'],user_note='Attempt a revival.')
    with pytest.raises(ValueError):workspace().require_accepted(root,shot,take['take_id'])
    current=json.loads((root/'artifacts/edit_decisions.json').read_text())
    assert current['timeline']['picture'][0]['kind']=='placeholder'


def test_signed_authority_cannot_escape_to_another_provider(film,provider,monkeypatch):
    from tools.video import _shared
    from tools.video.seedance_video import SeedanceVideo
    root,_=film;record=prepare(film);approve(root,record)
    original=_shared.upload_image_fal;attempts=[]
    def upload(path):
        result=SeedanceVideo().execute(workspace().tool_inputs(root,record))
        attempts.append(result.success)
        assert not result.success
        return original(path)
    monkeypatch.setattr(_shared,'upload_image_fal',upload)
    workspace().execute(root,record['request_id'])
    assert attempts==[False] and len(provider['posts'])==1


def test_changed_remaining_ceiling_can_make_a_fresh_card_without_erasing_the_old_one(film):
    from tools.audio._governed import _tracker
    from lib.project_config import load_current_verified_project_config
    root,_=film;old=prepare(film)
    tracker=_tracker(root,load_current_verified_project_config(root))
    entry=tracker.estimate('invented-existing-service','invented-model',.1)
    tracker.reserve(entry);tracker.reconcile(entry,.1)
    fresh=prepare(film)
    assert fresh['request_id']!=old['request_id']
    assert fresh['remaining_usd']==pytest.approx(old['remaining_usd']-.1)
    assert workspace()._read(root,old['request_id'])==old


@pytest.mark.parametrize('point',['before-hold','after-hold'])
def test_crash_around_budget_hold_is_durably_reserved_and_signed_resolvable(film,provider,monkeypatch,point):
    from tools.cost_tracker import CostTracker,nonterminal_reservations,resume_check,IndeterminatePaidCallError
    from lib import audio_workspace
    root,_=film;record=prepare(film);approve(root,record)
    class SimulatedCrash(BaseException):pass
    original=CostTracker.reserve
    def interrupted(self,*args,**kwargs):
        if point=='after-hold':original(self,*args,**kwargs)
        raise SimulatedCrash()
    with monkeypatch.context() as patch:
        patch.setattr(CostTracker,'reserve',interrupted)
        with pytest.raises(SimulatedCrash):workspace().execute(root,record['request_id'])
    pending=nonterminal_reservations(root)
    assert len(pending)==1
    assert pending[0]['output_hint']['footage_request_id']==record['request_id']
    with pytest.raises(IndeterminatePaidCallError):resume_check(root)
    with pytest.raises(Exception):prepare(film)
    assert not provider['uploads'] and not provider['posts']
    identity=pending[0]['reservation_id']
    note='Checked the invented service account; the clip was never submitted.'
    looks.approve_request(audio_workspace.request_resolution(root,identity,False,0,0,note),root)
    audio_workspace.resolve_charge(root,identity,False,0,0,note)
    assert nonterminal_reservations(root)==[]
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    assert not provider['posts']


def test_unsigned_reconciler_cannot_resolve_or_poll_an_unknown_trailer_charge(film,provider,monkeypatch):
    import io
    from scripts import reconcile_paid_calls as reconciler
    from tools.video import _shared
    from tools.cost_tracker import nonterminal_reservations
    root,_=film;record=prepare(film);approve(root,record)
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:(_ for _ in ()).throw(TimeoutError('Invented unknown result.')))
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    pending=nonterminal_reservations(root);polled=[]
    monkeypatch.setattr(reconciler,'fal_queue_status',lambda *a,**k:polled.append(1) or {'status':'FAILED'})
    summary=reconciler.reconcile_project(root,api_key='invented-only',out=io.StringIO())
    assert len(summary['manual'])==1 and not summary['failed']
    assert not polled and nonterminal_reservations(root)==pending


def test_identical_retakes_keep_independent_signed_provenance_and_final_decisions(film,provider):
    root,shot=film;first_request=prepare(film);approve(root,first_request)
    first=workspace().execute(root,first_request['request_id'])
    looks.approve_request(workspace().request_decision(root,shot,first['take_id'],'accept'),root)
    sp.select(root,shot,first['take_id'],user_note='Use the first signed take.')
    retake=workspace().prepare_request(root,shot,duration_seconds=5,model=first_request['model'],retake_of=first_request['request_id'])
    approve(root,retake);second=workspace().execute(root,retake['request_id'])
    assert first['sha256']==second['sha256'] and first['take_id']!=second['take_id']
    assert workspace().take_state(root,shot,first['take_id'])=='Accepted'
    looks.approve_request(workspace().request_decision(root,shot,second['take_id'],'accept'),root)
    sp.select(root,shot,second['take_id'],user_note='Use the second signed take.')
    looks.approve_request(workspace().request_decision(root,shot,first['take_id'],'decline'),root)
    sp.decline(root,shot,first['take_id'],user_note='Keep the first take declined.')
    assert workspace().take_state(root,shot,first['take_id'])=='Declined'
    assert workspace().take_state(root,shot,second['take_id'])=='Accepted'
    assert workspace().require_accepted(root,shot,second['take_id'])
    current=json.loads((root/'artifacts/edit_decisions.json').read_text())
    assert current['timeline']['picture'][0]['take_id']==second['take_id']
    assert len(provider['posts'])==2


def test_one_completed_request_cannot_be_reattached_to_revive_its_declined_take(film,provider):
    root,shot=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'decline'),root)
    sp.decline(root,shot,take['take_id'],user_note='A signed decline.')
    again=sp.attach(root,shot,root/take['path'],reservation_id=take['reservation_id'],user_note='Try the same paid clip again.',
        prompt=record['prompt'],references=record['references'])
    assert again['take_id']==take['take_id']
    assert len([r for r in sp.history(root,shot) if r['kind']=='take'])==1
    with pytest.raises(ValueError):workspace().request_decision(root,shot,again['take_id'],'accept')
    assert len(provider['posts'])==1


def test_success_without_invoice_fields_books_the_signed_maximum_honestly(film,provider,monkeypatch):
    from tools.video import _shared
    from tools.audio._governed import _tracker
    from lib.project_config import load_current_verified_project_config
    root,_=film;record=prepare(film);approve(root,record)
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:{'video':{'url':'https://simulation.invalid/clip.mp4'}})
    take=workspace().execute(root,record['request_id'])
    receipt=receipts.find_generation_by_id(root,take['generation_receipt_id'])
    assert receipt['footage_charge_basis']=='signed_maximum'
    assert take['cost_basis']=='signed_maximum'
    assert receipt['cost_usd']==record['max_cash_usd']+record['max_credits_usd']
    assert _tracker(root,load_current_verified_project_config(root)).budget_spent_usd==receipt['cost_usd']
    assert len(provider['posts'])==1


def test_generation_receipt_signature_binds_the_specific_footage_approval(film,provider):
    root,_=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    receipt=receipts.find_generation_by_id(root,take['generation_receipt_id'])
    assert gates.verify_receipt_signature(receipt)
    assert receipt['footage_charge_basis']=='service_report'
    for field in ['footage_request_id','footage_approval_receipt_id','cash_usd','footage_charge_basis']:
        altered=copy.deepcopy(receipt);altered[field]='changed'
        assert not gates.verify_receipt_signature(altered)


def accepted(film):
    root,shot=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'accept'),root)
    return take


def test_accept_preserves_later_timing_and_marks_downstream_review(film,provider):
    from lib import trailer_edit as edit
    root,shot=film;take=accepted(film)
    current=edit.read_revision(root);later=copy.deepcopy(current)
    later['timeline']['picture'][0].update(source_in_frame=12,source_out_frame=108,source_timebase_fps=24)
    later['timeline']['sound']=[{'label':'An invented sound entry','start_frame':17}]
    saved=edit.save_edit_revision(root,later)
    result=sp.select(root,shot,take['take_id'],user_note='Apply the signed choice.')
    assert result['timeline']['sound']==saved['timeline']['sound']
    assert result['timeline']['picture'][0]['source_in_frame']==12
    assert result['timeline']['picture'][0]['source_out_frame']==108
    assert result['paper_cut_pending'] and 'fresh look' in result['timing_message']
    assert result['revision_history'][-1]['edit_revision_sha256']==saved['edit_revision_sha256']


def test_accept_does_not_write_an_out_of_bounds_source_window(film,provider):
    from lib import trailer_edit as edit
    root,shot=film;take=accepted(film)
    current=edit.read_revision(root);later=copy.deepcopy(current)
    later['timeline']['picture'][0].update(source_in_frame=48,source_out_frame=144,source_timebase_fps=24)
    saved=edit.save_edit_revision(root,later)
    with pytest.raises(ValueError,match='short|trim|window'):
        sp.select(root,shot,take['take_id'],user_note='Apply the signed choice.')
    assert edit.read_revision(root)==saved
    assert workspace().take_state(root,shot,take['take_id'])=='Accepted'


def test_decline_then_restoring_a_saved_cut_cannot_restore_usable_footage(film,provider):
    from lib import trailer_edit as edit
    root,shot=film;take=accepted(film)
    chosen=sp.select(root,shot,take['take_id'],user_note='Apply the signed choice.')
    looks.approve_request(workspace().request_decision(root,shot,take['take_id'],'decline'),root)
    # The signer can finish before a crash prevents the timeline projection.
    with pytest.raises(ValueError,match='accepted|declined'):
        workspace().validate_cut_sources(root,chosen)
    restored=edit.save_edit_revision(root,chosen)
    assert restored['timeline']['picture'][0]['kind']=='placeholder'
    assert restored['set_aside_footage'][-1]['picture']['take_id']==take['take_id']
    assert restored['unfinished'] and restored['paper_cut_pending']
    assert restored['revision_history'][-1]['timeline']['picture'][0]['take_id']==take['take_id']


@pytest.mark.parametrize('change',['conceal-marker','broken-history'])
def test_paid_request_requires_positive_signed_trailer_not_board_label(film,provider,change):
    root,_=film;record=prepare(film);approve(root,record)
    if change=='broken-history':(root/'approvals.jsonl').write_text('{}\n')
    else:
        value=json.loads((root/'project.json').read_text());value.pop('trailer',None);value.pop('production_kind',None)
        (root/'project.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    assert not provider['uploads'] and not provider['posts']


def test_request_card_shows_saved_outgoing_material_and_prices(film,provider):
    from backlot.state import GATE_RENDERERS
    root,_=film;record=prepare(film);request=workspace().request_approval(root,record['request_id'])
    request['approval_record']['prompt']='An untrusted hint.'
    packet=GATE_RENDERERS['footage_spend'](root,request)
    assert packet['footage_card']['description']==record['prompt']
    assert packet['footage_card']['highest_charge']==.5
    assert packet['footage_card']['remaining']==50
    assert packet['visuals'][0]['sha256']==record['references'][0]['asset_id']
    assert packet['footage_card']['looks']
    assert not provider['uploads'] and not provider['posts']


def test_take_card_and_timing_sketch_use_verified_accepted_footage(film,provider):
    from backlot.state import GATE_RENDERERS,load_board_state
    root,shot=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    request=workspace().request_decision(root,shot,take['take_id'],'accept')
    packet=GATE_RENDERERS['footage_take'](root,request)
    assert packet['footage_take']['path']==take['path']
    assert packet['footage_card']['decision']=='accept'
    looks.approve_request(request,root);sp.select(root,shot,take['take_id'],user_note='Use the signed take.')
    state=load_board_state(root)
    assert state['paper_cut']['footage_availability'][shot]=='Accepted footage available'
    assert state['paper_cut']['current']['unfinished']


@pytest.mark.parametrize('billing',[{'cash_usd':.51,'credits_usd':0},{'cash_usd':.1},{'cash_usd':0,'credits_usd':1}])
def test_overcharge_or_incomplete_charge_stays_reserved_and_blocks(film,provider,monkeypatch,billing):
    from tools.video import _shared
    from tools.cost_tracker import nonterminal_reservations
    root,_=film;record=prepare(film);approve(root,record)
    monkeypatch.setattr(_shared,'fal_queue_wait',lambda *a,**k:{'video':{'url':'https://simulation.invalid/clip.mp4'},**billing})
    with pytest.raises(ValueError):workspace().execute(root,record['request_id'])
    assert nonterminal_reservations(root)
    with pytest.raises(Exception):prepare(film)
    assert len(provider['posts'])==1 and not provider['downloads']


def test_two_confirmations_in_parallel_and_restart_buy_one_clip(film,provider,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tools.video import _shared
    from lib.audio_workspace import PaidCallBusy
    root,_=film;record=prepare(film);approve(root,record)
    entered=Event();release=Event();original=_shared.fal_queue_submit
    def paused(*args,**kwargs):
        entered.set();assert release.wait(20)
        return original(*args,**kwargs)
    monkeypatch.setattr(_shared,'fal_queue_submit',paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(workspace().execute,root,record['request_id'])
        try:
            assert entered.wait(20)
            with pytest.raises(PaidCallBusy):workspace().execute(root,record['request_id'])
        finally:release.set()
        take=first.result(timeout=30)
    # A fresh operation has no remembered in-process authorization.
    assert workspace().execute(root,record['request_id'])==take
    assert len(provider['uploads'])==len(provider['posts'])==1


@pytest.mark.parametrize('field,value',[('prompt','A relabelled shot.'),('references',[]),('duration_seconds',900),
    ('cost_usd',0),('cost_basis','service_report'),('path','assets/keeper.png')])
def test_relabelled_take_cannot_obtain_acceptance(film,provider,field,value):
    root,shot=film;record=prepare(film);approve(root,record)
    if field=='cost_basis':value='invented_invoice'
    take=workspace().execute(root,record['request_id'])
    # Rewrite only an invented notebook to reproduce an edited take record.
    candidates=[p for p in sp.shot_dir(root,shot).glob('*.jsonl')]
    assert len(candidates)==1
    path=candidates[0];rows=[json.loads(line) for line in path.read_text().splitlines()]
    next(r for r in rows if r.get('take_id')==take['take_id'])[field]=value
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError):workspace().request_decision(root,shot,take['take_id'],'accept')
    assert len(provider['posts'])==1

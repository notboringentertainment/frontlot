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
def test_changed_approved_request_refuses_before_network(film, provider, field, value):
    root, _ = film
    record = prepare(film); approve(root, record)
    altered = copy.deepcopy(record); altered[field] = value
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
    with pytest.raises(ValueError,match='signed'):sp.decline(root,shot,take['take_id'],user_note='Decline.')
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

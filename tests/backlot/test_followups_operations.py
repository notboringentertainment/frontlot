"""Invented footage and sound regressions; every service boundary is counted."""
import json
import pytest
from tests.tools.test_signed_footage import film, provider, prepare, approve, workspace, looks
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project, sign
from tests.tools.test_audio_half_transport import priced, http
from tests.backlot.test_music_panel import prepare as music_request


@pytest.mark.parametrize('change',['retired','replaced'])
def test_1_changed_look_makes_kept_take_stale(film,provider,change):
    from lib.look_ingest import active_looks
    root,shot=film;record=prepare(film);approve(root,record)
    take=workspace().execute(root,record['request_id'])
    gate=workspace().request_decision(root,shot,take['take_id'],'accept')
    looks.approve_request(gate,root);workspace().apply_decision(root,shot,take['take_id'],'accept')
    old=next(iter(active_looks(root).values()))
    if change=='retired':looks.retire_look(root,old.payload)
    else:looks.activate_look(root,dict(old.payload,prompt_safe_description='An invented new coat.'),supersedes=old.look_hash)
    assert workspace().take_state(root,shot,take['take_id'])=='Needs a fresh look'
    with pytest.raises(ValueError,match='looks.*approved|look.*changed'):
        workspace()._sources(root,record)
    assert len(provider['posts'])==1


@pytest.mark.parametrize('contacted',[False,True])
def test_2_only_proven_precontact_failure_releases_hold(film,provider,monkeypatch,contacted):
    from tools.video import _shared
    from tools.cost_tracker import load_reservations,IndeterminatePaidCallError
    from lib.media_spending import _tracker
    from lib.project_config import load_current_verified_project_config
    from lib import gates,receipts
    root,_=film;record=prepare(film);approve(root,record)
    def refuse(*a,**k):
        if contacted:provider['uploads'].append('An upload was attempted.')
        raise OSError('Invented failed upload preparation.' if not contacted else 'Invented uncertain upload.')
    monkeypatch.setattr(_shared,'upload_image_fal',refuse) if contacted else monkeypatch.setattr(workspace(),'frozen_reference',refuse)
    with pytest.raises(ValueError,match='held' if contacted else 'not submitted or charged'):
        workspace().execute(root,record['request_id'])
    tracker=_tracker(root,load_current_verified_project_config(root))
    assert tracker.budget_spent_usd==0
    assert tracker.budget_reserved_usd==(.5 if contacted else 0)
    assert tracker.budget_remaining_usd==(49.5 if contacted else 50)
    reservation=next(iter(load_reservations(root).values()))
    assert reservation['state']==('submitting' if contacted else 'failed')
    if not contacted:
        rows=[json.loads(line) for line in (root/'cost-reservations.jsonl').read_text().splitlines()]
        receipt=next(row['not_submitted_receipt'] for row in rows if 'not_submitted_receipt' in row)
        assert gates.verify_receipt_signature(receipt)
        assert receipt['actual_usd']==0 and receipt['request_id']==record['request_id']
    assert provider['posts']==[]
    assert not any(r.get('footage_request_id')==record['request_id'] for r in receipts.verified_generation_receipts(root))
    with pytest.raises((ValueError,IndeterminatePaidCallError)):workspace().execute(root,record['request_id'])
    assert provider['posts']==[]


def test_3_refresh_contains_footage_availability(film,provider,monkeypatch):
    from fastapi.testclient import TestClient
    from backlot import server
    root,shot=film
    record=prepare(film);approve(root,record);take=workspace().execute(root,record['request_id'])
    gate=workspace().request_decision(root,shot,take['take_id'],'accept')
    looks.approve_request(gate,root);workspace().apply_decision(root,shot,take['take_id'],'accept')
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',str(root.parent.resolve()))
    with TestClient(server.create_app(port=4936,capability_token='invented')) as client:
        response=client.get(f'/api/project/{root.name}/paper-cut')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['footage_availability']==workspace().preview_availability(root,data['current'])
    assert list(data['footage_availability'].values())==['Accepted footage available']


def test_2_missing_cancellation_receipt_keeps_hold_for_signed_review(film,provider,monkeypatch):
    from lib import state_io,audio_workspace as audio
    from tools.cost_tracker import load_reservations
    from lib.media_spending import _tracker
    from lib.project_config import load_current_verified_project_config
    root,_=film;record=prepare(film);approve(root,record)
    original=state_io.append_jsonl
    def fail_receipt(path,row,*a,**k):
        if 'not_submitted_receipt' in row:raise OSError('Invented interruption before durable evidence.')
        return original(path,row,*a,**k)
    monkeypatch.setattr(state_io,'append_jsonl',fail_receipt)
    def local_failure(*a,**k):raise OSError('Invented unreadable frozen picture.')
    monkeypatch.setattr(workspace(),'frozen_reference',local_failure)
    with pytest.raises(ValueError,match='held'):workspace().execute(root,record['request_id'])
    reservation=next(iter(load_reservations(root).values()))
    tracker=lambda:_tracker(root,load_current_verified_project_config(root))
    assert tracker().budget_reserved_usd==.5 and tracker().budget_spent_usd==0
    identity=reservation['reservation_id'];note='I checked the invented account. Nothing was charged.'
    with pytest.raises(ValueError):audio.resolve_charge(root,identity,False,0,0,note)
    assert tracker().budget_reserved_usd==.5
    looks.approve_request(audio.request_resolution(root,identity,False,0,0,note),root)
    audio.resolve_charge(root,identity,False,0,0,note)
    assert tracker().budget_reserved_usd==tracker().budget_spent_usd==0
    assert all(not calls for calls in provider.values())


@pytest.mark.parametrize('kind',['speech','music'])
def test_7_receipted_take_can_be_recovered_without_transport(voice_world,monkeypatch,kind):
    from backlot import voice_panel as voice,music_panel as music
    from lib import audio_workspace as audio
    root,posts,gets=voice_world
    if kind=='speech':
        voice.catalogue(root)
        record=voice.prepare_voice(root,{'line_index':0,'voice':'invented_voice','notes':'','rights_confirmed':True})['request']
    else:record=music_request(root)
    sign(root,'audio_spend',record,record['request_id'])
    original=audio.generated_take
    def interrupted(*a,**k):raise OSError('Interrupted after the generation receipt.')
    monkeypatch.setattr(audio,'generated_take',interrupted)
    result=(voice if kind=='speech' else music).send(root,record['request_id'])
    assert not result['success'] and len(posts)==1
    monkeypatch.setattr(audio,'generated_take',original)
    item=next(r for r in voice.snapshot(root)['requests'] if r['record']['request_id']==record['request_id'])
    assert item['recoverable'] and not item['error']
    before=voice.snapshot(root)['budget']
    def forbidden(*a,**k):raise AssertionError('Recovery must never call a transport.')
    monkeypatch.setattr('requests.post',forbidden)
    recovered=voice.recover(root,record['request_id'],kind=kind)
    assert audio.take_state(root,recovered['take'])=='Waiting for your decision'
    assert voice.snapshot(root)['budget']==before and len(posts)==1
    assert voice.recover(root,record['request_id'],kind=kind)['take']['id']==record['request_id']
    with pytest.raises(ValueError):voice.recover(root,'not-a-recorded-request',kind=kind)


def test_9_signing_card_warns_when_ceiling_no_longer_covers_it(voice_world):
    import hashlib,yaml
    from backlot import voice_panel as voice,music_panel as music
    from backlot.state import _render_audio_spend
    from lib import audio_workspace as audio
    from lib.media_spending import _tracker
    from lib.project_config import load_current_verified_project_config
    root,posts,gets=voice_world
    settings=yaml.safe_load((root/'project.yaml').read_text());settings['budget_usd_cap']=.0481
    raw=yaml.safe_dump(settings).encode();(root/'project.yaml').write_bytes(raw)
    sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    a=music_request(root);b=music_request(root)
    for record in (a,b):sign(root,'audio_spend',record,record['request_id'])
    assert music.send(root,a['request_id'])['success'] and len(posts)==1
    request={'entity_id':b['request_id'],'summary':'One audio request.'}
    packet=_render_audio_spend(root,request)
    assert packet['budget_warning'] and packet['remaining_usd']==pytest.approx(.0479)
    assert 'ceiling' in packet['budget_warning']
    assert next(r for r in voice.snapshot(root)['requests'] if r['record']['request_id']==b['request_id'])['error']
    assert not music.send(root,b['request_id'])['success'] and len(posts)==1

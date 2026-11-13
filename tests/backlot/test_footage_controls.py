"""Writer controls use signed operations and counting stand-ins only."""
import importlib
import time
import pytest
from fastapi.testclient import TestClient
from tests.tools.test_signed_footage import film,provider,looks,workspace


@pytest.fixture
def board(film,provider,monkeypatch):
    from backlot import server
    root,shot=film
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    app=server.create_app(port=4863,capability_token='invented-writer')
    with TestClient(app) as client:
        client.headers.update({'origin':'http://127.0.0.1:4863','x-frontlot-capability':app.state.capability_token})
        yield client,root,shot


def post(board,action,**data):
    client,root,_=board
    return client.post(f'/api/project/{root.name}/footage/{action}',json=data)


def inspect(board):
    client,root,_=board
    response=client.get(f'/api/project/{root.name}/footage')
    assert response.status_code==200,response.text
    return response.json()


def wait_take(board):
    for _ in range(150):
        state=inspect(board)
        if state['shots'][0]['takes']:return state['shots'][0]['takes'][-1]
        time.sleep(.1)
    pytest.fail('The stand-in did not return its take.')


def test_complete_signed_shot_and_retake_through_controls(board,provider):
    _,root,shot=board
    state=inspect(board)
    assert state['shots'][0]['status']=='Nothing yet'
    assert state['shots'][0]['references'] and state['looks']
    quote=post(board,'quote',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    assert quote.status_code==200,quote.text
    assert quote.json()['highest_usd']==.5 and quote.json()['remaining_usd']==50
    made=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    assert made.status_code==200,made.text
    gate=made.json()['gate'];rid=gate['entity_id']
    ordinary=board[0].get(f'/api/project/{root.name}/state').json()
    assert any(r['request_id']==gate['request_id'] for r in (ordinary.get('gates') or {}).get('requests',[])),ordinary.get('gates')
    assert inspect(board)['shots'][0]['status']=='Waiting on your signature'
    assert post(board,'dispatch',request_id=rid).status_code==400
    assert provider['posts']==[]
    looks.approve_request(gate,root)
    started=post(board,'signed',gate_id=gate['request_id'])
    assert started.status_code==200,started.text
    assert post(board,'dispatch',request_id=rid).status_code==200
    take=wait_take(board)
    assert len(provider['posts'])==1
    assert inspect(board)['shots'][0]['status']=='Take waiting for your decision'
    decision=post(board,'decision',shot_id=shot,take_id=take['take_id'],decision='accept')
    assert decision.status_code==200,decision.text
    assert workspace().take_state(root,shot,take['take_id'])=='Waiting for your decision'
    looks.approve_request(decision.json()['gate'],root)
    assert post(board,'signed',gate_id=decision.json()['gate']['request_id']).status_code==200
    assert inspect(board)['shots'][0]['status']=='Kept'
    retake=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5,retake_of=rid)
    assert retake.status_code==200,retake.text
    assert retake.json()['gate']['entity_id']!=rid
    assert len(provider['posts'])==1
    assert not (root/'checkpoint_assets.json').exists()


@pytest.mark.parametrize('field,value',[('duration_seconds',0),('duration_seconds',2.5),('model','another-service')])
def test_invalid_choices_refuse_without_contact(board,provider,field,value):
    data={'shot_id':board[2],'model':workspace().MODEL,'duration_seconds':5,field:value}
    response=post(board,'create',**data)
    assert response.status_code==400
    assert response.json()['detail']
    assert not provider['posts'] and not provider['uploads']


def test_controls_require_the_writer_capability(board,provider):
    client,root,shot=board
    client.headers.pop('x-frontlot-capability')
    result=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    assert result.status_code==401
    assert not provider['posts']


@pytest.mark.parametrize('case',['story','picture','prices','unsigned-settings','unknown-shot','extra-price','wrong-origin','malformed'])
def test_refusals_have_a_next_step_and_never_contact_service(board,provider,case):
    import hashlib,yaml
    from tests.tools.test_signed_footage import sign
    client,root,shot=board
    data={'shot_id':shot,'model':workspace().MODEL,'duration_seconds':5}
    if case=='story':(root/'checkpoint_canon_ingest.json').write_text('{}')
    if case=='picture':(root/'assets/keeper.png').write_bytes(b'An invented changed picture.')
    if case in ('prices','unsigned-settings'):
        config=yaml.safe_load((root/'project.yaml').read_text());config.pop('footage_price_basis')
        raw=yaml.safe_dump(config).encode();(root/'project.yaml').write_bytes(raw)
        if case=='prices':sign(root,'config',{'config_sha256':hashlib.sha256(raw).hexdigest()},'project_config')
    if case=='unknown-shot':data['shot_id']='not-a-paper-shot'
    if case=='extra-price':data['max_cash_usd']=0
    if case=='wrong-origin':client.headers['origin']='https://outside.invalid'
    response=client.post(f'/api/project/{root.name}/footage/create',content='{',headers={'content-type':'application/json'}) if case=='malformed' else post(board,'create',**data)
    assert response.status_code in (400,403)
    assert isinstance(response.json()['detail'],str) and response.json()['detail']
    assert not provider['posts'] and not provider['uploads']


def test_decline_is_final_through_the_controls(board,provider):
    _,root,shot=board
    made=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()
    looks.approve_request(made['gate'],root)
    post(board,'signed',gate_id=made['gate']['request_id'])
    take=wait_take(board)
    gate=post(board,'decision',shot_id=shot,take_id=take['take_id'],decision='decline').json()['gate']
    looks.approve_request(gate,root)
    assert post(board,'signed',gate_id=gate['request_id']).status_code==200
    assert post(board,'decision',shot_id=shot,take_id=take['take_id'],decision='accept').status_code==400
    assert workspace().take_state(root,shot,take['take_id'])=='Declined'
    assert len(provider['posts'])==1


def test_new_paper_shot_gets_its_brief_without_commands(board,provider):
    import copy
    from lib.trailer_edit import read_revision,save_revision
    _,root,_=board
    current=read_revision(root);paper=copy.deepcopy(current['paper_cut'])
    paper['shots'][0]['id']='new-paper-shot'
    save_revision(root,paper,current['edit_revision_sha256'])
    made=post(board,'create',shot_id='new-paper-shot',model=workspace().MODEL,duration_seconds=5)
    assert made.status_code==200,made.text
    from lib.supervised_production import read_brief
    brief=read_brief(root,'new-paper-shot')
    assert brief['direction']==paper['shots'][0]['purpose']
    assert [r['path'] for r in brief['reference_manifest']]==paper['shots'][0]['references']
    assert not provider['posts']


def test_unknown_charge_blocks_and_uses_the_shared_signed_review(board,provider,monkeypatch):
    from tools.video import _shared
    _,root,shot=board
    made=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()
    looks.approve_request(made['gate'],root)
    def timeout(*a,**k):raise TimeoutError('Invented unknown outcome.')
    monkeypatch.setattr(_shared,'fal_queue_wait',timeout)
    assert post(board,'signed',gate_id=made['gate']['request_id']).status_code==200
    for _ in range(100):
        row=inspect(board)['shots'][0]
        if row['status']=='Charge needs your review':break
        time.sleep(.1)
    assert row['status']=='Charge needs your review'
    assert post(board,'dispatch',request_id=made['gate']['entity_id']).status_code==400
    reservation=row['requests'][0]['reservation']
    review=post(board,'resolve',reservation_id=reservation['reservation_id'],charged=False,cash=0,credits=0,note='Checked the invented service account.').json()['gate']
    assert post(board,'signed',gate_id=review['request_id']).status_code==400
    looks.approve_request(review,root)
    assert post(board,'signed',gate_id=review['request_id']).status_code==200
    assert len(provider['posts'])==1


def test_missing_approved_look_has_a_true_refusal(board,provider):
    _,root,shot=board
    from lib.look_ingest import active_looks
    looks.retire_look(root,next(iter(active_looks(root).values())).payload)
    response=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    assert response.status_code==400
    assert 'no approved active look' in response.json()['detail']
    assert not inspect(board)['looks'] and not provider['uploads']


def test_double_create_and_double_decision_reuse_the_signing_card(board,provider):
    from concurrent.futures import ThreadPoolExecutor
    _,root,shot=board
    def create():return post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5)
    with ThreadPoolExecutor(2) as workers:responses=list(workers.map(lambda _:create(),range(2)))
    assert all(r.status_code in (200,400) for r in responses),[r.text for r in responses]
    gate=next(r.json()['gate'] for r in responses if r.status_code==200)
    assert create().json()['gate']['request_id']==gate['request_id']
    assert len(list((root/'.gate-requests').glob('media-*.json')))==1
    looks.approve_request(gate,root)
    post(board,'signed',gate_id=gate['request_id']);take=wait_take(board)
    def decision():return post(board,'decision',shot_id=shot,take_id=take['take_id'],decision='accept')
    with ThreadPoolExecutor(2) as workers:responses=list(workers.map(lambda _:decision(),range(2)))
    assert all(r.status_code in (200,400) for r in responses),[r.text for r in responses]
    gate=next(r.json()['gate'] for r in responses if r.status_code==200)
    assert decision().json()['gate']['request_id']==gate['request_id']
    assert len(list((root/'.gate-requests').glob('media-*.json')))==1
    assert len(provider['posts'])==1


def test_restart_recovers_a_signed_request_and_keep_without_a_second_purchase(board,provider):
    from backlot import server
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    looks.approve_request(gate,root)
    # A new app has no in-memory jobs or browser signing tags.
    app=server.create_app(port=4863,capability_token='invented-writer')
    with TestClient(app) as client:
        client.headers.update(board[0].headers)
        fresh=(client,root,shot)
        assert post(fresh,'dispatch',request_id=gate['entity_id']).status_code==200
        take=wait_take(fresh)
        decision=post(fresh,'decision',shot_id=shot,take_id=take['take_id'],decision='accept').json()['gate']
        assert post(fresh,'apply',shot_id=shot,take_id=take['take_id'],decision='accept').status_code==400
        looks.approve_request(decision,root)
        assert not inspect(fresh)['shots'][0]['takes'][0]['selected']
        assert post(fresh,'apply',shot_id=shot,take_id=take['take_id'],decision='accept').status_code==200
        assert post(fresh,'apply',shot_id=shot,take_id=take['take_id'],decision='accept').status_code==200
        assert inspect(fresh)['shots'][0]['status']=='Kept'
        assert post(fresh,'dispatch',request_id=gate['entity_id']).status_code==200
    assert len(provider['posts'])==1
    assert not (root/'checkpoint_assets.json').exists()


def test_changed_kept_shot_is_plainly_stale(board,provider):
    import copy
    from lib.trailer_edit import read_revision,save_revision
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    looks.approve_request(gate,root);post(board,'signed',gate_id=gate['request_id']);take=wait_take(board)
    decision=post(board,'decision',shot_id=shot,take_id=take['take_id'],decision='accept').json()['gate']
    looks.approve_request(decision,root);post(board,'signed',gate_id=decision['request_id'])
    current=read_revision(root);paper=copy.deepcopy(current['paper_cut'])
    paper['shots'][0]['purpose']='An invented keeper turns away from the doorway.'
    save_revision(root,paper,current['edit_revision_sha256'])
    state=inspect(board)['shots'][0]
    assert state['status']=='Needs a fresh look'
    assert state['takes'][0]['status']=='Needs a fresh look' and not state['takes'][0]['selected']
    assert post(board,'apply',shot_id=shot,take_id=take['take_id'],decision='accept').status_code==400
    assert len(provider['posts'])==1


def test_every_write_control_requires_the_writer_and_valid_origin(board,provider):
    client,root,_=board
    for action in ('quote','create','dispatch','decision','apply','signed','resolve'):
        token=client.headers.pop('x-frontlot-capability')
        assert post(board,action).status_code==401
        client.headers['x-frontlot-capability']=token
        client.headers['origin']='https://outside.invalid'
        assert post(board,action).status_code==403
        client.headers['origin']='http://127.0.0.1:4863'
    assert not provider['uploads'] and not provider['posts']


@pytest.mark.parametrize('field,value',[('prompt','A substituted shot.'),('references',[]),('model','other'),('duration_seconds',6),('max_cash_usd',1)])
def test_changed_signed_request_refuses_through_dispatch(board,provider,field,value):
    import json
    _,root,shot=board
    gate=post(board,'create',shot_id=shot,model=workspace().MODEL,duration_seconds=5).json()['gate']
    looks.approve_request(gate,root)
    path=root/'.footage-requests'/(gate['entity_id']+'.json')
    record=json.loads(path.read_text());record[field]=value;path.write_text(json.dumps(record))
    assert post(board,'dispatch',request_id=gate['entity_id']).status_code==400
    assert not provider['uploads'] and not provider['posts']

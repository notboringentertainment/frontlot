"""The five real reviews have visible, capability-checked operations."""
import json
import pytest
from fastapi.testclient import TestClient
from tests.lib.test_trimmed_trailer_contract import trimmed,human_terminal,checked_paper,no_service_contact
from tests.lib.look_lock_helpers import approve_request


def test_paper_stage_is_recorded_only_after_its_real_signature(trimmed,monkeypatch):
    import backlot.server as server
    from lib import trailer_paper_review as review
    monkeypatch.setattr(server,'PROJECTS_DIR',trimmed.parent)
    monkeypatch.setattr(server,'_PROJECTS_ROOT_STR',str(trimmed.parent.resolve()))
    saved=checked_paper(trimmed)
    headers={'origin':'http://127.0.0.1:4948','x-frontlot-capability':'invented-contract'}
    with TestClient(server.create_app(port=4948,capability_token='invented-contract')) as client:
        url=f'/api/project/{trimmed.name}/paper-cut/apply'
        assert client.post(url,json={}).status_code==403
        refused=client.post(url,json={},headers=headers)
        assert refused.status_code==400 and 'signed approval' in refused.json()['detail']
        approve_request(review.request_review(trimmed,saved['edit_revision_sha256']),trimmed)
        result=client.post(url,json={},headers=headers)
        assert result.status_code==200 and result.json()['message']=='Your signed paper-cut review is recorded.'
    cp=json.loads((trimmed/'checkpoint_script.json').read_text())
    assert cp['status']=='completed' and set(cp['artifacts'])=={'script','scene_plan'}


def test_trimmed_guidance_owes_only_reviews_that_exist(trimmed):
    from lib.trailer_contract import outstanding_stage_reviews
    words=' '.join(outstanding_stage_reviews(trimmed,'compose'))
    for required in ('Story:', 'Paper cut:', 'Rough cut:', 'Colour and sound:', 'protected'):
        assert required in words
    for dropped in ('Treatment:', 'Looks:', 'Cast portraits:', 'Visual references:', 'Shot list:', 'Footage and sound:', 'no control yet'):
        assert dropped not in words


def test_existing_trailer_updates_only_through_the_signed_screen(tmp_path,monkeypatch):
    from lib import trailer_edit,trailer_contract
    import backlot.server as server
    with monkeypatch.context() as old:
        old.setattr(trailer_edit,'CURRENT_VERSION','1.6')
        old.setattr(trailer_edit,'CURRENT_REF','authored-film@1.6')
        root=trailer_edit.create_trailer(tmp_path,'Invented earlier signed trailer',4,10)
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
    approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    headers={'origin':'http://127.0.0.1:4948','x-frontlot-capability':'invented-contract'}
    with TestClient(server.create_app(port=4948,capability_token='invented-contract')) as client:
        result=client.post(f'/api/project/{root.name}/trailer-contract/review',json={},headers=headers)
        assert result.status_code==200
        gate=result.json()
        assert trailer_contract.signed_trailer_pin(root).version=='1.6'
        detail=client.get(f'/api/project/{root.name}/gate/{gate["request_id"]}')
        assert detail.json()['packet']['trailer_test_contract']['decision']=='trim for the trailer test'
        # The same signing constructor used by the screen rechecks this pin.
        approve_request(gate,root)
        assert trailer_contract.signed_trailer_pin(root).version=='1.7'
        assert not list(root.glob('checkpoint_*.json'))


@pytest.mark.parametrize('bad',[None,{}, {'canon_pass':{}}])
def test_a_broken_pending_final_story_request_is_not_a_ready_signing_packet(trimmed,monkeypatch,bad):
    from backlot.delivery import signing_packet
    from lib import trailer_delivery as delivery
    checked_paper(trimmed)
    # Isolate the presentation boundary after the file checks. The actual
    # exported bytes and receipts are exercised in the five-signature test.
    item={'id':'a'*32,'final_review':{'issues_found':[],'metadata':{'boundary_samples':[]}}}
    monkeypatch.setattr(delivery,'read_export',lambda *args:item)
    monkeypatch.setattr(delivery,'_artifacts',lambda *args,**kwargs:{'final_review':item['final_review']})
    packet=signing_packet(trimmed,{'stage':'compose','entity_id':item['id'],'approval_record':{'final_story_review':bad}})
    assert packet.get('packet_error') is True
    assert 'final story comparison' in packet['error']
    assert 'play_url' not in packet


def test_earlier_stage_records_stay_saved_without_becoming_trailer_test_debts(trimmed):
    from backlot.state import load_board_state
    path=trimmed/'checkpoint_scene_plan.json'
    previous=json.dumps({'stage':'scene_plan','status':'in_progress','artifacts':{}}).encode()
    path.write_bytes(previous)
    state=load_board_state(trimmed)
    assert [row['label'] for row in state['stages']]==['Story','Paper cut','Rough cut','Colour and sound','Final file']
    assert path.read_bytes()==previous

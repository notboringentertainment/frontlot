"""Retirement uses real signed look history without activating a location."""
import json
import pytest
from tests.lib.test_story_ingest import root
from tests.lib import look_lock_helpers as helpers


def setup_locations(root, monkeypatch, count):
    monkeypatch.setattr(helpers, 'PROJECT', root.name)
    for name in ['first-room', 'second-room'][:count]:
        helpers.activate_look(root, helpers.location_look(name))
    req = {'request_id': 'retire-first-room', 'project_id': root.name,
           'kind': 'look_lock', 'stage': 'look_lock', 'scope': 'location:first-room',
           'entity_id': 'first-room', 'summary': 'Retire this invented location look.',
           'envelope': {'action': 'retire'}}
    # The full constructed envelope is bound when signing; leave the request
    # hint absent so it cannot stand in for authoritative history.
    from lib.look_ingest import active_look_for
    current = active_look_for(root, 'location', 'first-room')
    req['envelope'].update(entity_kind='location', look_hash=current.look_hash)
    (root/'.gate-requests'/f'{req["request_id"]}.json').write_text(json.dumps(req))
    return req


def test_retirement_can_repair_two_signed_active_locations(root, monkeypatch):
    from scripts.gate_approve import construct
    req = setup_locations(root, monkeypatch, 2)
    built = construct(root, req)
    if built.pre_commit_check:
        built.pre_commit_check()
    assert built.envelope['action'] == 'retire'
    # Activation must still refuse a second location at signing time.
    from lib.frontlot_location_source import require_slot
    with pytest.raises(ValueError, match='one approved location'):
        require_slot(root, 'third-room')
    helpers.approve_request(req, root)
    from lib.look_ingest import active_looks
    assert set(active_looks(root)) == {('location', 'second-room')}


def test_signed_retirement_finishes_without_location_activation(root, monkeypatch):
    from lib import frontlot_location_source, receipts
    from lib.look_ingest import active_looks
    req = setup_locations(root, monkeypatch, 1)
    calls = []
    def activation(*args):
        calls.append(args)
        raise AssertionError('Retirement must never activate a location.')
    monkeypatch.setattr(frontlot_location_source, 'apply', activation)
    signed = helpers.approve_request(req, root)
    assert not calls
    assert ('location', 'first-room') not in active_looks(root)
    archived = root/'.gate-requests/done'/f'{req["request_id"]}.json'
    assert json.loads(archived.read_text())['approval_receipt_id'] == signed['receipt_id']
    assert any(r['receipt_id'] == signed['receipt_id'] for r in receipts.verified_approvals(root, 'look_lock'))
    assert not (root/'cost_log.json').exists()

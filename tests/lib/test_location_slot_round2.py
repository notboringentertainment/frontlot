"""The first-location controls cannot occupy a second location slot."""
import json,yaml
import pytest
from tests.lib.test_story_ingest import root
from tests.lib.test_frontlot_location_source import form
from tests.lib import look_lock_helpers as helpers
from lib import frontlot_location_source as locations
from lib.look_ingest import active_looks
from scripts.gate_approve import construct,GateHandlerError

def external(root,tmp_path,*names):
    folder=tmp_path/'invented-external';folder.mkdir(exist_ok=True)
    for name in names:helpers.write_ticket(folder,helpers.location_look(name))
    data=yaml.safe_load((root/'project.yaml').read_text());data['wayfinder_root']=str(folder)
    (root/'project.yaml').write_text(yaml.safe_dump(data))
    return folder

def test_existing_location_cannot_prepare_a_second_subject(root,tmp_path):
    external(root,tmp_path,'first-room','second-room')
    first=locations.prepare_existing(root,'wayfinder','first-room')['gate'];helpers.approve_request(first,root)
    with pytest.raises(ValueError,match='one|location|another'):
        locations.prepare_existing(root,'wayfinder','second-room')
    assert len([key for key in active_looks(root) if key[0]=='location'])==1

def test_already_prepared_second_subject_cannot_be_signed(root,tmp_path):
    external(root,tmp_path,'first-room','second-room')
    first=locations.prepare_existing(root,'wayfinder','first-room')['gate']
    # An old pending request or a direct preparation cannot bypass signing.
    from lib.look_ingest import look_lock_request
    look=next(v['look'] for v in locations.external_sources(root) if v['entity_id']=='second-room')
    second=json.loads(look_lock_request(root,root.name,look,request_id='second-location-direct',
        ticket_path=next(v['ticket_path'] for v in locations.external_sources(root) if v['entity_id']=='second-room')).read_text())
    helpers.approve_request(first,root)
    with pytest.raises((ValueError,GateHandlerError),match='one|location|another'):helpers.approve_request(second,root)
    assert len(active_looks(root))==1

def test_external_source_arriving_after_display_is_rechecked_before_commit(root,tmp_path):
    req=locations.prepare(root,form())['gate'];built=construct(root,req)
    assert built.pre_commit_check
    external(root,tmp_path,'late-room')
    with pytest.raises((ValueError,GateHandlerError),match='source|existing|WriterOS|Wayfinder'):built.pre_commit_check()
    assert not active_looks(root)

def test_signed_location_is_applied_without_a_browser(root):
    req=locations.prepare(root,form())['gate'];helpers.approve_request(req,root)
    assert (root/'checkpoint_look_lock.json').is_file(),'Closing the browser could lose the signed look application.'
    packet=json.loads((root/'checkpoint_look_lock.json').read_text())
    assert packet['status']=='in_progress' and not packet['human_approved']
    assert len(packet['artifacts']['look_packet']['looks'])==1

def test_only_current_unsigned_location_request_stays_pending(root):
    first=locations.prepare(root,form())['gate'];data=form();data['text']+=' The door stays dark.'
    second=locations.prepare(root,data)['gate']
    assert not (root/'.gate-requests'/(first['request_id']+'.json')).exists()
    assert (root/'.gate-requests'/(second['request_id']+'.json')).is_file()

def test_interrupted_location_application_has_a_signature_only_recovery(root,monkeypatch):
    from backlot.footage_prereqs import snapshot
    from lib import receipts
    req=locations.prepare(root,form())['gate'];apply=locations.apply
    monkeypatch.setattr(locations,'apply',lambda *a:(_ for _ in ()).throw(OSError('Invented interrupted look filing.')))
    with pytest.raises(OSError,match='interrupted'):helpers.approve_request(req,root)
    before=receipts.verified_approvals(root,'look_lock');assert len(before)==1
    assert snapshot(root)['signed_locations_to_apply']==[req['request_id']]
    monkeypatch.setattr(locations,'apply',apply)
    apply(root,req['request_id']);apply(root,req['request_id'])
    assert not snapshot(root)['signed_locations_to_apply']
    assert receipts.verified_approvals(root,'look_lock')==before

def test_application_refuses_two_locations_left_by_an_earlier_build(root,tmp_path,monkeypatch):
    external(root,tmp_path,'first-room','second-room')
    # Reproduce history the reviewed code could create. Neither approval is
    # fabricated; the original signer writes both, with only the slot guard off.
    with monkeypatch.context() as old:
        old.setattr(locations,'require_slot',lambda *a:None)
        first=locations.prepare_existing(root,'wayfinder','first-room')['gate'];helpers.approve_request(first,root)
        second=locations.prepare_existing(root,'wayfinder','second-room')['gate'];helpers.approve_request(second,root)
    before=(root/'checkpoint_look_lock.json').read_bytes()
    with pytest.raises(ValueError,match='one approved location'):locations.apply(root,first['request_id'])
    assert (root/'checkpoint_look_lock.json').read_bytes()==before

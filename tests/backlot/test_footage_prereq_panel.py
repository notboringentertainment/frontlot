"""The location and picture controls prepare existing signatures, never spend."""
import base64,importlib.util,io,json
import pytest
from PIL import Image
from tests.lib.test_story_ingest import root
from tests.lib.test_frontlot_location_source import form
from tests.lib.look_lock_helpers import approve_request
from tests.backlot.test_early_paid_readiness import isolated_standins

def panel():
    assert importlib.util.find_spec('backlot.footage_prereqs'),'The prerequisite panel operations are missing.'
    from backlot import footage_prereqs
    return footage_prereqs

def picture(look):
    output=io.BytesIO();Image.new('RGB',(1024,576),(49,67,83)).save(output,format='PNG')
    return {'file':{'name':'Invented synthetic gallery.png','base64':base64.b64encode(output.getvalue()).decode()},
        'look':look,'origin_class':'imported_synthetic','machine_generated':True,'no_real_person':True,'origin_tool':'Invented local picture tool'}

@pytest.mark.parametrize('origin',['personal_photograph','casting_inspiration','unknown'])
def test_other_picture_origins_refuse_before_import(root,origin,isolated_standins):
    value=picture(None);value['origin_class']=origin
    with pytest.raises(ValueError,match='machine|person|synthetic|photograph'):panel().prepare_picture(root,value)
    assert all(not v for v in isolated_standins.values())

def test_picture_needs_a_real_current_signed_location_look(root,isolated_standins):
    from lib import frontlot_location_source as looks
    m=panel()
    with pytest.raises(ValueError,match='look'):m.prepare_picture(root,picture(None))
    result=looks.prepare(root,form());ref=result['look_refs'][0]
    with pytest.raises(ValueError,match='look|sign'):m.prepare_picture(root,picture(ref))
    approve_request(result['gate'],root);looks.apply(root,result['gate']['request_id'])
    made=m.prepare_picture(root,picture(ref));assert made['gate']['kind']=='reference_import'
    assert all(not v for v in isolated_standins.values())
    changed=form();changed['text']+=' A colder light fills the empty doorway.';looks.prepare(root,changed)
    with pytest.raises(ValueError,match='look|sign'):m.prepare_picture(root,picture(ref))

def test_price_draft_can_be_corrected_without_signing_wrong_figures(root):
    from lib import media_settings
    from tests.backlot.test_footage_price_settings import footage
    first=media_settings.prepare_footage(root,footage());second=media_settings.prepare_footage(root,{**footage(),'cash_usd_per_second':.12})
    assert first['request_id']!=second['request_id']
    assert (root/'.gate-requests/abandoned'/(first['request_id']+'.json')).is_file()
    assert not (root/'cost_log.json').exists()

def test_changed_writer_location_is_not_offered_as_an_approved_current_look(root):
    from lib import frontlot_location_source as looks
    from backlot.footage_controls import _looks
    first=looks.prepare(root,form());approve_request(first['gate'],root);looks.apply(root,first['gate']['request_id'])
    assert _looks(root)
    changed=form();changed['text']+=' The doorway now stays dark.';looks.prepare(root,changed)
    assert not _looks(root)

def test_signed_footage_figures_stop_at_their_expiry(root,monkeypatch,isolated_standins):
    from lib import media_settings,footage_requests
    from tests.backlot.test_footage_price_settings import footage
    data=footage();approve_request(media_settings.prepare_footage(root,data),root)
    monkeypatch.setattr(footage_requests.time,'time',lambda:data['expires_at']+1)
    with pytest.raises(ValueError,match='expired'):footage_requests.quote(root,footage_requests.MODEL,5)
    assert all(not v for v in isolated_standins.values()) and not (root/'cost_log.json').exists()

def test_five_second_clip_over_the_shared_ceiling_is_refused_plainly(root,monkeypatch,isolated_standins):
    from lib import frontlot_location_source as looks,story_ingest,trailer_edit,media_settings
    from tests.lib.test_trailer_edit import paper
    from tests.backlot.test_footage_price_settings import footage
    import backlot.server as server
    from fastapi.testclient import TestClient
    approve_request(story_ingest.supply(root,b'An invented empty gallery waits behind its doorway.')['gate'],root)
    draft=paper();draft['shots'][0]['duration_frames']=672;trailer_edit.save_revision(root,draft,None)
    made=looks.prepare(root,form());approve_request(made['gate'],root);looks.apply(root,made['gate']['request_id']);look=made['look_refs'][0]
    image=panel().prepare_picture(root,picture(look));receipt=approve_request(image['gate'],root)
    shot=trailer_edit.read_revision(root)['paper_cut']['shots'][0]
    panel().use_picture(root,{'receipt_id':receipt['receipt_id'],'shot_id':shot['id'],'look':look})
    approve_request(media_settings.prepare_footage(root,{**footage(),'cash_usd_per_second':3}),root)
    before=(root/'approvals.jsonl').read_bytes();monkeypatch.setattr(server,'PROJECTS_DIR',root.parent)
    with TestClient(server.create_app(port=4961,capability_token='invented-ceiling')) as client:
        from lib.footage_requests import MODEL
        headers={'origin':'http://127.0.0.1:4961','x-frontlot-capability':'invented-ceiling'}
        quote=client.post(f'/api/project/{root.name}/footage/quote',json={'shot_id':shot['id'],'model':MODEL,'duration_seconds':5},headers=headers)
        assert quote.status_code==200 and quote.json()['highest_usd']==15 and quote.json()['remaining_usd']==10
        response=client.post(f'/api/project/{root.name}/footage/create',json={'shot_id':shot['id'],'model':__import__('lib.footage_requests',fromlist=['MODEL']).MODEL,'duration_seconds':5,'looks':{trailer_edit.read_revision(root)['paper_cut']['shots'][0]['references'][0]:'location:trailer-location'}},headers={'origin':'http://127.0.0.1:4961','x-frontlot-capability':'invented-ceiling'})
    assert response.status_code==400 and 'ceiling' in response.json()['detail'],response.text
    assert (root/'approvals.jsonl').read_bytes()==before
    assert all(not v for v in isolated_standins.values())

"""The assembled cut stays editable; unusable media never gains approval."""
import copy
import pytest
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

def decline_picture(root,row):
    from lib import footage_requests as f
    looks.approve_request(f.request_decision(root,row['shot_id'],row['take_id'],'decline'),root)
    f.apply_decision(root,row['shot_id'],row['take_id'],'decline')

def test_declining_one_picture_keeps_the_whole_cut_on_screen(signed_cut):
    from lib import trailer_edit,cutting_view
    from backlot.cutting import snapshot
    root,provider,posts,_,_=signed_cut;before=trailer_edit.read_revision(root)
    decline_picture(root,before['timeline']['picture'][1])
    view=snapshot(root)
    assert len(view['timeline']['picture'])==3 and len(view['timeline']['audio'])==3
    assert view['timeline']['picture'][0]['take_id']==before['timeline']['picture'][0]['take_id']
    assert 'declined' in view['clips']['picture'][1]['problem'].lower()
    assert view['clips']['picture'][0]['problem'] is None
    with pytest.raises(ValueError,match='Picture 2.*declined'):
        cutting_view.save_cut(root,view['timeline'],view['revision'])
    with pytest.raises(ValueError,match='Picture 2.*declined'):
        cutting_view.render_preview(root,view['revision'])
    assert len(provider['posts'])==3 and len(posts)==1

def test_restore_uses_the_selected_cut_not_current_timeline(signed_cut):
    from lib import trailer_edit,cutting_view
    from backlot.cutting import snapshot
    root,provider,posts,_,_=signed_cut;original=trailer_edit.read_revision(root)
    value=cutting_view.editable(original['timeline']);value['picture'][0]['source_in_frame']=18
    changed=cutting_view.save_cut(root,value,original['edit_revision_sha256'])
    restored=trailer_edit.restore_revision(root,original['edit_revision_sha256'],changed['edit_revision_sha256'])
    assert restored['timeline']==original['timeline']
    decline_picture(root,restored['timeline']['picture'][1])
    now=trailer_edit.read_revision(root)
    restored=trailer_edit.restore_revision(root,original['edit_revision_sha256'],now['edit_revision_sha256'])
    view=snapshot(root)
    assert len(view['timeline']['picture'])==3 and len(view['timeline']['audio'])==3
    assert 'declined' in view['clips']['picture'][1]['problem'].lower()
    assert restored['revision_number']>changed['revision_number']
    assert len(provider['posts'])==3 and len(posts)==1

@pytest.mark.parametrize('medium',['picture','audio'])
@pytest.mark.parametrize('change',['declined','changed_file'])
def test_rough_approval_rechecks_every_sound_and_picture(signed_cut,monkeypatch,medium,change):
    from lib import trailer_edit,trailer_contract as c,audio_workspace as a
    from lib.pipeline_pin import pinned_pipeline
    from lib.canonical_json import record_sha256
    from lib import gates,receipts
    root,provider,posts,_,take=signed_cut;saved=trailer_edit.read_revision(root)
    # Stand in only for the unfinished-stage restriction; never for media or signing.
    monkeypatch.setattr(c,'validate_stage_contract',lambda *args,**kw:None)
    pin=pinned_pipeline(root,'authored-film');artifacts={'edit_decisions':saved}
    record=c.approval_record(root,pin,'edit',artifacts)
    artifact={'artifact_type':'trailer_stage','artifact_version':'1.0','artifact_digest':record_sha256(record),'migration_status':'reviewed'}
    with gates.handler_context():
        token=gates.mint_gate_token(root.name,'edit','trailer-stage',record_sha256(record),user_response='approve')
        receipts.record_human_approval(root,root.name,'edit','trailer-stage',record,token,'artifact_review',artifact=artifact)
    c.require_stage_approval(root,pin,'edit',artifacts)
    if change=='changed_file':
        row=saved['timeline'][medium][0]
        with (root/row['path']).open('ab') as f:f.write(b'invented change')
    elif medium=='audio':sign(root,'audio_take',a.decision_record(root,take['id'],'decline'),take['id'])
    else:decline_picture(root,saved['timeline']['picture'][1])
    label=('Picture '+('1' if change=='changed_file' else '2')) if medium=='picture' else 'Sound 1'
    reason=label+'.*'+('fresh look' if change=='changed_file' else 'declined')
    with pytest.raises(ValueError,match=reason):c.require_stage_approval(root,pin,'edit',artifacts)
    with pytest.raises(ValueError,match=reason):c.approval_record(root,pin,'edit',artifacts)
    assert len(provider['posts'])==3 and len(posts)==1

def test_sound_list_rejects_a_picture_with_plain_words(signed_cut):
    from lib import cutting_view,trailer_edit
    root,provider,posts,value,_=signed_cut;value=copy.deepcopy(value)
    value['audio'][0]['take_id']=value['picture'][0]['take_id']
    with pytest.raises(ValueError,match='Sound 1.*voice, music or sound effect'):
        cutting_view.validate_cut(root,value)
    assert len(provider['posts'])==3 and len(posts)==1

def test_take_catalogue_distinguishes_each_kept_take(signed_cut):
    from backlot.cutting import snapshot
    root,provider,posts,_,_=signed_cut;view=snapshot(root)
    assert len({c['label'] for c in view['pictures']})==3
    assert [c['take_number'] for c in view['pictures']]==[1,2,3]
    assert all(c['kept_at'] and c['thumbnail'] for c in view['pictures'])
    assert all(c['kept_at'] and c['take_number'] for c in view['sounds'])
    assert len(provider['posts'])==3 and len(posts)==1

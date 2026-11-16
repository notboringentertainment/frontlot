import copy
import pytest
from lib import receipts, trailer_edit
from tests.lib.test_trailer_edit import trailer, paper, signed_story, human_terminal
from tests.lib.look_lock_helpers import approve_request


def draft(root):
    signed_story(root)
    value=paper()
    value['lines']=[{'text':'  A lamp flickers.\n','start_frame':0,'duration_frames':24,'source_ref':'opening'}]
    value['cards']=[{'text':' THIS WINTER ','start_frame':48,'duration_frames':24}]
    return trailer_edit.save_revision(root,value,None)


def test_whole_paper_signature_is_exact_and_does_not_complete_stages(tmp_path):
    from lib import trailer_paper_review as review
    root=trailer(tmp_path);saved=draft(root)
    assert review.state(root)=='Needs your signature'
    request=review.request_review(root,saved['edit_revision_sha256'])
    record=request['approval_record']
    assert record['paper_cut']==saved['paper_cut']
    assert record['story_check']==saved['story_check']
    receipt=approve_request(request,root)
    assert receipt['scope']=='trailer-paper' and review.state(root)=='Signed by you'
    from lib.trailer_contract import outstanding_stage_reviews
    remaining=outstanding_stage_reviews(root,'edit')
    assert any(line.startswith('Paper cut:') and 'record that signed review' in line for line in remaining)
    assert not any(line.startswith(('Lines and title cards:','Shot list:')) for line in remaining)
    assert receipts.verified_generation_receipts(root)==[]
    import json
    cp=json.loads((root/'checkpoint_script.json').read_text())
    assert cp['status']=='in_progress' and not cp['human_approved']
    assert set(cp['artifacts'])=={'script','scene_plan'}
    assert not (root/'checkpoint_scene_plan.json').exists()
    # Selecting media or changing the later cut is not a rewrite of this paper.
    edited=copy.deepcopy(saved);edited['local_preview_note']='A local cut was prepared.'
    trailer_edit.save_edit_revision(root,edited,saved['edit_revision_sha256'])
    assert review.state(root)=='Signed by you'
    latest=trailer_edit.read_revision(root)
    changed=copy.deepcopy(latest['paper_cut']);changed['beats'][0]['text']='A different question.'
    trailer_edit.save_revision(root,changed,latest['edit_revision_sha256'])
    assert review.state(root)=='Needs a fresh look'


@pytest.mark.parametrize('change',['text','timing','story','request'])
def test_paper_changed_between_review_and_signing_is_refused(tmp_path,change):
    from lib import trailer_paper_review as review
    from scripts import gate_approve
    root=trailer(tmp_path);saved=draft(root)
    request=review.request_review(root,saved['edit_revision_sha256'])
    shown=gate_approve.construct(root,request)
    if change=='request':request['approval_record']['paper_cut']['cards'][0]['text']='Different words.'
    elif change=='story':
        import json
        path=root/'checkpoint_canon_ingest.json';cp=json.loads(path.read_text());cp['status']='in_progress';path.write_text(json.dumps(cp))
    else:
        value=copy.deepcopy(saved['paper_cut'])
        if change=='text':value['cards'][0]['text']=' A different season '
        else:value['cards'][0]['start_frame']=49
        trailer_edit.save_revision(root,value,saved['edit_revision_sha256'])
    with pytest.raises((ValueError,gate_approve.GateHandlerError),match='paper cut|paper-cut|story|changed'):
        built=gate_approve.construct(root,request)
        built.pre_commit_check()
    assert not [r for r in receipts.verified_approvals(root,'artifact_review') if r.get('scope')=='trailer-paper']


def test_unchecked_paper_cannot_be_offered_for_signature(tmp_path):
    from lib import trailer_paper_review as review
    root=trailer(tmp_path);saved=trailer_edit.save_revision(root,paper(),None)
    with pytest.raises(ValueError,match='approved story'):
        review.request_review(root,saved['edit_revision_sha256'])


@pytest.mark.parametrize('sign_changed',[False,True])
def test_restoring_earlier_paper_needs_its_own_new_review(tmp_path,sign_changed):
    from lib import trailer_paper_review as review
    root=trailer(tmp_path);first=draft(root)
    approve_request(review.request_review(root,first['edit_revision_sha256']),root)
    changed=copy.deepcopy(first['paper_cut']);changed['beats'][0]['text']='Another opening question.'
    second=trailer_edit.save_revision(root,changed,first['edit_revision_sha256'])
    if sign_changed:approve_request(review.request_review(root,second['edit_revision_sha256']),root)
    restored=trailer_edit.restore_revision(root,first['edit_revision_sha256'],second['edit_revision_sha256'])
    assert review.state(root)=='Needs a fresh look'
    approve_request(review.request_review(root,restored['edit_revision_sha256']),root)
    assert review.state(root)=='Signed by you'

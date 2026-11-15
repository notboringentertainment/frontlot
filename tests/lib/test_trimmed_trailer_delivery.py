"""Distinct rough, colour/mix and exact-file signatures for the trailer test."""
import copy
import json
import pytest
from tests.lib.test_cutting_signed import signed_cut as original_cut
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign


@pytest.fixture
def trailer_cut(film,provider,monkeypatch,request):
    from lib import pipeline_pin,story_ingest,trailer_edit,trailer_paper_review
    from tests.lib.look_lock_helpers import approve_request
    root,shot=film
    path=pipeline_pin.prepare_migration_request(root,root.name,'authored-film','1.7')
    approve_request(json.loads(path.read_text()),root)
    story=story_ingest.supply(root,b'The lamp is still lit. Someone is waiting.',protected=['The lamp is still lit.'])
    approve_request(story['gate'],root)
    current=trailer_edit.read_revision(root)
    paper=copy.deepcopy(current['paper_cut'])
    paper['shots'][0]['purpose']='A gallery keeper waits beside the lamp.'
    trailer_edit.save_revision(root,paper,current['edit_revision_sha256'])
    cut=original_cut.__wrapped__((root,shot),provider,monkeypatch,request)
    saved=trailer_edit.read_revision(root)
    approve_request(trailer_paper_review.request_review(root,saved['edit_revision_sha256']),root)
    trailer_paper_review.complete(root)
    return cut


def test_colour_and_mix_needs_its_own_signature_of_the_saved_cut(trailer_cut):
    from lib import trailer_delivery as delivery,trailer_edit
    from tests.lib.look_lock_helpers import approve_request
    root,calls,posts,*_=trailer_cut;saved=trailer_edit.read_revision(root)
    rough=delivery.request_rough(root,saved['edit_revision_sha256']);approve_request(rough,root)
    assert delivery.complete_stage(root,'edit')=='Your signed stage approval is recorded.'
    saved=trailer_edit.read_revision(root)
    with pytest.raises(ValueError,match='colour and sound'):delivery.require_colour(root)
    gate=delivery.request_colour(root,saved['edit_revision_sha256']);approve_request(gate,root)
    assert delivery.complete_stage(root,'colour_mix')=='Your signed stage approval is recorded.'
    delivery.require_colour(root)
    from lib.cutting_view import save_cut,editable
    changed=editable(saved['timeline']);changed['mix']['speech_margin_db']+=1
    save_cut(root,changed,saved['edit_revision_sha256'])
    with pytest.raises(ValueError,match='rough|colour|review'):delivery.require_colour(root)
    assert len(calls['posts'])==3 and len(posts)==1


def test_final_story_questions_keep_every_protected_line_and_literal_words(trailer_cut):
    from lib import trailer_delivery as delivery
    root,calls,posts,*_=trailer_cut
    questions=delivery.story_questions(root)
    assert questions['protected_lines']==['The lamp is still lit.']
    assert isinstance(questions['locks'],list) and questions['tone']
    assert len(questions['story_sha256'])==64
    assert len(calls['posts'])==3 and len(posts)==1


def test_delivery_summary_uses_one_saved_version_for_all_five_reviews(trailer_cut,monkeypatch):
    from lib import trailer_delivery as delivery
    root,calls,posts,*_=trailer_cut;reads=[];original=delivery._saved
    def counted(*args,**kwargs):reads.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(delivery,'_saved',counted)
    result=delivery.snapshot(root)
    assert result['rough_status']=='Needs your signature'
    assert reads==[1], 'A refresh must use one checked saved version, without repeatedly rebuilding it for the new review.'
    assert result['colour_status']=='Waiting for the rough-cut signature'
    assert len(calls['posts'])==3 and len(posts)==1


def test_final_file_checks_use_one_current_saved_cut_for_both_signatures(trailer_cut,monkeypatch):
    from lib import trailer_delivery as delivery,trailer_edit
    from tests.lib.look_lock_helpers import approve_request
    root,calls,posts,*_=trailer_cut
    approve_request(delivery.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256']),root)
    approve_request(delivery.request_colour(root,trailer_edit.read_revision(root)['edit_revision_sha256']),root)
    reads=[];original=delivery._saved
    def counted(*args,**kwargs):reads.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(delivery,'_saved',counted)
    sentinel={'same_current_material':True}
    monkeypatch.setattr(delivery,'_file_artifacts',lambda *args,**kwargs:sentinel)
    assert delivery._artifacts(root,'a'*32) is sentinel
    assert reads==[1], 'One operation must check one current cut and both genuine signatures, without rebuilding the cut twice.'
    assert len(calls['posts'])==3 and len(posts)==1


@pytest.mark.parametrize('changed_after_approval',[False,True])
def test_reveal_checks_approved_material_once_and_rechecks_file_bytes_before_finder(tmp_path,monkeypatch,changed_after_approval):
    import hashlib
    from lib import trailer_delivery as delivery
    root=tmp_path/'invented-reveal';root.mkdir();path=root/'trailer.mp4';path.write_bytes(b'invented local bytes')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    pair={'render_report':{'outputs':[{'path':'trailer.mp4'}],'export_sha256':digest},'final_review':{'status':'pass'}}
    checked=[];opened=[]
    def artifacts(*args,**kwargs):
        checked.append(1)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('The final file changed.')
        return pair
    def approved(*args,**kwargs):
        if changed_after_approval:path.write_bytes(b'changed after the signed-material check')
        return 'Approved by you'
    monkeypatch.setattr(delivery,'_artifacts',artifacts)
    monkeypatch.setattr(delivery,'_approval_state',approved)
    monkeypatch.setattr(delivery.subprocess,'run',lambda args,**kwargs:opened.append(args))
    if changed_after_approval:
        with pytest.raises(ValueError,match='changed|fresh look'):delivery.reveal(root,'a'*32)
        assert opened==[]
    else:
        assert 'shown in Finder' in delivery.reveal(root,'a'*32)['message']
        assert opened==[['open','-R',str(path)]]
    assert checked==[1], 'Reveal must reuse the exact material it just checked, then hash the real file again.'


def final_answers(questions):
    return {'story_sha256':questions['story_sha256'],'canon_pass':{
        'status':'pass','unresolved_flags':[],
        'protected_lines':[{'line':line,'present_verbatim':True,'audible':True} for line in questions['protected_lines']],
        'locks':[{'lock_id':row['id'],'verdict':'honored'} for row in questions['locks']],
        'continuity_spotchecks':[{'subject':name,'verdict':'consistent'} for name in questions['entities']],
        'tone':{'must_never_feel_like_verdict':'The invented test keeps the supplied story’s waiting mood.'}}}


def test_five_real_signatures_finish_the_trimmed_trailer_and_nothing_else(trailer_cut,monkeypatch):
    from lib import trailer_delivery as delivery,trailer_edit,receipts
    from lib.checkpoint import get_completed_stages
    from tests.lib.look_lock_helpers import approve_request
    # The smaller cutting fixture deliberately has an uncovered final second
    # and a pure-tone effect. Use the delivery fixture's full music bed,
    # distinct noise effect and three genuinely signed cards for this pass.
    from tests.backlot.test_delivery_browser import delivery_cut
    root,calls,posts=delivery_cut.__wrapped__(trailer_cut)
    dropped=('proposal','look_lock','headshots','visual_bible','scene_plan','assets')
    earlier={p:p.read_bytes() for stage in dropped if (p:=root/f'checkpoint_{stage}.json').exists()}
    gate=delivery.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256']);approve_request(gate,root)
    assert delivery.complete_stage(root,'edit')=='Your signed stage approval is recorded.'
    revision=trailer_edit.read_revision(root)['edit_revision_sha256']
    approve_request(delivery.request_colour(root,revision),root)
    assert delivery.complete_stage(root,'colour_mix')=='Your signed stage approval is recorded.'
    item=delivery.export(root,revision)
    assert item['final_review']['status']=='pass',item['final_review']['issues_found']
    assert get_completed_stages(root.parent,root.name,'authored-film@1.7')==['canon_ingest','script','edit','colour_mix']
    before=len(receipts.verified_approvals(root,'artifact_review'))
    with pytest.raises(ValueError,match='final story comparison'):delivery.request_final(root,item['id'])
    answers=final_answers(delivery.story_questions(root));missing=copy.deepcopy(answers)
    missing['canon_pass']['protected_lines'][0]['audible']=False
    with pytest.raises(ValueError,match='protected line'):delivery.request_final(root,item['id'],missing)
    assert len(receipts.verified_approvals(root,'artifact_review'))==before
    gate=delivery.request_final(root,item['id'],answers)
    assert delivery.final_state(root,item['id'])=='Needs your signature'
    approve_request(gate,root)
    assert delivery.final_state(root,item['id'])=='Approved by you'
    assert delivery.complete_stage(root,'compose',item['id'])=='Your signed stage approval is recorded.'
    assert get_completed_stages(root.parent,root.name,'authored-film@1.7')==['canon_ingest','script','edit','colour_mix','compose']
    # This fixture deliberately signed an update of an earlier trailer.
    # Its old records remain on disk; none becomes an approval of this pin.
    assert all(p.read_bytes()==raw for p,raw in earlier.items())
    assert not [r for r in receipts.verified_approvals(root,'artifact_review')
                if r.get('stage') in dropped and r.get('record',{}).get('pipeline',{}).get('version')=='1.7']
    row=receipts.verified_approvals(root,'artifact_review')[-1]
    assert row['record']['final_story_review']==answers
    assert 'canon_pass' not in item['final_review']['checks'], 'A technical report must never claim the writer’s findings.'
    assert delivery.snapshot(root)['outstanding_requirements']==[]
    opened=[];original_run=delivery.subprocess.run
    def local_run(args,*values,**options):
        if args[:2]==['open','-R']:
            opened.append(args[2]);return delivery.subprocess.CompletedProcess(args,0)
        return original_run(args,*values,**options)
    monkeypatch.setattr(delivery.subprocess,'run',local_run)
    reads=[];original_artifacts=delivery._artifacts
    def counted(*args,**kwargs):reads.append(1);return original_artifacts(*args,**kwargs)
    monkeypatch.setattr(delivery,'_artifacts',counted)
    assert 'shown in Finder' in delivery.reveal(root,item['id'])['message']
    assert opened==[str(root/item['render_report']['outputs'][0]['path'])] and reads==[1]
    assert len(calls['posts'])==3 and len(posts)==1

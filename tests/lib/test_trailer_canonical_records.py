import copy
import json
import pytest
from lib import trailer_edit,receipts
from schemas.artifacts import validate_artifact
from tests.lib.test_trailer_edit import trailer,paper,signed_story,human_terminal
from tests.lib.look_lock_helpers import approve_request


@pytest.mark.parametrize('with_words',[False,True])
def test_saving_paper_writes_exact_unapproved_canonical_records(tmp_path,with_words):
    root=trailer(tmp_path);signed_story(root);draft=paper()
    if with_words:
        draft['lines']=[{'text':'  The lamp flickers.\n','start_frame':3,'duration_frames':25,'source_ref':'opening'}]
        draft['cards']=[{'text':' THIS WINTER ','start_frame':48,'duration_frames':24}]
    before=receipts.verified_approvals(root,'artifact_review')
    saved=trailer_edit.save_revision(root,draft,None)
    for stage,name in [('script','script'),('scene_plan','scene_plan')]:
        cp=json.loads((root/f'checkpoint_{stage}.json').read_text())
        artifact=cp['artifacts'][name]
        validate_artifact(name,artifact)
        assert artifact==json.loads((root/'artifacts'/f'{name}.json').read_text())
        assert artifact['metadata']['paper_cut']==saved['paper_cut']
        assert cp['status']=='in_progress' and cp['human_approved'] is False
    script=json.loads((root/'artifacts/script.json').read_text())
    assert [r['text'] for r in script['sections']]==[r['text'] for r in draft['lines']+draft['cards']]
    assert [(r['start_seconds'],r['end_seconds']) for r in script['sections']]==[(r['start_frame']/24,(r['start_frame']+r['duration_frames'])/24) for r in draft['lines']+draft['cards']]
    before_files={p:p.read_bytes() for p in root.glob('checkpoint_*.json')}
    trailer_edit.save_revision(root,draft,saved['edit_revision_sha256'])
    assert all(p.read_bytes()==value for p,value in before_files.items()),'Unchanged facts do not make new checkpoint versions.'
    assert receipts.verified_approvals(root,'artifact_review')==before
    assert trailer_edit.paper_state(root)['usable_downstream']


def test_canonical_records_reference_the_real_existing_settings_signature(tmp_path):
    import hashlib
    from lib.media_spending import gate_request
    root=trailer(tmp_path);signed_story(root)
    config_record={'config_sha256':hashlib.sha256((root/'project.yaml').read_bytes()).hexdigest()}
    receipt=approve_request(gate_request(root,'config','project_config',config_record,'Review these invented test settings.'),root)
    trailer_edit.save_revision(root,paper(),None)
    records=json.loads((root/'artifacts/script.json').read_text())
    assert records['metadata']['signed_settings']=={'config_sha256':config_record['config_sha256'],'approval_receipt_id':receipt['receipt_id']}
    decisions=json.loads((root/'decision_log.json').read_text())['decisions']
    assert len(decisions)==1 and decisions[0]['user_approved'] is True
    assert config_record['config_sha256'] in decisions[0]['question_id']
    assert receipt['receipt_id'] in decisions[0]['decision_id']
    assert len(receipts.verified_approvals(root,'config'))==1


def test_a_failed_record_projection_can_be_replayed_without_changing_the_saved_paper(tmp_path,monkeypatch):
    from lib import trailer_records
    root=trailer(tmp_path);signed_story(root)
    original=trailer_records.write_checkpoint
    def interrupt(*args,**kwargs):raise OSError('invented interruption after the paper commit')
    monkeypatch.setattr(trailer_records,'write_checkpoint',interrupt)
    with pytest.raises(OSError,match='invented interruption'):
        trailer_edit.save_revision(root,paper(),None)
    saved=trailer_edit.read_revision(root)
    assert saved and saved['paper_cut']['beats']==paper()['beats']
    monkeypatch.setattr(trailer_records,'write_checkpoint',original)
    assert trailer_edit.save_revision(root,paper(),saved['edit_revision_sha256'])==saved
    assert json.loads((root/'checkpoint_script.json').read_text())['status']=='in_progress'

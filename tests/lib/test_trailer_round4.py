import copy,json
import pytest
from lib.canon_enforcement import check_trailer_text
from lib import trailer_edit as edit
from tests.lib.test_trailer_edit import trailer,paper
from tests.lib.test_trailer_round3_editor import collection_canon,row

@pytest.mark.parametrize('text',['Nobody stays forever.','THE EXHIBIT STAYS OPEN','Please leave before the rain.','We stay together.','The doors open tomorrow.','Please bring a coat.','Nobody leaves hungry.','We open at noon.','Leave the parcel here.','Before the rain comes.','The rain stays outside.','We leave nothing behind.','Run to brunch.','Cannot yet say.','Ego homes are bright.'])
def test_honest_words(text):
    canon=collection_canon();canon['protected_lines'] += [{'line':x} for x in ['Stay.','Run.','Not yet.','Go home.']]
    assert not check_trailer_text({'lines':[row(text,ref='')]},canon)[0]

@pytest.mark.parametrize('parts', [['Before we open, please leave.'],['Please leave.'],['Please leave','before we open.'],['Tonight please leave before we open now.']])
def test_mechanical_versions(parts):
    errors,_=check_trailer_text({'lines':[row(t,ref='') for t in parts]},collection_canon())
    assert errors and 'Please leave before we open.' in errors[0]

@pytest.mark.parametrize('key',['lines','cards'])
def test_three_part_spoiler(key):
    errors,_=check_trailer_text({key:[row(t,i*24,ref='') for i,t in enumerate(['THE CARETAKER','HAS BEEN DEAD','SINCE THE MUSEUM CLOSED'])]},collection_canon())
    assert errors

def test_synonyms_are_not_claimed_cleared(tmp_path,monkeypatch):
    from tests.lib.test_trailer_edit import signed_story
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=trailer(tmp_path);signed_story(root,collection_canon());p=paper();p['lines']=[row('Kindly depart before we open.')]
    value=edit.save_revision(root,p,None)
    assert 'cannot recognise every rewording' in value['story_check']['message']
    assert edit.paper_state(root)['protected_lines']

@pytest.mark.parametrize('channel',['audio','cards','custom'])
def test_later_timing_without_footage_is_kept(tmp_path,monkeypatch,channel):
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=trailer(tmp_path);p=paper();p['cards']=[row('A TITLE',ref='')]
    a=edit.save_revision(root,p,None);later=json.loads(json.dumps(a))
    if channel=='cards':later['timeline']['cards'][0]['start_frame']=12
    else:later['timeline'][channel]=[{'id':'score','start_frame':12,'duration_frames':24}]
    later['colour_notes']={'keep':'warm'}
    b=edit.save_edit_revision(root,later,a['edit_revision_sha256'])
    p=copy.deepcopy(b['paper_cut']);p['beats'][0]['text']='A changed hook'
    c=edit.save_revision(root,p,b['edit_revision_sha256'])
    assert c['timeline']==b['timeline'] and c['colour_notes']==b['colour_notes']
    assert edit.paper_state(root)['needs_review']

def test_incomplete_creation_is_truthful(tmp_path):
    from lib.checkpoint import init_project
    root=init_project('early',title='An early draft',pipeline_type='authored-film@1.6',pipeline_dir=tmp_path,target_duration_seconds=28)
    assert 'cannot be completed' in edit.creation_state(root)['message']
    with pytest.raises(ValueError,match='another name'):edit.repair_creation(root)

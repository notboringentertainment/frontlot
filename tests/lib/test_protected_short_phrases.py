import pytest
from lib.canon_enforcement import check_trailer_text
from lib import trailer_edit as edit
from tests.lib.test_trailer_edit import trailer,paper,signed_story,human_terminal

@pytest.mark.parametrize('text,protected',[('I know.','I know what you did.'),('THIS WINTER','Not this winter.'),('Please leave.','Please leave before we open.'),('leave before we','Please leave before we open.')])
@pytest.mark.parametrize('key',['lines','cards'])
def test_short_runs_save_with_named_warning(tmp_path,text,protected,key):
    root=trailer(tmp_path)
    signed_story(root,canon(protected))
    p=paper();p[key]=[row(text)]
    saved=edit.save_revision(root,p,None)
    check=saved['story_check']
    assert not check['errors'] and len(check['warnings'])==1
    assert protected in check['warnings'][0]
    assert ('Line 1' if key=='lines' else 'Card 1') in check['warnings'][0]
    assert saved['timeline']['picture'] and saved['unfinished']

def row(text):return {'text':text,'start_frame':0,'duration_frames':24}

def canon(protected):
    from tests.lib.test_trailer_round3_editor import collection_canon
    value=collection_canon();value['locks']=[]
    value['protected_lines']=[{'line':protected,'source_path':'invented.md'}]
    return value

@pytest.mark.parametrize('text,protected',[('leave before we open','Please leave before we open.'),('know I now','I know now.'),('Please leave before we open now','Please leave before we open.'),('Please leave before we close','Please leave before we open.')])
def test_other_refusals_stay_refused(text,protected):
    errors,_=check_trailer_text({'lines':[row(text)]},canon(protected))
    assert errors and protected in errors[0]

def test_single_word_and_exact_quote_have_no_warning():
    for text in ['The winter sky glows.','Not this winter.']:
        assert check_trailer_text({'lines':[row(text)]},canon('Not this winter.'))==([],[])

def test_split_line_stays_refused():
    errors,_=check_trailer_text({'lines':[row('Please leave')],'cards':[dict(row('before we open.'),start_frame=24)]},canon('Please leave before we open.'))
    assert any('together' in e for e in errors)

def test_warning_does_not_clear_unchecked_story(tmp_path):
    import json
    root=trailer(tmp_path)
    signed_story(root,canon('I know what you did.'))
    path=root/'checkpoint_canon_ingest.json';cp=json.loads(path.read_text());cp['status']='in_progress';path.write_text(json.dumps(cp))
    p=paper();p['lines']=[row('I know.')]
    saved=edit.save_revision(root,p,None)
    assert saved['story_check']['status']=='unchecked'
    assert 'not yet checked' in saved['story_check']['message']
    assert saved['story_check']['warnings']

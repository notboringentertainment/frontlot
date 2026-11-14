"""Exact invented story bytes use the existing signed canon checkpoint."""
import hashlib,json,importlib.util
from pathlib import Path
import pytest
from tests.lib.look_lock_helpers import approve_request

TEXT=b'  A gallery waits.\r\nThe gallery is closed.\r\nKeep the light on.\r\nThe keeper is the thief.  \r\n'

@pytest.fixture
def story():
    assert importlib.util.find_spec('lib.story_ingest'),'The deterministic story adapter is missing.'
    from lib import story_ingest
    return story_ingest

@pytest.fixture
def root(tmp_path,monkeypatch):
    from lib.trailer_edit import create_trailer
    monkeypatch.setattr('lib.paths.PROJECTS_DIR',tmp_path)
    monkeypatch.setattr('scripts.gate_approve.require_tty',lambda:None)
    root=create_trailer(tmp_path,'Invented story approval',28,10)
    approve_request(json.loads(next((root/'.gate-requests').glob('*.json')).read_text()),root)
    return root

def supply(story,root,raw=TEXT,**kw):
    return story.supply(root,raw,protected=['Keep the light on.'],spoilers=['The keeper is the thief.'],**kw)

def test_exact_bytes_and_signature_only_complete_story(story,root):
    from lib.checkpoint import read_checkpoint
    from lib import receipts
    before=(root/'approvals.jsonl').read_bytes()
    result=supply(story,root)
    assert (root/result['path']).read_bytes()==TEXT
    assert result['sha256']==hashlib.sha256(TEXT).hexdigest()
    cp=read_checkpoint(root.parent,root.name,'canon_ingest')
    assert cp['status']=='awaiting_human' and not cp['human_approved']
    assert (root/'approvals.jsonl').read_bytes()==before
    approve_request(result['gate'],root)
    cp=read_checkpoint(root.parent,root.name,'canon_ingest')
    assert cp['status']=='completed' and cp['human_approved']
    assert cp['artifacts']['canon_packet']['structure']['beats'][0]['summary']==TEXT.decode()
    assert not (root/'cost_log.json').exists()
    assert not (root/'generation_receipts.jsonl').exists()
    assert not (root/'checkpoint_assets.json').exists()

def test_replacement_keeps_source_and_invalidates_paper_until_signed_and_checked(story,root):
    from lib import trailer_edit
    from tests.lib.test_trailer_edit import paper
    first=supply(story,root);approve_request(first['gate'],root)
    draft=paper();draft['shots'][0]['duration_frames']=672
    draft['lines']=[{'text':'The gallery is closed.','start_frame':0,'duration_frames':72,'source_ref':'supplied-story'}]
    old=trailer_edit.save_revision(root,draft,None)
    assert trailer_edit.paper_state(root)['usable_downstream']
    newer=supply(story,root,TEXT+b'Another footstep.\n')
    assert (root/first['path']).read_bytes()==TEXT
    assert not trailer_edit.paper_state(root)['usable_downstream']
    assert trailer_edit.paper_state(root)['needs_review']
    approve_request(newer['gate'],root)
    assert not trailer_edit.paper_state(root)['usable_downstream']
    story.check_paper(root)
    assert trailer_edit.paper_state(root)['usable_downstream']
    assert trailer_edit.read_revision(root,old['edit_revision_sha256'])==old

@pytest.mark.parametrize('raw',[b'',b' \r\n\t',b'\x89PNG\x00bad',b'\xff\xfea',b'a'*131073])
def test_invalid_text_refused_before_checkpoint_or_approval(story,root,raw):
    with pytest.raises(ValueError):story.supply(root,raw)
    assert not (root/'checkpoint_canon_ingest.json').exists()

@pytest.mark.parametrize('change',['bytes','checkpoint','foreign'])
def test_changed_or_foreign_story_cannot_use_signature(story,root,tmp_path,monkeypatch,change):
    from lib.checkpoint import read_checkpoint
    from lib.trailer_edit import create_trailer
    result=supply(story,root);approve_request(result['gate'],root)
    if change=='bytes':(root/result['path']).write_bytes(TEXT+b'Changed.')
    elif change=='checkpoint':
        path=root/'checkpoint_canon_ingest.json';value=json.loads(path.read_text())
        value['artifacts']['canon_packet']['structure']['beats'][0]['summary']='Changed.'
        path.write_text(json.dumps(value))
    else:
        other=create_trailer(tmp_path,'Another invented trailer',28,10)
        approve_request(json.loads(next((other/'.gate-requests').glob('*.json')).read_text()),other)
        from shutil import copytree,copyfile
        copytree(root/'sources',other/'sources',dirs_exist_ok=True)
        copyfile(root/'checkpoint_canon_ingest.json',other/'checkpoint_canon_ingest.json')
        root=other
    cp=read_checkpoint(root.parent,root.name,'canon_ingest')
    assert cp is None or cp['status']!='completed' or not cp.get('human_approved')

def test_existing_protected_and_spoiler_checks_are_used(story,root):
    from lib.canon_enforcement import check_trailer_text
    result=supply(story,root);approve_request(result['gate'],root)
    canon=json.loads((root/'checkpoint_canon_ingest.json').read_text())['artifacts']['canon_packet']
    paper={'lines':[{'text':'Keep the lamp on.'}],'cards':[{'text':'The keeper is the thief.'}]}
    errors,_=check_trailer_text(paper,canon)
    assert any('protected line' in x for x in errors) and any('reveals' in x for x in errors)
    errors,warnings=check_trailer_text({'lines':[{'text':'An invented sentence elsewhere.'}],'cards':[]},canon)
    assert errors or any('not an exact passage' in x for x in warnings)

def test_old_request_cannot_approve_new_story(story,root):
    from scripts.gate_approve import construct,GateHandlerError
    first=supply(story,root)
    supply(story,root,TEXT+b'New ending.\n')
    with pytest.raises(GateHandlerError):construct(root,first['gate'])

def test_source_annotations_must_be_exact_passages(story,root):
    with pytest.raises(ValueError,match='exact passage'):
        story.supply(root,TEXT,protected=['Changed wording.'])

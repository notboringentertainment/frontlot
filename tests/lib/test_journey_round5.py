"""Identical paper operations preserve a signed cut; real edits still invalidate it."""
import copy
import pytest
from tests.lib.test_journey_round4 import earlier_restore,older_cut,film,provider,looks,workspace,sign,human_terminal
from tests.lib.test_trimmed_trailer_delivery import trailer_cut

@pytest.fixture(params=['earlier','current'])
def reviewed(request,monkeypatch):
    if request.param=='earlier':root=request.getfixturevalue('earlier_restore')
    else:
        from tests.backlot.test_delivery_browser import delivery_cut
        from tests.lib.test_delivery_export import approve_rough
        root,*_=delivery_cut.__wrapped__(request.getfixturevalue('trailer_cut'));approve_rough(root)
    calls=[]
    def refuse(*a,**k):calls.append(1);raise AssertionError('Paper operations must not contact a service.')
    monkeypatch.setattr('requests.get',refuse);monkeypatch.setattr('requests.post',refuse)
    yield root
    assert calls==[]

def test_identical_paper_routes_keep_rough_cards_and_sounds(reviewed,monkeypatch):
    from lib import trailer_edit as edit,story_ingest,cutting_view as cutting,trailer_delivery as delivery,trailer_titles as titles,audio_workspace as audio
    root=reviewed;saved=edit.read_revision(root)
    source=next(v for v in reversed(saved['revision_history']) if not v.get('cutting_view') and v.get('paper_cut')==saved['paper_cut'])
    raw=(root/'artifacts/edit_decisions.json').read_bytes()
    checkpoints={p:p.read_bytes() for p in root.glob('checkpoint_*.json')}
    approvals=(root/'approvals.jsonl').read_bytes()
    assert delivery.rough_state(root)=='Signed by you'
    assert edit.save_revision(root,copy.deepcopy(saved['paper_cut']),saved['edit_revision_sha256'])==saved
    story_ingest.check_paper(root)
    with pytest.raises(ValueError,match='another window'):
        edit.save_revision(root,saved['paper_cut'],source['edit_revision_sha256'])
    with pytest.raises(ValueError,match='another window'):
        edit.restore_revision(root,source['edit_revision_sha256'],source['edit_revision_sha256'])
    original=edit._commit
    def unchanged_commit(*a,**k):pytest.fail('Identical paper must not create a changed edit revision.')
    monkeypatch.setattr(edit,'_commit',unchanged_commit)
    restored=edit.restore_revision(root,source['edit_revision_sha256'],saved['edit_revision_sha256'])
    monkeypatch.setattr(edit,'_commit',original)
    assert restored==saved and (root/'artifacts/edit_decisions.json').read_bytes()==raw
    assert all(p.read_bytes()==b for p,b in checkpoints.items())
    assert (root/'approvals.jsonl').read_bytes()==approvals
    assert edit.paper_state(root)['usable_downstream']
    assert delivery.rough_state(root)=='Signed by you'
    assert [titles.status(root,c) for c in restored['timeline']['cards']]==['Signed by you']*3
    for row in restored['timeline']['audio']:audio.require_accepted(root,row['take_id'])
    changed=cutting.editable(restored['timeline']);changed['picture'][0]['colour']['brightness']+=5
    cutting.save_cut(root,changed,restored['edit_revision_sha256'])
    assert delivery.rough_state(root)=='Needs a fresh look'
    assert (root/'approvals.jsonl').read_bytes()==approvals

@pytest.mark.parametrize('operation',['save','restore'])
def test_genuine_paper_words_retire_dependent_voice(reviewed,operation):
    from lib import trailer_edit as edit,trailer_delivery as delivery,audio_workspace as audio
    root=reviewed;saved=edit.read_revision(root)
    if operation=='restore':
        source=next(v for v in saved['revision_history'] if not v.get('cutting_view') and v.get('paper_cut',{}).get('lines')!=saved['paper_cut']['lines'])
        result=edit.restore_revision(root,source['edit_revision_sha256'],saved['edit_revision_sha256'])
        assert result['paper_cut']==source['paper_cut']
    else:
        paper=copy.deepcopy(saved['paper_cut']);paper['lines'][0]['text']='Someone is waiting.'
        result=edit.save_revision(root,paper,saved['edit_revision_sha256'])
        assert result['paper_cut']==paper
    assert result.get('paper_cut_pending') and not edit.paper_state(root)['usable_downstream']
    assert delivery.rough_state(root)=='Needs a fresh look'
    voice=next(r for r in result['timeline']['audio'] if audio.read(root,r['take_id'])['kind']=='speech')
    with pytest.raises(ValueError):audio.require_accepted(root,voice['take_id'])

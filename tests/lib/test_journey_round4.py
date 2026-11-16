"""Paper-only restores keep source identities; real word changes remain changes."""
import json,os
from pathlib import Path
import pytest
from tests.lib.test_journey_round2 import older_cut,film,provider,looks,workspace,sign,human_terminal

@pytest.fixture
def earlier_restore(request,monkeypatch):
    manifest=os.environ.get('JOURNEY_R4_MANIFEST')
    if manifest:
        value=json.loads(Path(manifest).read_text());assert value['producer']=='56ad1a9'
        root=Path(value['root']);monkeypatch.setenv('OPENMONTAGE_GATES_DIR',value['gates'])
        monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
    else:
        from tests.backlot.test_delivery_browser import delivery_cut
        from tests.lib.test_delivery_export import approve_rough
        root,*_=delivery_cut.__wrapped__(request.getfixturevalue('older_cut'));approve_rough(root)
    calls=[]
    def refuse(*a,**k):calls.append(1);raise AssertionError('No service belongs in a restore.')
    monkeypatch.setattr('requests.post',refuse);monkeypatch.setattr('requests.get',refuse)
    yield root
    assert calls==[]

def test_restore_earlier_paper_preserves_cards_but_not_changed_words(earlier_restore,monkeypatch):
    from lib import trailer_edit as edit,trailer_titles as titles,trailer_delivery as delivery,audio_workspace as audio,receipts
    root=earlier_restore;saved=edit.read_revision(root)
    paper_versions=[v for v in saved['revision_history'] if 'paper_cut' in v and not v.get('cutting_view')]
    same=next(v for v in reversed(paper_versions) if v['paper_cut']==saved['paper_cut'])
    different=next(v for v in paper_versions if v['paper_cut']['lines']!=saved['paper_cut']['lines'])
    assert len(saved['timeline']['cards'])==3
    approvals=(root/'approvals.jsonl').read_bytes()
    upstream={p:p.read_bytes() if p.exists() else None for p in (root/'checkpoint_script.json',root/'checkpoint_scene_plan.json')}
    original=edit._commit
    def commit(root,value,old,inputs=None):
        # Detect the fault before changing this exact historical fixture.
        assert value['inputs']==old['inputs'],'A paper-only restore must keep the matching earlier fingerprint.'
        return original(root,value,old,inputs)
    monkeypatch.setattr(edit,'_commit',commit)
    restored=edit.restore_revision(root,same['edit_revision_sha256'],saved['edit_revision_sha256'])
    assert restored['restored_from']==same['edit_revision_sha256']
    assert restored['paper_cut']==same['paper_cut'] and restored['inputs']==saved['inputs']
    assert [titles.status(root,c) for c in restored['timeline']['cards']]==['Signed by you']*3
    assert all((p.read_bytes() if p.exists() else None)==raw for p,raw in upstream.items())
    assert (root/'approvals.jsonl').read_bytes()==approvals
    assert delivery.rough_state(root)=='Needs a fresh look'
    changed=edit.restore_revision(root,different['edit_revision_sha256'],restored['edit_revision_sha256'])
    assert changed['paper_cut']['lines']==different['paper_cut']['lines']
    assert changed.get('paper_cut_pending') and not edit.paper_state(root)['usable_downstream']
    assert delivery.rough_state(root)=='Needs a fresh look'
    voice=next(r for r in changed['timeline']['audio'] if audio.read(root,r['take_id'])['kind']=='speech')
    assert audio.take_state(root,audio.read(root,voice['take_id']))=='Needs a fresh look'
    with pytest.raises(ValueError):audio.require_accepted(root,voice['take_id'])
    assert (root/'approvals.jsonl').read_bytes()==approvals

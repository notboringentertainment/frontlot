"""A recorded story signature survives a later filing interruption."""
import json
import pytest
from tests.lib.test_story_ingest import root
from tests.lib.look_lock_helpers import approve_request


def test_completed_story_request_recovers_without_another_signature(root,monkeypatch):
    from lib import story_ingest as story,receipts
    from scripts import gate_approve as signer
    gate=story.supply(root,b'The gallery is closed.\r\nKeep the light on.')['gate']
    done=signer._complete_done
    monkeypatch.setattr(signer,'_complete_done',lambda *a:(_ for _ in ()).throw(OSError('Invented filing interruption')))
    with pytest.raises(OSError,match='filing interruption'):approve_request(gate,root)
    before=receipts.verified_approvals(root,'artifact_review')
    assert len(before)==1
    assert json.loads((root/'checkpoint_canon_ingest.json').read_text())['status']=='completed'
    monkeypatch.setattr(signer,'_complete_done',done)
    monkeypatch.setattr('builtins.input',lambda *a:pytest.fail('A second answer must not be requested.'))
    monkeypatch.setattr(signer.gates,'mint_gate_token',lambda *a,**kw:pytest.fail('A second signature must not be minted.'))
    assert signer.main(['--project',root.name,'--projects-dir',str(root.parent),'--request',gate['request_id']])==0
    assert receipts.verified_approvals(root,'artifact_review')==before
    assert (root/'.gate-requests/done'/f"{gate['request_id']}.json").is_file()


def test_unreadable_story_warning_is_advisory(root):
    from lib import story_ingest as story
    (root/'checkpoint_canon_ingest.json').write_text('{')
    assert 'unavailable' in story.source_warning(root,'An invented spoken line.').lower()


@pytest.mark.parametrize('filler',['\u115f','\u1160'])
def test_korean_blank_fillers_are_not_story_words(root,filler):
    from lib.story_ingest import supply
    with pytest.raises(ValueError,match='words'):supply(root,filler.encode())


def test_terminal_preview_shows_exact_story_and_designations(root,capsys):
    from lib import story_ingest as story
    from scripts import gate_approve as signer
    text='  A gallery waits.\r\nKeep the light on.\r\nThe keeper is the thief.  '
    gate=story.supply(root,text.encode(),protected=['Keep the light on.'],spoilers=['The keeper is the thief.'])['gate']
    signer.show_constructed(signer.construct(root,gate))
    output=capsys.readouterr().out
    assert text in output and 'Windows line endings' in output
    assert 'Protected wording:' in output and 'Spoiler passages:' in output


def test_invalid_story_checkpoint_schema_keeps_the_plain_signing_refusal(root):
    from lib import story_ingest as story
    from scripts import gate_approve as signer
    gate=story.supply(root,b'An invented gallery waits.')['gate']
    path=root/'checkpoint_canon_ingest.json';value=json.loads(path.read_text());value['status']='invented-invalid-status'
    path.write_text(json.dumps(value))
    with pytest.raises(signer.GateHandlerError) as caught:signer.construct(root,gate)
    assert str(caught.value)==story.UNREADABLE


def test_blank_primary_fal_connection_falls_back_to_trimmed_alternative(monkeypatch):
    from tools.video.kling_reference_video import KlingReferenceVideo
    monkeypatch.setenv('FAL_KEY',' \t ');monkeypatch.setenv('FAL_AI_API_KEY',' invented-fallback ')
    assert KlingReferenceVideo()._get_api_key()=='invented-fallback'
    monkeypatch.setenv('FAL_AI_API_KEY',' ')
    assert KlingReferenceVideo()._get_api_key() is None

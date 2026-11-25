"""Stage messages follow real signed artifacts, including interrupted recording."""
import pytest
from tests.lib.test_trimmed_trailer_delivery import trailer_cut, final_answers
from tests.tools.test_signed_footage import film,provider,looks,workspace,sign

def interrupt_recording(monkeypatch,unfinished):
    if unfinished:
        from lib import checkpoint
        def interrupted(*args,**kwargs):
            raise checkpoint.CheckpointValidationError('An invented recording interruption.')
        monkeypatch.setattr(checkpoint,'write_checkpoint',interrupted)

@pytest.mark.parametrize('unfinished',[False,True])
def test_rough_cut_signature_message_names_the_rough_cut(trailer_cut,monkeypatch,unfinished):
    from lib import trailer_delivery as d,trailer_edit
    from tests.lib.look_lock_helpers import approve_request
    root,calls,posts,*_=trailer_cut
    approve_request(d.request_rough(root,trailer_edit.read_revision(root)['edit_revision_sha256']),root)
    interrupt_recording(monkeypatch,unfinished)
    message=d.complete_stage(root,'edit')
    assert message.startswith('Your rough-cut signature is recorded.')
    assert ('unfinished' in message)==unfinished
    assert len(calls['posts'])==3 and len(posts)==1

@pytest.mark.parametrize('unfinished',[False,True])
def test_final_file_signature_message_names_this_file(trailer_cut,monkeypatch,unfinished):
    from lib import trailer_delivery as d,trailer_edit
    from tests.lib.look_lock_helpers import approve_request
    from tests.backlot.test_delivery_browser import delivery_cut
    root,calls,posts=delivery_cut.__wrapped__(trailer_cut)
    revision=trailer_edit.read_revision(root)['edit_revision_sha256']
    approve_request(d.request_rough(root,revision),root);d.complete_stage(root,'edit')
    revision=trailer_edit.read_revision(root)['edit_revision_sha256']
    approve_request(d.request_colour(root,revision),root);d.complete_stage(root,'colour_mix')
    item=d.export(root,revision)
    approve_request(d.request_final(root,item['id'],final_answers(d.story_questions(root))),root)
    assert d.final_state(root,item['id'])=='Approved by you'
    interrupt_recording(monkeypatch,unfinished)
    message=d.complete_stage(root,'compose',item['id'])
    assert message.startswith('Your final-file signature for this file is recorded.')
    assert ('unfinished' in message)==unfinished
    assert len(calls['posts'])==3 and len(posts)==1

"""Plain guidance after a real story signature and on broken history."""
from tests.lib.test_story_ingest import root,story,supply
from tests.lib.look_lock_helpers import approve_request


def test_signed_story_has_no_outstanding_paper_instruction(story,root):
    result=supply(story,root)
    approve_request(result['gate'],root)
    expected='Your exact story is signed. Changes to it need a new review.'
    assert story.complete(root)['message']==expected
    assert story.status(root)['message']==expected


def test_damaged_history_rail_uses_plain_colour_label(root):
    from backlot.state import load_board_state
    path=root/'approvals.jsonl'
    path.write_text(path.read_text()+'{"invented_invalid_row":true}\n')
    state=load_board_state(root)
    assert "approval history can't be verified" in state['trailer_damaged']
    colour=next(row for row in state['stages'] if row['name']=='colour_mix')
    assert colour['label']=='Colour and sound'
    assert all(row['status']!='completed' for row in state['stages'])

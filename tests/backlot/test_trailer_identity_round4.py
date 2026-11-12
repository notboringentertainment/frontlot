"""Lost trailer identity and unreadable history-row regressions."""
import json
import pytest
from backlot.state import load_board_state,_trailer_display_evidence
from lib import checkpoint
from lib.receipts import ReceiptChainError
from tests.backlot.test_trailer_closing_board import pin


@pytest.mark.parametrize('marker',[{}, {'pipeline_type':'cinematic'}])
def test_trailer_stage_tuple_survives_lost_creation_record(tmp_path,marker):
    root=tmp_path/'p';root.mkdir();pin(root)
    (root/'project.json').write_text(json.dumps(marker))
    (root/'approvals.jsonl').write_text('{"forged":true}\n')
    (root/'checkpoint_proposal.json').write_text(json.dumps({
        'stage':'proposal','pipeline_type':'authored-film','pipeline':{'name':'authored-film','version':'1.6'},
        'status':'completed','human_approved':True,'artifacts':{}}))
    s=load_board_state(root)
    assert s.get('is_trailer')
    assert "approval history can't be verified" in s['trailer_damaged']
    assert not any(stage['status']=='completed' for stage in s['stages'])
    with pytest.raises(ReceiptChainError):
        checkpoint.write_checkpoint(tmp_path,'p','proposal','completed',{},human_approved=True)


def test_unreadable_nested_row_does_not_hide_later_creation_evidence(tmp_path):
    root=tmp_path/'p';root.mkdir();pin(root)
    path=root/'approvals.jsonl';creation=path.read_text()
    path.write_text('['*2000+'0'+']'*2000+'\n'+creation)
    assert _trailer_display_evidence(root,{}) is True
    assert load_board_state(root)['trailer_damaged']


@pytest.mark.parametrize('tuple_value',[{'name':'authored-film','version':v} for v in ['1.1','1.2','1.3','1.4','1.5']]+[{'name':'cinematic','version':'1.6'}])
def test_older_stage_tuples_are_not_trailer_evidence(tmp_path,tuple_value):
    (tmp_path/'checkpoint_script.json').write_text(json.dumps({'pipeline':tuple_value,'status':'completed'}))
    assert _trailer_display_evidence(tmp_path,{}) is False


def test_lost_creation_record_cannot_display_handwritten_approval_in_browser(tmp_path):
    from playwright.sync_api import sync_playwright
    from tests.backlot.test_trailer_identity_display import display_server
    import os
    root=tmp_path/'p';root.mkdir();pin(root)
    (root/'project.yaml').write_text('pipeline: authored-film\n')
    (root/'project.json').write_text('{"pipeline_type":"cinematic"}')
    (root/'approvals.jsonl').write_text('{"forged":true}\n')
    (root/'checkpoint_proposal.json').write_text(json.dumps({'stage':'proposal','pipeline_type':'authored-film',
        'pipeline':{'name':'authored-film','version':'1.6'},'status':'completed','human_approved':True,'artifacts':{}}))
    with display_server(root,os.environ['OPENMONTAGE_GATES_DIR']) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') and r.request.method=='GET' else r.abort())
            page.goto(url+'/p/p?static=1')
            page.get_by_text("This trailer's approval history can't be verified, so nothing can be completed. Nothing has been changed.",exact=True).wait_for()
            assert page.get_by_text('Cannot continue',exact=True).is_visible()
            state=page.request.get(url+'/api/project/p/state').json()
            assert state['is_trailer'] and not any(s['status']=='completed' for s in state['stages'])
            assert not any('done, approved by you' in str(label).lower() for label in page.locator('.stage').evaluate_all('(es)=>es.map(e=>e.getAttribute("title")||e.innerText)'))
        finally:browser.close()

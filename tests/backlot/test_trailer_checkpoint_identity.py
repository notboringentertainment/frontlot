"""Checkpoint-only trailer evidence is visible without granting approval."""
import json,os
import pytest
from backlot.state import load_board_state,summarize_project
from lib import checkpoint
from lib.receipts import ReceiptChainError

PINS=['authored-film@1.6',{'name':'authored-film','version':'1.6'}]


@pytest.mark.parametrize('pipeline',PINS)
@pytest.mark.parametrize('history',['absent','valid','broken'])
def test_checkpoint_only_trailer_is_blocked_with_every_history(tmp_path,pipeline,history):
    from tests.tools.test_audio_governance import sign
    root=tmp_path/'p';root.mkdir()
    if history=='valid':sign(root,'config',{'config_sha256':'a'*64},'project_config')
    if history=='broken':(root/'approvals.jsonl').write_text('{"forged":true}\n')
    (root/'project.json').write_text('{"pipeline_type":"cinematic"}')
    (root/'project.yaml').write_text('pipeline: cinematic\n')
    (root/'checkpoint_proposal.json').write_text(json.dumps({'stage':'proposal','pipeline':pipeline,
        'status':'completed','human_approved':True,'artifacts':{}}))
    state=load_board_state(root);film=summarize_project(root)
    assert state.get('is_trailer') and film.get('is_trailer')
    assert state.get('trailer_damaged')
    assert not any(s['status']=='completed' for s in state['stages'])
    assert next(s for s in state['stages'] if s['name']=='proposal')['label']=='Treatment'
    error=ReceiptChainError if history=='broken' else checkpoint.CheckpointValidationError
    message="not this project's receipt" if history=='broken' else 'has not been signed'
    with pytest.raises(error,match=message):
        checkpoint.write_checkpoint(tmp_path,'p','proposal','completed',{},human_approved=True)


@pytest.mark.parametrize('pipeline',PINS)
def test_checkpoint_only_trailer_on_real_board(tmp_path,pipeline):
    from playwright.sync_api import sync_playwright
    from tests.backlot.test_trailer_identity_display import display_server
    root=tmp_path/'p';root.mkdir()
    (root/'project.json').write_text('{"pipeline_type":"cinematic"}')
    (root/'checkpoint_proposal.json').write_text(json.dumps({'stage':'proposal','pipeline':pipeline,
        'status':'completed','human_approved':True,'artifacts':{}}))
    with display_server(root,os.environ['OPENMONTAGE_GATES_DIR']) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':900});page.set_default_timeout(5000)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') and r.request.method=='GET' else r.abort())
            page.goto(url+'/p/p?static=1')
            page.get_by_text('Cannot continue',exact=True).wait_for()
            assert page.locator('.counter .stage.done').count()==0
            assert 'treatment' in page.locator('.counter').inner_text().lower()
            page.goto(url+'/');page.wait_for_selector('.lib-card')
            assert 'Trailer' in page.locator('.lib-card').inner_text()
            assert not errors
        finally:browser.close()


@pytest.mark.parametrize('pipeline',PINS)
def test_standalone_checkpoint_reference_reads_both_forms(pipeline):
    assert checkpoint._ref_from_checkpoint({'pipeline':pipeline})=='authored-film@1.6'

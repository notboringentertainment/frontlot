"""Advisory story failures cannot hide a previously held charge."""
import json,time
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.signing_outcome_helpers import (project,priced,http,voice_world,film,provider,signing_folder,bounded_signers,held,figures)
from tests.backlot.test_voice_panel_browser import board
from tests.backlot.test_followups_browser import open_page
from tests.backlot.test_signing_outcome_browser import review_gate,open_review,wait_for


@pytest.mark.parametrize('medium',['audio','footage'])
def test_unreadable_story_keeps_held_money_and_review_controls(request,monkeypatch,medium):
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count()
    from backlot import voice_panel,footage_controls
    from lib import story_ingest
    from lib.pathsafe import PathSafetyError
    monkeypatch.setattr(story_ingest,'story_checkpoint',lambda *a:(_ for _ in ()).throw(PathSafetyError('Invented unreadable story')))
    if medium=='audio':
        value=voice_panel.snapshot(root)
        assert value['budget']['held']==figures(root,ceiling)[1]>0
        assert value['unresolved_requests'] and value['requests']
        assert 'unavailable' in value['requests'][0]['source_warning'].lower()
    else:
        # Force the paper display's safe error result as well as the advisory
        # failure, without damaging the signed charge or its recovery inputs.
        from lib import trailer_edit
        monkeypatch.setattr(trailer_edit,'paper_state',lambda *a:{'error':'The supplied story cannot be read.','usable_downstream':False})
        value=footage_controls.inspect(root,{})
        assert value['shots'] and value['shots'][0]['requests']
    assert count()==before and figures(root,ceiling)[1]>0


def test_two_answers_in_one_frame_do_not_abandon_a_receipt_commit(request,monkeypatch,bounded_signers,tmp_path):
    marker=tmp_path/'buffered-receipt-barrier'
    monkeypatch.setenv('FRONTLOT_TEST_RECEIPT_BARRIER',str(marker))
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);gate=review_gate(root,reservation);before=count()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.evaluate('''()=>{const send=WebSocket.prototype.send;WebSocket.prototype.send=function(v){if(this.url.endsWith('/tty'))window.signingWire=this;return send.call(this,v)}}''')
            open_review(page,gate)
            expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_enabled(timeout=10000)
            page.evaluate('()=>window.signingWire.send(new Uint8Array([121,10,10]))')
            wait_for(page,marker.exists);page.close();time.sleep(5.5)
            assert any(p.poll() is None for p in bounded_signers),'The fully answered signer was abandoned during its receipt commit.'
            for p in bounded_signers:p.wait(timeout=10)
            from lib import receipts
            assert len(receipts.verified_approvals(root,'audio_resolution'))==1
            from lib.media_spending import apply_signed_resolution
            apply_signed_resolution(root,reservation['reservation_id'])
            assert figures(root,ceiling,fresh=True)==[0,0,ceiling] and count()==before
        finally:browser.close()

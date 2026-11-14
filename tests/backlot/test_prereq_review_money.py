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
    from lib import trailer_edit
    saved=trailer_edit.read_revision(root)
    assert saved and saved['paper_cut']['shots']
    # Damage the real checkpoint the panels read, not an unused helper.
    (root/'checkpoint_canon_ingest.json').write_text('{')
    if medium=='audio':
        value=voice_panel.snapshot(root)
        assert value['budget']['held']==figures(root,ceiling)[1]>0
        assert value['unresolved_requests'] and value['requests']
        assert value['lines']==saved['paper_cut']['lines'] and value['shots']==saved['paper_cut']['shots']
        assert 'unavailable' in value['requests'][0]['source_warning'].lower()
    else:
        value=footage_controls.inspect(root,{})
        assert value['shots'] and value['shots'][0]['requests']
    from backlot.footage_prereqs import snapshot
    assert snapshot(root)['shots']==saved['paper_cut']['shots']
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


def test_receipt_verification_during_board_refresh_is_a_plain_refusal(request,monkeypatch):
    """A signer updating its receipt tip must not break the observer's HTTP response."""
    from lib import receipts,trailer_edit
    root,reservation,count,ceiling=held(request,'footage',monkeypatch);before=count()
    original=trailer_edit._story_check
    blocked=[False]
    def story_check(*args,**kwargs):
        if blocked[0]:raise receipts.ReceiptChainError('Invented receipt tip changed during a read.')
        return original(*args,**kwargs)
    monkeypatch.setattr(trailer_edit,'_story_check',story_check)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            blocked[0]=True
            response=page.request.get(url+'/api/project/'+root.name+'/state')
            assert response.status==200
            value=response.json()['paper_cut']
            assert not value['usable_downstream'] and value['needs_review']
            assert 'signed approval records could not be verified' in value['error']
            assert figures(root,ceiling)[1]>0 and count()==before
            blocked[0]=False
            refreshed=page.request.get(url+'/api/project/'+root.name+'/state')
            assert refreshed.status==200 and not refreshed.json()['paper_cut'].get('error')
            assert not page.errors
        finally:browser.close()

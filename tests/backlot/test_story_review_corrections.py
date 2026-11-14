"""Narrow signing lifecycle and refusal regressions from the approved review."""
import json,time,threading
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.signing_outcome_helpers import (project,priced,http,voice_world,film,provider,signing_folder,bounded_signers,held,figures)
from tests.backlot.test_voice_panel_browser import board
from tests.backlot.test_followups_browser import open_page
from tests.backlot.test_signing_outcome_browser import review_gate,open_review,wait_for


@pytest.mark.parametrize('close',['panel','tab'])
def test_partial_answer_or_closed_tab_releases_unsigned_helper(request,monkeypatch,bounded_signers,close):
    root,reservation,count,ceiling=held(request,'audio',monkeypatch)
    gate=review_gate(root,reservation);before=count()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.evaluate('''()=>{const send=WebSocket.prototype.send;WebSocket.prototype.send=function(v){if(this.url.endsWith('/tty'))window.signingWire=this;return send.call(this,v)}}''')
            open_review(page,gate)
            expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_enabled(timeout=7000)
            # Capture the actual wire to type an incomplete terminal answer.
            page.get_by_role('button',name='Show terminal',exact=True).click() if page.get_by_role('button',name='Show terminal',exact=True).count() else None
            from backlot.tty import session_paths
            from tests.backlot.tty_helpers import attach_when_free,encode_frame,IN
            # Browser's raw terminal accepts keyboard input without completing
            # the line; no affirmative button or newline is sent.
            page.evaluate('()=>window.signingWire.send(new Uint8Array([121]))')
            page.wait_for_timeout(150)
            if close=='panel':page.get_by_role('button',name='Return to audio',exact=True).click()
            else:page.close()
            end=time.monotonic()+9
            while any(p.poll() is None for p in bounded_signers):
                assert time.monotonic()<end,'The unanswered signing helper still holds the production slot.'
                time.sleep(.05)
            from lib import receipts
            assert not receipts.verified_approvals(root,'audio_resolution')
            assert figures(root,ceiling)[1]>0 and count()==before
        finally:browser.close()


def test_switching_request_clears_error_and_delayed_retry_uses_failed_identity(request,monkeypatch,bounded_signers):
    from lib.media_spending import gate_request
    from lib.project_config import read_project_config
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);gate=review_gate(root,reservation)
    _,digest=read_project_config(root)
    other=gate_request(root,'config','project_config',{'config_sha256':digest},'Review separate settings.')
    from backlot import tty
    released=threading.Event();release=tty.TTYRegistry.release
    async def release_observed(self,*args,**kwargs):
        try:return await release(self,*args,**kwargs)
        finally:released.set()
    monkeypatch.setattr(tty.TTYRegistry,'release',release_observed)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.evaluate('''()=>{const Native=WebSocket;let first=true;
              window.WebSocket=class extends Native{constructor(...a){super(...a);if(String(a[0]).endsWith('/tty')&&first){first=false;this.addEventListener('message',e=>{if(typeof e.data==='string'&&JSON.parse(e.data).tag?.ask)this.close(1000,'Invented disconnect');});}}};}''')
            open_review(page,gate)
            retry=page.get_by_role('button',name='Try signing again',exact=True)
            expect(retry).to_be_visible(timeout=7000)
            wait_for(page,released.is_set)
            retry.evaluate('(b)=>window.oldRetry=b')
            page.get_by_role('button',name=f"Inspect config gate {other['request_id']}",exact=True).click()
            expect(retry).to_have_count(0)
            # A callback queued before switching still belongs to the failed
            # request. It must not capture the newly displayed settings kind.
            page.evaluate('()=>window.oldRetry.click()')
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=10000)
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
        finally:browser.close()


def test_apply_refuses_linked_request_folder_before_scanning(request,monkeypatch,tmp_path):
    from tests.backlot.signing_outcome_helpers import signed_review
    from lib.media_spending import apply_signed_resolution
    from lib import media_spending
    root,reservation,count,ceiling=held(request,'audio',monkeypatch)
    signed_review(root,reservation)
    # Use a new linked directory path without moving or deleting a folder.
    from scripts import gate_approve
    original=gate_approve._confined
    seen=[]
    def checked(path,base):
        if path.name=='.gate-requests':seen.append(path)
        return original(path,base)
    # The fake root supplies the same verified signature but exposes a link
    # at the scan boundary; the linked target must never be traversed.
    linked=tmp_path/'linked-film';linked.mkdir();target=linked/'requests';target.mkdir()
    (linked/'.gate-requests').symlink_to(target,target_is_directory=True)
    from lib import receipts
    row=receipts.verified_approvals(root,'audio_resolution')[0]
    monkeypatch.setattr(media_spending,'_root',lambda p:linked)
    monkeypatch.setattr(media_spending,'signed_resolution',lambda *a:row['record'])
    monkeypatch.setattr(receipts,'verified_approvals',lambda *a,**kw:[row])
    monkeypatch.setattr(gate_approve,'_confined',checked)
    monkeypatch.setattr(media_spending,'resolve_charge',lambda *a:pytest.fail('A linked folder reached reconciliation.'))
    with pytest.raises(ValueError,match='links|link'):
        apply_signed_resolution(linked,reservation['reservation_id'])
    assert not seen,'The request-directory link was resolved before it was refused.'


def test_tab_close_never_interrupts_a_complete_answer_recording_its_receipt(request,monkeypatch,bounded_signers,tmp_path):
    import os
    from pathlib import Path
    marker=tmp_path/'receipt-barrier'
    monkeypatch.setenv('FRONTLOT_TEST_RECEIPT_BARRIER',str(marker))
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);gate=review_gate(root,reservation);before=count()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);open_review(page,gate)
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=10000)
            wait_for(page,marker.exists);page.close()
            time.sleep(5.5)
            assert any(p.poll() is None for p in bounded_signers),'A receipt being recorded was interrupted by the idle limit.'
            for process in bounded_signers:process.wait(timeout=10)
            from lib import receipts
            assert len(receipts.verified_approvals(root,'audio_resolution'))==1
            from lib.media_spending import apply_signed_resolution
            apply_signed_resolution(root,reservation['reservation_id'])
            assert figures(root,ceiling,fresh=True)==[0,0,ceiling] and count()==before
        finally:browser.close()


def test_signing_fixture_needs_no_extra_parent_or_tmpdir(monkeypatch):
    import os
    from pathlib import Path
    from tests.backlot.signing_outcome_helpers import signing_folder
    monkeypatch.delenv('TMPDIR',raising=False);monkeypatch.delenv('FRONTLOT_FOLLOWUPS_PARENT',raising=False)
    signing_folder.__wrapped__(monkeypatch)
    assert Path(os.environ['OPENMONTAGE_GATES_DIR']).is_dir()
    assert Path(os.environ['OPENMONTAGE_GATES_DIR']).is_relative_to(Path(os.environ['OPENMONTAGE_HOME']))


def test_late_error_for_finished_request_cannot_disable_another_question(request,monkeypatch,bounded_signers):
    from lib.media_spending import gate_request,apply_signed_resolution
    from lib.project_config import read_project_config
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);gate=review_gate(root,reservation);before=count()
    _,digest=read_project_config(root)
    other=gate_request(root,'config','project_config',{'config_sha256':digest},'Review separate settings.')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);held_reply=[]
            path=f'{url}/api/project/{root.name}/voice/resolution/{reservation["reservation_id"]}/apply'
            page.route(path,lambda route:held_reply.append(route))
            open_review(page,gate);page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=10000)
            wait_for(page,lambda:bool(held_reply))
            page.get_by_role('button',name=f"Inspect config gate {other['request_id']}",exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            answer=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(answer).to_be_enabled(timeout=7000)
            with page.expect_response(path):held_reply[0].fulfill(status=400,content_type='application/json',body=json.dumps({'detail':'Invented delayed error for the first request.'}))
            page.wait_for_timeout(200)
            expect(answer).to_be_enabled()
            expect(page.locator('#gate-sign-panel')).not_to_contain_text('Invented delayed error')
            page.get_by_role('button',name='Not now',exact=True).click()
            expect(page.locator('#gate-sign-panel')).to_contain_text('Nothing decided',timeout=7000)
            apply_signed_resolution(root,reservation['reservation_id'])
            assert figures(root,ceiling,fresh=True)==[0,0,ceiling] and count()==before
        finally:browser.close()

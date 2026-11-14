"""Force the exact refresh and transport boundaries of real signing screens."""
import json,time
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board
from tests.backlot.test_followups_browser import open_page
from tests.backlot.signing_outcome_helpers import (
    project,priced,http,voice_world,film,provider,signing_folder,bounded_signers,
    held,figures,signed_review)


def wait_for(page,condition):
    deadline=time.monotonic()+7
    while not condition():
        assert time.monotonic()<deadline,'The forced signing boundary was not reached.'
        page.wait_for_timeout(20)


def review_gate(root,reservation):
    from lib.media_spending import request_resolution
    return request_resolution(root,reservation['reservation_id'],False,0,0,'I checked the invented service account.')


def open_review(page,gate):
    name='Review: Review uncertain charge'
    page.get_by_role('button',name=name,exact=True).click()
    page.get_by_role('button',name='Start signing',exact=True).click()


def delayed_wire(page,kind):
    delayed=[];connections=[]
    def route(ws):
        server=ws.connect_to_server();connections.append((ws,server))
        def message(value):
            item=json.loads(value) if isinstance(value,str) else {}
            target=(item.get('type')=='input_ready' if kind=='ready' else
                    item.get('type')=='status' and item.get('tag',{}).get('outcome')=='approved')
            if target:delayed.append((ws,value))
            else:ws.send(value)
        server.on_message(message)
    page.route_web_socket('**/tty',route)
    return delayed,connections


def wipe_detail(page,url,root,gate):
    path=f'{url}/api/project/{root.name}/gate/{gate["request_id"]}'
    def unavailable(route):
        response=route.fetch();value=response.json()
        value['packet']={'packet_error':True,'error':'The account review is temporarily busy. Try again.'}
        route.fulfill(response=response,json=value)
    page.route(path,unavailable)
    with page.expect_response(path):page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
    # A page read observes the actual renderer while another owner holds the
    # paid-call lock. The browser's forced response also covers other read errors.
    return path


@pytest.mark.parametrize('medium',['footage','audio'])
@pytest.mark.parametrize('changed',['busy','done'])
def test_approved_outcome_survives_replaced_detail(request,medium,changed,monkeypatch,bounded_signers):
    from lib.media_spending import call_lock
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);delayed,_=delayed_wire(page,'approved')
            open_review(page,gate)
            approve=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(approve).to_be_enabled(timeout=10000)
            if changed=='busy':
                with call_lock(root):wipe_detail(page,url,root,gate)
            approve.click();wait_for(page,lambda:bool(delayed))
            if changed=='done':
                path=f'{url}/api/project/{root.name}/gate/{gate["request_id"]}'
                with page.expect_response(path):page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
                assert 'packet' not in page.request.get(path).json()
            delayed[0][0].send(delayed[0][1])
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            expect(page.get_by_role('region',name='Footage workspace' if medium=='footage' else 'Trailer audio')).to_be_visible()
            page.reload()
            assert figures(root,ceiling,fresh=True)==pytest.approx([0,0,ceiling])
            assert count()==before and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('medium',['footage','audio'])
def test_background_packet_error_cannot_discard_signers_input_ready(request,medium,monkeypatch,bounded_signers):
    from lib.media_spending import call_lock
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);delayed,_=delayed_wire(page,'ready');open_review(page,gate)
            wait_for(page,lambda:bool(delayed))
            with call_lock(root):wipe_detail(page,url,root,gate)
            delayed[0][0].send(delayed[0][1])
            button=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(button).to_be_enabled(timeout=5000);button.click()
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            assert count()==before and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('charged',[False,True])
def test_footage_recovers_recorded_outcome_after_reloading(request,charged,monkeypatch):
    root,reservation,count,ceiling=held(request,'footage',monkeypatch);before=count()
    signed_review(root,reservation,charged)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);page.reload()
            panel=page.get_by_role('region',name='Footage workspace')
            button=panel.get_by_role('button',name='Apply the signed account outcome',exact=True)
            expect(button).to_be_visible(timeout=7000);button.click()
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            expected=[.003,0,ceiling-.003] if charged else [0,0,ceiling]
            assert figures(root,ceiling,fresh=True)==pytest.approx(expected)
            path=f'{url}/api/project/{root.name}/footage/apply-resolution'
            for _ in range(2):
                response=page.request.post(path,data={'reservation_id':reservation['reservation_id']},headers={'x-frontlot-capability':'invented-browser-proof'})
                assert response.ok,response.text()
            page.reload();expect(button).to_have_count(0)
            assert figures(root,ceiling,fresh=True)==pytest.approx(expected) and count()==before and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('medium',['footage','audio'])
def test_call_lock_during_signing_admission_does_not_drop_the_question(request,medium,monkeypatch,bounded_signers):
    from backlot import server
    from lib.media_spending import call_lock
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation)
    original=server.load_gate_detail;hold={'enabled':False}
    def locked(*args,**kwargs):
        if hold['enabled']:
            hold['enabled']=False
            with call_lock(root):return original(*args,**kwargs)
        return original(*args,**kwargs)
    monkeypatch.setattr(server,'load_gate_detail',locked)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.get_by_role('button',name='Review: Review uncertain charge',exact=True).click()
            hold['enabled']=True
            page.get_by_role('button',name='Start signing',exact=True).click()
            button=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(button).to_be_enabled(timeout=7000);button.click()
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            assert count()==before and not page.errors
        finally:browser.close()


def test_disconnect_before_question_offers_retry_and_abandoned_helper_stops(request,monkeypatch,bounded_signers):
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);before=count();gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);first={'cut':False}
            def route(ws):
                server=ws.connect_to_server()
                def message(value):
                    item=json.loads(value) if isinstance(value,str) else {}
                    if not first['cut'] and item.get('type')=='status' and item.get('tag',{}).get('ask'):
                        first['cut']=True;ws.close(code=1011,reason='Invented lost connection.');server.close();return
                    ws.send(value)
                server.on_message(message)
            page.route_web_socket('**/tty',route);open_review(page,gate)
            retry=page.get_by_role('button',name='Try signing again',exact=True)
            expect(retry).to_be_visible(timeout=7000)
            expect(page.locator('#gate-sign-panel')).not_to_contain_text('Getting the question ready')
            assert figures(root,ceiling)[1]>0 and count()==before
            retry.click();expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_enabled(timeout=7000)
            page.get_by_role('button',name='Not now',exact=True).click()
            expect(page.locator('#gate-sign-panel')).to_contain_text('Nothing decided',timeout=7000)
            assert figures(root,ceiling)[1]>0 and count()==before and not page.errors
        finally:browser.close()


def test_closing_unanswered_signing_stops_its_exact_helper(request,monkeypatch,bounded_signers):
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);open_review(page,gate)
            expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_enabled(timeout=7000)
            page.get_by_role('button',name='Return to audio',exact=True).click()
            wait_for(page,lambda:all(p.poll() is not None for p in bounded_signers))
            from lib import receipts
            assert not receipts.verified_approvals(root,'audio_resolution') and figures(root,ceiling)[1]>0
        finally:browser.close()

"""Force the exact refresh and transport boundaries of real signing screens."""
import json,time,sys
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
        assert time.monotonic()<deadline,('The forced signing boundary was not reached. '+page.locator('#gate-sign-panel').inner_text()+' '+page.locator('#gate-terminal-message').inner_text())
        page.wait_for_timeout(20)


def review_gate(root,reservation,charged=False):
    from lib.media_spending import request_resolution
    return request_resolution(root,reservation['reservation_id'],charged,.001 if charged else 0,.002 if charged else 0,'I checked the invented service account.')


def open_review(page,gate):
    name='Review: Review uncertain charge'
    page.get_by_role('button',name=name,exact=True).click()
    page.get_by_role('button',name='Start signing',exact=True).click()


def delayed_wire(page,kind):
    # Hold actual frames in the page, leaving the real connection and signer
    # untouched. Playwright's installed on_close adapter loses reason on EOF.
    page.evaluate('''kind=>{
      const Native=window.WebSocket;window.heldSignerFrames=[];
      class DelayedSigner extends EventTarget{
        static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;
        constructor(url,...args){super();if(!String(url).includes('/gate/'))return new Native(url,...args);
          this.raw=new Native(url,...args);this.holding=false;
          this.raw.addEventListener('open',()=>this.dispatchEvent(new Event('open')));
          this.raw.addEventListener('error',()=>this.dispatchEvent(new Event('error')));
          this.raw.addEventListener('close',e=>{if(!this.holding)this.dispatchEvent(new CloseEvent('close',{code:e.code,reason:e.reason}));});
          this.raw.addEventListener('message',e=>{
            let m={};try{if(typeof e.data==='string')m=JSON.parse(e.data)}catch{}
            const target=kind==='ready'?m.type==='input_ready':m.type==='status'&&m.tag?.outcome==='approved';
            if(target){window.heldSignerFrames.push({socket:this,data:e.data});if(kind==='approved')this.holding=true;return;}
            if(this.holding&&(m.type==='bye'||m.signer==='exited'))return;
            this.dispatchEvent(new MessageEvent('message',{data:e.data}));
          });
        }
        get readyState(){return this.raw.readyState}get binaryType(){return this.raw.binaryType}
        set binaryType(value){this.raw.binaryType=value}send(value){this.raw.send(value)}close(...args){this.raw.close(...args)}
      }
      window.WebSocket=DelayedSigner;
      window.releaseSignerFrame=()=>{const f=window.heldSignerFrames.shift();f.socket.dispatchEvent(new MessageEvent('message',{data:f.data}));};
    }''',kind)
    return lambda:page.evaluate('()=>window.heldSignerFrames.length>0')


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
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation,changed=='done')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);delayed=delayed_wire(page,'approved')
            open_review(page,gate)
            approve=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(approve).to_be_enabled(timeout=10000)
            if changed=='busy':
                with call_lock(root):wipe_detail(page,url,root,gate)
            approve.click();wait_for(page,delayed)
            if changed=='done':
                path=f'{url}/api/project/{root.name}/gate/{gate["request_id"]}'
                with page.expect_response(path):page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
                assert 'packet' not in page.request.get(path).json()
            page.evaluate('()=>window.releaseSignerFrame()')
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            expect(page.get_by_role('region',name='Footage workspace' if medium=='footage' else 'Trailer audio')).to_be_visible()
            page.reload()
            expected=[.003,0,ceiling-.003] if changed=='done' else [0,0,ceiling]
            assert figures(root,ceiling,fresh=True)==pytest.approx(expected)
            assert count()==before and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('medium',['footage','audio'])
def test_background_packet_error_cannot_discard_signers_input_ready(request,medium,monkeypatch,bounded_signers):
    from lib.media_spending import call_lock
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);delayed=delayed_wire(page,'ready');open_review(page,gate)
            wait_for(page,delayed)
            expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_visible(timeout=7000)
            with call_lock(root):wipe_detail(page,url,root,gate)
            page.evaluate('()=>window.releaseSignerFrame()')
            button=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(button).to_be_enabled(timeout=5000);button.click()
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            assert count()==before and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('charged',[False,True])
def test_footage_recovers_recorded_outcome_after_reloading(request,charged,monkeypatch):
    root,reservation,count,ceiling=held(request,'footage',monkeypatch);before=count()
    gate=signed_review(root,reservation,charged)
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
                response=page.request.post(path,data={'reservation_id':reservation['reservation_id']},headers={'x-frontlot-capability':'invented-browser-proof','origin':url})
                assert response.ok,response.text()
            page.reload();expect(button).to_have_count(0)
            state=page.request.get(f'{url}/api/project/{root.name}/state').json()
            assert next(row for row in state['gates']['requests'] if row['request_id']==gate['request_id'])['state']=='done'
            assert figures(root,ceiling,fresh=True)==pytest.approx(expected) and count()==before and not page.errors
        finally:browser.close()


@pytest.mark.parametrize('medium',['footage','audio'])
def test_call_lock_during_signing_admission_does_not_drop_the_question(request,medium,monkeypatch,bounded_signers):
    from backlot import server
    from lib.media_spending import call_lock
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation)
    original=server.load_gate_detail;hold={'enabled':False,'used':False}
    def locked(*args,**kwargs):
        if hold['enabled'] and sys._getframe(1).f_code.co_name=='_tty_websocket':
            hold['enabled']=False;hold['used']=True
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
            expect(button).to_be_enabled(timeout=7000);assert hold['used'];button.click()
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
            expect(page.locator('#gate-sign-panel')).to_contain_text('Nothing has been signed yet',timeout=7000)
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


def test_failed_footage_signing_video_can_load_again(film,provider,monkeypatch):
    from tests.tools.test_signed_footage import prepare,approve,workspace
    root,shot=film;record=prepare(film);approve(root,record);take=workspace().execute(root,record['request_id'])
    workspace().request_decision(root,shot,take['take_id'],'accept')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);failed={'active':True}
            def media(route):
                if failed['active']:route.abort()
                else:route.continue_()
            page.route(url+'/media/'+root.name+'/'+take['path'],media)
            page.get_by_role('button',name='Review: Review this take',exact=True).click()
            player=page.get_by_label('Full footage take',exact=True)
            page.wait_for_function('''()=>!!document.querySelector('video[aria-label="Full footage take"]')?.error''')
            failed['active']=False
            page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            expect(player).to_have_js_property('readyState',4,timeout=7000)
            assert all(len(calls)==1 for calls in provider.values()) and not page.errors
        finally:browser.close()


def test_board_teardown_stops_its_abandoned_signer(request,monkeypatch,bounded_signers):
    root,reservation,count,ceiling=held(request,'audio',monkeypatch);gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);connections=[]
            def wire(ws):connections.append((ws,ws.connect_to_server()))
            page.route_web_socket('**/tty',wire);open_review(page,gate)
            expect(page.get_by_role('button',name='Approve and sign',exact=True)).to_be_enabled(timeout=7000)
            # Simulate a lost page, with no opportunity for pagehide to cancel.
            connections[0][0].close(code=1011,reason='Invented abandoned page.');connections[0][1].close()
        finally:browser.close()
    assert all(process.poll() is not None for process in bounded_signers),'The board left its own real signer running.'


@pytest.mark.parametrize('medium',['footage','audio'])
def test_recorded_signature_applies_even_when_the_approved_frame_is_lost(request,medium,monkeypatch,bounded_signers):
    from lib import receipts
    root,reservation,count,ceiling=held(request,medium,monkeypatch);before=count();gate=review_gate(root,reservation)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);delayed_wire(page,'lost');open_review(page,gate)
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=7000)
            wait_for(page,lambda:figures(root,ceiling)[1]==0)
            assert page.evaluate('()=>window.heldSignerFrames.length')==1
            assert len(receipts.verified_approvals(root,'audio_resolution',entity_id=reservation['reservation_id']))==1
            assert figures(root,ceiling,fresh=True)==pytest.approx([0,0,ceiling]) and count()==before and not page.errors
        finally:browser.close()

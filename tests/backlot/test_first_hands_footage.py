"""Footage observations never buy again or hide the signed account outcome."""
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.tools.test_signed_footage import film,provider,prepare,approve,workspace
from tests.backlot.signing_outcome_helpers import held,signed_review,figures


def test_settled_footage_signature_has_no_make_button_and_cannot_dispatch(request,monkeypatch):
    from lib.media_spending import apply_signed_resolution
    root,reservation,counts,ceiling=held(request,'footage',monkeypatch)
    gate=signed_review(root,reservation,charged=True)
    apply_signed_resolution(root,reservation['reservation_id']);before=counts()
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'{url}/p/{root.name}?static=1#k=invented-browser-proof')
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            expect(shot).to_be_visible()
            expect(shot.get_by_role('button',name='Make signed clip',exact=True)).to_have_count(0)
            expect(shot).to_contain_text('Your signed account outcome is recorded')
            response=page.request.post(f'{url}/api/project/{root.name}/footage/dispatch',
                headers={'origin':url,'x-frontlot-capability':'invented-browser-proof'},
                data={'request_id':gate['approval_record']['request']['request_id']})
            assert response.status==400 and 'signed account outcome is recorded' in response.json()['detail']
            assert figures(root,ceiling,fresh=True)==pytest.approx([.003,0,ceiling-.003])
            assert counts()==before and not errors
        finally:browser.close()


@pytest.mark.parametrize('recover',[True,False])
def test_failed_footage_observation_retries_bounded_reads_only(film,provider,monkeypatch,recover):
    from tests.backlot.test_signing_outcome_browser import wait_for
    root,_=film;record=prepare(film);approve(root,record)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});reads=[]
            page.add_init_script("window.pauseFootagePoll=true;Object.defineProperty(document,'hidden',{get:()=>window.pauseFootagePoll});")
            def read(route):
                reads.append(1)
                if len(reads)==1:
                    value=route.fetch().json();value['shots'][0]['requests'][0]['running']=True;value['shots'][0]['status']='Generating'
                    route.fulfill(json=value)
                elif len(reads)==2 or not recover:route.fulfill(status=400,json={'detail':'Invented temporary read failure.'})
                else:route.continue_()
            page.route('**/footage',read)
            page.goto(f'{url}/p/{root.name}?static=1#k=invented-browser-proof')
            shot=page.get_by_role('article',name='Shot 1 footage',exact=True)
            expect(shot.get_by_role('status').first).to_have_text('Generating')
            # Force one full board read to fail; static mode supplies no event
            # feed or wake handler that could accidentally rescue this test.
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
            paper.get_by_role('button',name='Save a new version',exact=True).click()
            wait_for(page,lambda:len(reads)>=2)
            expect(paper.get_by_role('button',name='Save a new version',exact=True)).to_be_enabled(timeout=15000)
            expect(page.get_by_role('region',name='Footage workspace',exact=True)).to_contain_text('could not be read')
            page.evaluate('()=>window.pauseFootagePoll=false')
            if recover:
                expect(shot.get_by_role('button',name='Make signed clip',exact=True)).to_be_visible(timeout=10000)
            else:
                page.wait_for_timeout(8000);attempts=len(reads)
                assert 3<=attempts<=5,'Failed observation must retry a bounded number of reads.'
                page.wait_for_timeout(4000);assert len(reads)==attempts
                expect(page.get_by_role('button',name='Try reading footage again',exact=True)).to_be_visible()
            assert all(not calls for calls in provider.values())
        finally:browser.close()


def test_footage_form_keeps_its_live_field_during_focus_refresh(film,provider,monkeypatch):
    root,_=film
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            field=page.get_by_role('article',name='Shot 1 footage',exact=True).get_by_label('Clip length in seconds',exact=True)
            field.fill('6');field.focus();page.evaluate('()=>window.originalFootageField=document.activeElement')
            with page.expect_response(url+'/api/project/'+root.name+'/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(300)
            assert field.evaluate('(e)=>e===window.originalFootageField&&e===document.activeElement')
            expect(field).to_have_value('6')
            assert all(not calls for calls in provider.values())
        finally:browser.close()


def test_account_refusal_is_beside_its_footage_button_without_releasing_money(request,monkeypatch):
    root,reservation,counts,ceiling=held(request,'footage',monkeypatch);before=counts();money=figures(root,ceiling,fresh=True)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});page.goto(f'{url}/p/{root.name}?static=1#k=invented-browser-proof')
            button=page.get_by_role('button',name='Review account outcome',exact=True);button.click()
            expect(button.locator('xpath=following-sibling::*[1]')).to_contain_text('Record what you checked')
            assert figures(root,ceiling,fresh=True)==money and counts()==before
        finally:browser.close()

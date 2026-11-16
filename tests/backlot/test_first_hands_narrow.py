"""Scrolling and local held-charge explanations at writer window sizes."""
import pytest
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_voice_panel import voice_world, project, priced, http, options
from tests.backlot.signing_outcome_helpers import bounded_signers
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.test_first_hands import failed_filing

@pytest.mark.parametrize('width',[735,1440])
def test_wheel_and_keyboard_scroll(voice_world,monkeypatch,width):
    root,posts,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            expect(page.get_by_role('region',name='Trailer paper cut')).to_be_visible()
            page.evaluate("()=>{window.scrollTo(0,0);document.querySelector('#viewer').scrollTop=0;document.activeElement.blur()}")
            position="()=>innerWidth<=760?scrollY:document.querySelector('#viewer').scrollTop"
            page.mouse.move(300,400);page.mouse.wheel(0,900)
            page.wait_for_timeout(350)
            assert page.evaluate(position)>0,'Wheel must scroll the board'
            page.locator('#viewer').evaluate("e=>{e.tabIndex=0;e.focus({preventScroll:true})}")
            for key in ('PageDown','End','ArrowDown','Space'):
                page.evaluate("()=>{window.scrollTo(0,0);document.querySelector('#viewer').scrollTop=0}")
                page.keyboard.press(key);page.wait_for_timeout(350)
                assert page.evaluate(position)>0,key+' must scroll the board'
            page.evaluate("id=>fetch('/api/project/'+id+'/paper-cut',{method:'POST'})",root.name)
            expect(page.locator('#writer-warning')).to_be_visible()
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            assert page.locator('.bar').bounding_box()['y']>=page.locator('#writer-warning').bounding_box()['height']
            assert not posts
        finally:browser.close()

@pytest.mark.parametrize('width',[735,1440])
def test_return_after_signature_never_invents_a_waiting_question(voice_world,monkeypatch,bounded_signers,width):
    from backlot import voice_panel as voice
    root,posts,_=voice_world;voice.catalogue(root);voice.prepare_voice(root,options())
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('region',name='Trailer audio').get_by_role('button',name='Review and sign this request',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=20000)
            page.route('**/gate-sessions',lambda r:r.fulfill(json=[{'request_id':'invented-ended-session'}]))
            with page.expect_response('**/gate-sessions'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.get_by_role('button',name='Return to audio',exact=True).click()
            expect(page.locator('#gate-sign-panel')).not_to_contain_text('Getting the question ready')
            if os.environ.get('FIRST_HANDS_PICTURES'):
                page.locator('#gate-terminal-shell').scroll_into_view_if_needed()
                path=Path(os.environ['FIRST_HANDS_PICTURES'])/f'nov15-polish-r3-completed-signature-{width}.png'
                assert not path.exists();page.screenshot(path=str(path))
            assert not posts
        finally:browser.close()

@pytest.mark.parametrize('width',[735,1440])
def test_filing_failure_is_explained_on_card_and_account_review(voice_world,monkeypatch,width):
    root,posts,_=voice_world;failed_filing(root,monkeypatch)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            audio=page.get_by_role('region',name='Trailer audio')
            card=audio.locator('.voice-spend')
            expect(card).to_contain_text('could not be filed')
            form=audio.locator('[data-account-review]')
            expect(form).to_contain_text('could not be filed')
            card.scroll_into_view_if_needed();expect(card.get_by_role('alert')).to_be_in_viewport()
            form.scroll_into_view_if_needed();expect(form.get_by_role('alert')).to_be_in_viewport()
            assert len(posts)==1
        finally:browser.close()

"""Measure what is actually visible after paid filing and signing settle."""
import os
import json
import shutil
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_voice_panel import voice_world, project, priced, http, options
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.signing_outcome_helpers import bounded_signers
from tests.backlot.test_first_hands_icon import icon_board

def picture(page,name):
    folder=os.environ.get('SETTLED_PICTURES')
    if folder:
        path=Path(folder)/(os.environ.get('SETTLED_PREFIX','')+name);assert not path.exists();page.screenshot(path=str(path))

def visible_bounds(page,locator):
    box=locator.bounding_box();bar=page.locator('.bar').bounding_box()
    assert box is not None
    print('SETTLED BOUNDS',box)
    assert box['y']>=bar['y']+bar['height'] and box['y']+box['height']<=900,box

@pytest.mark.parametrize('width',[735,1440])
def test_failed_paid_filing_reveals_live_explanation_and_review_button(voice_world,monkeypatch,bounded_signers,width,tmp_path):
    from backlot import voice_panel as voice
    root,posts,_=voice_world;voice.catalogue(root)
    record=voice.prepare_voice(root,options())['request']
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    with icon_board(tmp_path,failed_filing=True) as (projects,counter,start,stop),sync_playwright() as pw:
        live=projects/root.name;shutil.copytree(root,live);ready=start(1);url=ready['url'].rstrip('/')
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(f'{url}/p/{root.name}#k='+ready['token'])
            audio=page.get_by_role('region',name='Trailer audio')
            audio.get_by_role('button',name='Review and sign this request',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=20000)
            expect(page.locator('#gate-sign-panel')).to_contain_text('Your approval is recorded',timeout=20000)
            page.route('**/gate-sessions',lambda route:route.fulfill(json=[{'request_id':'invented-ended-session'}]))
            with page.expect_response('**/gate-sessions'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.get_by_role('button',name='Return to audio',exact=True).click()
            page.wait_for_timeout(6000)
            page.unroute('**/gate-sessions')
            held_reads=[]
            page.route('**/state',lambda route:held_reads.append(route))
            audio.get_by_role('button',name='Make this approved take',exact=True).click()
            expect(audio).to_contain_text('Audio arrived but could not be filed',timeout=20000)
            page.wait_for_timeout(800)
            card=page.locator(f'[data-audio-request="{record["request_id"]}"]')
            try:
                visible_bounds(page,card.get_by_role('alert'))
                visible_bounds(page,card.get_by_role('button',name='Review this held charge',exact=True))
            finally:
                page.unroute('**/state')
                for route in held_reads:
                    try:route.continue_()
                    except Exception:pass  # A superseded read may already be cancelled.
            expect(audio.locator(':scope > [role=status]')).to_contain_text('did not finish',timeout=30000)
            page.wait_for_timeout(800)
            card=page.locator(f'[data-audio-request="{record["request_id"]}"]')
            picture(page,f'nov15-polish-r4-{width}-filing-settled.png')
            visible_bounds(page,card.get_by_role('alert'))
            visible_bounds(page,card.get_by_role('button',name='Review this held charge',exact=True))
            assert [json.loads(x)['kind'] for x in counter.read_text().splitlines()]==['speech']
            from tests.backlot.signing_outcome_helpers import figures
            assert figures(live,1,fresh=True)==pytest.approx([0,record['max_credits_usd'],1-record['max_credits_usd']])
        finally:browser.close()

@pytest.mark.parametrize('width',[735,1440])
def test_signing_instruction_and_complete_question_are_visible(voice_world,monkeypatch,bounded_signers,width):
    from backlot import voice_panel as voice
    root,posts,_=voice_world;voice.catalogue(root);voice.prepare_voice(root,options())
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('region',name='Trailer audio').get_by_role('button',name='Review and sign this request',exact=True).click()
            page.get_by_role('button',name='Start signing',exact=True).click()
            approve=page.get_by_role('button',name='Approve and sign',exact=True)
            expect(approve).to_be_visible(timeout=20000)
            page.wait_for_timeout(800)
            picture(page,f'nov15-polish-r4-{width}-signing-question.png')
            visible_bounds(page,page.locator('#gate-sign-panel .sign-question'))
            visible_bounds(page,approve)
            if width==735:expect(page.locator('#viewer')).not_to_contain_text('panel on the right')
            assert not posts
        finally:browser.close()

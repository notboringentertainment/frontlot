"""Invented first-use faults, exercised with real signing and counted services."""
import time
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options,sign
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_followups_browser import open_page
from tests.backlot.signing_outcome_helpers import bounded_signers,figures

def failed_filing(root,monkeypatch):
    from backlot import voice_panel as voice
    from lib import audio_workspace
    from tools.cost_tracker import nonterminal_reservations
    voice.catalogue(root);record=voice.prepare_voice(root,options())['request']
    sign(root,'audio_spend',record,record['request_id'])
    original=audio_workspace.probe_audio
    def unavailable(*a,**k):raise FileNotFoundError('Invented local filing failure.')
    monkeypatch.setattr(audio_workspace,'probe_audio',unavailable)
    assert not voice.send(root,record['request_id'])['success']
    monkeypatch.setattr(audio_workspace,'probe_audio',original)
    reservation,=nonterminal_reservations(root)
    assert reservation['state']=='pending_billing'
    return reservation

def test_account_review_click_survives_focus_refresh(voice_world,monkeypatch,bounded_signers):
    root,posts,gets=voice_world;reservation=failed_filing(root,monkeypatch)
    monkeypatch.setenv('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            form=page.get_by_role('region',name='Trailer audio')
            form.get_by_label('The account was charged.',exact=True).check()
            form.get_by_label('Actual credit replacement dollars',exact=True).fill('0.0038')
            form.get_by_label('What I checked in the account',exact=True).fill('I checked the invented account; nineteen credits were charged.')
            button=form.get_by_role('button',name='Review and sign this account outcome',exact=True)
            button.scroll_into_view_if_needed();box=button.bounding_box()
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down()
            with page.expect_response(url+'/api/project/'+root.name+'/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(400);page.mouse.up()
            expect(page.get_by_role('button',name='Start signing',exact=True)).to_be_visible(timeout=7000)
            page.get_by_role('button',name='Start signing',exact=True).click()
            page.get_by_role('button',name='Approve and sign',exact=True).click(timeout=15000)
            deadline=time.monotonic()+10
            while figures(root,1)[1] and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert figures(root,1,fresh=True)==pytest.approx([.0038,0,.9962])
            assert len(posts)==1 and not page.errors
        finally:browser.close()

def test_refresh_keeps_viewer_focus_and_unsaved_words(voice_world,monkeypatch):
    root,_,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            notes=page.get_by_label('Pronunciation and delivery notes for line 1',exact=True)
            notes.fill('Keep these unfinished words.');notes.focus()
            page.evaluate('()=>{window.keptViewer=document.getElementById("viewer");window.keptField=document.activeElement;}')
            with page.expect_response(url+'/api/project/'+root.name+'/state'):
                page.evaluate("()=>window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(300)
            assert page.evaluate('()=>document.getElementById("viewer")===window.keptViewer')
            assert page.evaluate('()=>document.activeElement===window.keptField')
            expect(notes).to_have_value('Keep these unfinished words.')
        finally:browser.close()

def test_refused_writer_token_is_visible_across_the_page(voice_world,monkeypatch):
    from backlot import server
    root,posts,_=voice_world;original=server.create_app;apps=[]
    def create(*a,**k):app=original(*a,**k);apps.append(app);return app
    monkeypatch.setattr(server,'create_app',create)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);apps[0].state.capability_token='invented-rotated-token'
            page.get_by_role('button',name='Load account voices',exact=True).click()
            banner=page.get_by_role('alert').filter(has_text='Open Front Lot from its app icon')
            expect(banner).to_be_visible(timeout=7000)
            page.reload();expect(banner).to_be_visible(timeout=7000)
            assert not posts
        finally:browser.close()

def test_paper_seconds_required_words_and_open_unsigned_prices(voice_world,monkeypatch):
    root,_,_=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root)
            page.get_by_role('button',name='Paper cut workspace',exact=True).click()
            paper=page.get_by_role('region',name='Trailer paper cut')
            paper.get_by_role('button',name='Add shot',exact=True).click()
            shot=paper.get_by_role('group',name='Shot 2',exact=True)
            expect(shot.get_by_label('Purpose (private plan)',exact=True)).to_have_attribute('required','')
            shot.get_by_label('Screen time in seconds',exact=True).fill('5.25')
            expect(shot).to_contain_text('126 frames')
            expect(shot.get_by_label('Difficulty (optional planning note)',exact=True)).to_have_value('Not assessed')
            expect(page.get_by_role('region',name='Next step')).to_be_visible()
        finally:browser.close()

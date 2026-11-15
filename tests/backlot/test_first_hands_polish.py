"""Changed-read pointer races and safe, visible writer refusals."""
import pytest
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel import voice_world,project,priced,http,options,sign
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_followups_browser import open_page
from tests.backlot.test_first_hands import failed_filing


def test_every_audio_button_receives_its_release_during_changed_reads(voice_world,monkeypatch):
    root,posts,gets=voice_world;failed_filing(root,monkeypatch);before=len(posts)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);audio=page.get_by_role('region',name='Trailer audio')
            expect(audio.get_by_role('button',name='Review and sign this account outcome',exact=True)).to_be_visible()
            # Observe delivery without executing purchases. Separate journey
            # tests exercise the real callbacks and signed account review.
            page.evaluate("()=>document.addEventListener('click',e=>{if(e.target===window.pressed){window.delivered++;e.stopImmediatePropagation();e.preventDefault();}},true)")
            labels=audio.get_by_role('button').evaluate_all("es=>es.filter(e=>!e.disabled).map(e=>e.textContent)")
            for number,label in enumerate(dict.fromkeys(labels)):
                button=audio.get_by_role('button',name=label,exact=True).first
                if not button.is_visible():continue
                button.scroll_into_view_if_needed();button.evaluate('(e)=>{window.pressed=e;window.delivered=0;}');box=button.bounding_box()
                page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down()
                def changed(route):
                    data=route.fetch().json();data['record_warnings']=['Invented changed information '+str(number)];route.fulfill(json=data)
                page.route('**/voice',changed)
                with page.expect_response(url+'/api/project/'+root.name+'/voice'):
                    page.evaluate("id=>import('/ui/voice.js').then(m=>m.renderVoice({project_id:id},()=>{}))",root.name)
                page.wait_for_timeout(150);page.mouse.up()
                assert page.evaluate('window.delivered')==1,label+' lost its mouse release'
                page.unroute('**/voice',changed)
            assert len(posts)==before
        finally:browser.close()


def test_refused_save_says_copy_before_opening_the_icon(voice_world,monkeypatch):
    from backlot import server
    root,posts,_=voice_world;original=server.create_app;apps=[]
    def create(*a,**kw):app=original(*a,**kw);apps.append(app);return app
    monkeypatch.setattr(server,'create_app',create)
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=open_page(browser,url,root);paper=page.get_by_role('region',name='Trailer paper cut')
            paper.get_by_label('Hook',exact=True).fill('Keep my unsaved hook.')
            apps[0].state.capability_token='invented-rotated-token'
            button=paper.get_by_role('button',name='Save a new version',exact=True);button.click()
            note=button.locator('xpath=following-sibling::*[1]')
            expect(note).to_contain_text('Copy')
            expect(note).not_to_contain_text('Reload')
            expect(paper.get_by_label('Hook',exact=True)).to_have_value('Keep my unsaved hook.')
            assert not posts
        finally:browser.close()

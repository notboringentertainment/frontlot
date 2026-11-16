"""An older audio snapshot cannot replace the draft saved after it began."""
from playwright.sync_api import sync_playwright,expect
from tests.backlot.test_voice_panel_browser import board,short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced,http
from tests.backlot.test_followups_browser import open_page
from tests.backlot.test_signing_outcome_browser import wait_for


def test_refresh_started_before_a_save_cannot_erase_the_saved_music_draft(voice_world,monkeypatch):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);held=[]
        try:
            page=open_page(browser,url,root);panel=page.get_by_role('region',name='Trailer audio',exact=True)
            area=panel.get_by_label('Music brief',exact=True);expect(area).to_be_visible()
            def delay(route):
                response=route.fetch();value=response.json()
                if not held:value['record_warnings']=['An invented older snapshot.']
                held.append((route,value))
            page.route('**/voice',delay)
            page.evaluate('''project=>import('/ui/voice.js').then(m=>m.renderVoice({project_id:project},()=>{}))''',root.name)
            wait_for(page,lambda:bool(held))
            text='A quiet instrumental pulse with room for speech.'
            area.fill(text);panel.get_by_label('Music length in seconds',exact=True).fill('12')
            panel.get_by_role('button',name='Save music brief',exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Music brief saved')
            held[0][0].fulfill(json=held[0][1]);page.wait_for_timeout(200)
            expect(area).to_have_value(text)
            assert not posts and not page.errors
        finally:
            for route,_ in held[1:]:route.abort()
            browser.close()

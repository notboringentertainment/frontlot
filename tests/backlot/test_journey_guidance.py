"""The board must describe the work that is actually available now."""
import pytest
from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced, http


@pytest.mark.parametrize('case',['recent-activity','paper-playback','empty-sound','canonical-labels'])
def test_journey_guidance_matches_the_actual_screen(voice_world,monkeypatch,case):
    root,posts,gets=voice_world
    with board(root,monkeypatch) as url,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(url+'/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            if case=='recent-activity':
                expect(page.locator('header.bar .status')).to_have_text('Recently updated')
            elif case=='paper-playback':
                page.get_by_role('button',name='Paper cut workspace',exact=True).click()
                paper=page.get_by_role('region',name='Trailer paper cut',exact=True)
                expect(paper).to_contain_text('Play kept pictures and sound in Cutting workspace, then make the final file in Delivery workspace.')
                expect(paper).not_to_contain_text('Timed playback and export of real media are not built yet.')
            elif case=='empty-sound':
                page.get_by_role('button',name='Cutting workspace',exact=True).click()
                cut=page.get_by_role('region',name='Cutting view',exact=True)
                expect(cut).to_contain_text('No kept sound takes yet. Keep sound in Voice workspace, Music workspace or Sound effects workspace, then reopen this cut.')
                expect(cut).not_to_contain_text('Original take is no longer available.')
            else:
                expect(page.locator('.sp-slug').first).to_contain_text('Spoken line 1')
                expect(page.locator('.sp-slug').first).not_to_contain_text('LINES-1')
                expect(page.locator('.sc-slate .num').first).to_have_text('Shot 1')
            assert posts==[] and gets==[] and not errors
        finally:browser.close()

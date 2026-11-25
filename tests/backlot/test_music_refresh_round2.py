"""An audio read begun before Save cannot replace the saved brief afterward."""
from playwright.sync_api import sync_playwright, expect
from tests.backlot.test_voice_panel_browser import board, short_signing_folder
from tests.backlot.test_voice_panel import voice_world
from tests.tools.test_audio_governance import project
from tests.tools.test_audio_half_transport import priced, http, wav_bytes


def test_old_audio_read_cannot_undo_a_saved_music_brief(voice_world, monkeypatch):
    root, posts, gets = voice_world
    with board(root, monkeypatch) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(url + '/') else r.abort())
            page.goto(f'{url}/p/{root.name}#k=invented-browser-proof')
            page.get_by_role('button', name='Music workspace', exact=True).click()
            panel = page.get_by_role('region', name='Trailer audio')
            form = panel.locator('#music-compose')
            expect(form).to_be_visible(timeout=15000)
            endpoint = url + f'/api/project/{root.name}/voice'
            stale = page.request.get(endpoint).json()
            assert stale['music_brief'] is None
            held = []
            page.route(endpoint, lambda r: held.append(r))
            with page.expect_request(endpoint):
                page.evaluate('''async id => {
                    const module = await import('/ui/voice.js');
                    module.renderVoice({project_id: id}, () => {});
                }''', root.name)
            words = 'An invented quiet pulse with room for speech.'
            form.get_by_label('Music brief', exact=True).fill(words)
            form.get_by_label('Music length in seconds', exact=True).fill('12')
            form.get_by_role('button', name='Save music brief', exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Music brief saved')
            with page.expect_request(endpoint):
                held[0].fulfill(json=stale)
            # Keep the queued fresh response held, so it cannot hide a stale
            # answer replacing the exact words the writer has just saved.
            expect(form.get_by_label('Music brief', exact=True)).to_have_value(words)
            expect(form.get_by_label('Music length in seconds', exact=True)).to_have_value('12')
            for route in held[1:]:
                route.continue_()
            page.unroute(endpoint)
            form.get_by_role('button', name='Prepare music spending card', exact=True).click()
            expect(panel.locator('.voice-spend[data-kind="music"]')).to_contain_text(words)
            assert posts == [] and gets == [] and not errors
        finally:
            browser.close()
